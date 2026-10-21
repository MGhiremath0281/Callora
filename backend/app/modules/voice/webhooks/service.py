"""Webhook intake.

Provider webhooks are the only way the system learns what happened during
a call, so this service has to be exactly right:

* **verify** the signature when the provider supports one;
* **deduplicate** — a retried delivery must not create a second record or
  a second background job;
* **tolerate reordering** — Bolna sends ``call-disconnected`` right before
  ``completed``; a late event may never move a call backwards;
* **persist before acknowledging** — the event row is committed with the
  call update, so a crash after the 200 response can never lose data;
* **stay fast** — the heavy post-call analysis is queued, not executed
  inline.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    STATUS_RANK,
    TERMINAL_STATUSES,
    AuditAction,
    JobType,
)
from app.modules.voice.errors import WebhookSignatureError
from app.modules.voice.jobs import queue
from app.modules.voice.models import CallEvent
from app.modules.voice.providers.base import VoiceProvider

logger = logging.getLogger("callora.voice.webhooks")

#: Result states returned to the provider.
RESULT_PROCESSED = "processed"
RESULT_DUPLICATE = "duplicate"
RESULT_UNMATCHED = "unmatched"
RESULT_IGNORED = "ignored_out_of_order"


@dataclass
class WebhookResult:
    """What the webhook endpoint reports back to the provider."""

    status: str
    call_id: Optional[str] = None
    event_id: Optional[str] = None
    call_status: Optional[str] = None
    job_id: Optional[str] = None
    signature_valid: Optional[bool] = None
    detail: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "status": self.status,
            "call_id": self.call_id,
            "event_id": self.event_id,
            "call_status": self.call_status,
            "job_id": self.job_id,
            "signature_valid": self.signature_valid,
        }
        if self.detail:
            payload["detail"] = self.detail
        payload.update(self.extra)
        return payload


class WebhookService:
    """Processes provider events."""

    def __init__(self, settings: Settings, session: Session, provider: VoiceProvider) -> None:
        self._settings = settings
        self._session = session
        self._provider = provider

    # -- public API --------------------------------------------------------

    def process_execution_event(
        self,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
        raw_body: bytes = b"",
    ) -> WebhookResult:
        """Handle a call-lifecycle / transcript / extracted-data event."""
        signature_valid = self._verify(headers, raw_body)
        event = self._provider.normalize_event(payload, headers)

        stored, created = repo.insert_call_event(
            self._session,
            CallEvent(
                business_id=None,
                provider=event.provider,
                event_key=event.event_key,
                event_type=event.event_type,
                provider_status=event.status,
                provider_event_id=event.execution_id,
                payload=event.payload,
                signature_valid=signature_valid,
            ),
        )
        if not created:
            repo.add_audit_log(
                self._session,
                action=AuditAction.WEBHOOK_DUPLICATE,
                business_id=(stored.business_id if stored else None),
                entity_type="call_event",
                entity_id=(stored.id if stored else None),
                reason_code="duplicate_event",
                reason="the provider re-delivered an event that was already stored",
                metadata={"event_key": event.event_key},
            )
            self._session.commit()
            return WebhookResult(
                status=RESULT_DUPLICATE,
                call_id=(stored.call_id if stored else None),
                event_id=(stored.id if stored else None),
                signature_valid=signature_valid,
                detail="the event was already processed",
            )

        call = None
        if event.execution_id:
            call = repo.get_call_by_provider_id(
                self._session, event.provider, event.execution_id
            )
        if call is None:
            stored.call_id = None
            stored.processed = False
            stored.processing_error = "unknown_call"
            repo.add_audit_log(
                self._session,
                action=AuditAction.WEBHOOK_RECEIVED,
                entity_type="call_event",
                entity_id=stored.id,
                reason_code="unknown_call",
                reason="the event did not match any known call",
                metadata={"event_key": event.event_key, "execution_id": event.execution_id},
            )
            self._session.commit()
            return WebhookResult(
                status=RESULT_UNMATCHED,
                event_id=stored.id,
                signature_valid=signature_valid,
                detail="no call matches this provider call id",
            )

        stored.business_id = call.business_id
        stored.call_id = call.id

        internal_status = self._provider.to_internal_status(event.status)
        applied = self._apply_status(call, internal_status, event.payload)

        stored.processed = applied["applied"]
        stored.processing_error = applied["error"]
        repo.add_audit_log(
            self._session,
            action=AuditAction.WEBHOOK_RECEIVED,
            business_id=call.business_id,
            entity_type="call_event",
            entity_id=stored.id,
            reason_code=applied.get("reason_code"),
            reason=(
                "the provider event was applied to the call"
                if applied["applied"]
                else "the provider event was ignored"
            ),
            metadata={
                "call_id": call.id,
                "provider_status": event.status,
                "call_status": call.status,
            },
        )

        job_id = None
        if applied["applied"] and call.status in TERMINAL_STATUSES:
            job, _created = queue.enqueue(
                self._session,
                JobType.POSTCALL_PROCESS,
                payload={
                    "call_id": call.id,
                    "business_id": call.business_id,
                    "status": call.status,
                },
                dedupe_key=queue.postcall_dedupe_key(call.id, call.status),
                max_attempts=self._settings.voice_job_max_attempts,
                business_id=call.business_id,
            )
            if job is not None:
                job_id = job.id

        self._session.commit()
        return WebhookResult(
            status=RESULT_PROCESSED if applied["applied"] else RESULT_IGNORED,
            call_id=call.id,
            event_id=stored.id,
            call_status=call.status,
            job_id=job_id,
            signature_valid=signature_valid,
            detail=applied.get("detail"),
        )

    def process_pre_transfer_event(
        self,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
        raw_body: bytes = b"",
    ) -> WebhookResult:
        """Handle the pre-call webhook Bolna fires before a warm transfer."""
        from app.modules.voice.handoff import HandoffService

        signature_valid = self._verify(headers, raw_body)
        event = self._provider.normalize_event(payload, headers)
        call = None
        if event.execution_id:
            call = repo.get_call_by_provider_id(
                self._session, event.provider, event.execution_id
            )
        if call is None:
            return WebhookResult(
                status=RESULT_UNMATCHED,
                signature_valid=signature_valid,
                detail="no call matches this provider call id",
            )
        service = HandoffService(self._settings, self._session)
        handoff = service.record_warm_transfer(
            call=call,
            reason=payload.get("transfer_reason") or "the caller asked for a human",
            summary=payload.get("conversation_summary") or "",
            transferred_to=payload.get("transferred_to"),
        )
        self._session.commit()
        return WebhookResult(
            status=RESULT_PROCESSED,
            call_id=call.id,
            signature_valid=signature_valid,
            extra={"handoff_id": handoff.id if handoff else None},
        )

    # -- internals ---------------------------------------------------------

    def _verify(
        self, headers: Optional[Mapping[str, str]], raw_body: bytes
    ) -> Optional[bool]:
        """Verify the webhook signature.

        ``None`` means the provider does not support signatures; ``False``
        means the signature was present but wrong — the event is rejected.
        """
        result = self._provider.verify_signature(headers, raw_body)
        if result is False:
            repo.add_audit_log(
                self._session,
                action=AuditAction.WEBHOOK_REJECTED,
                reason_code="invalid_signature",
                reason="the webhook signature could not be verified",
            )
            self._session.commit()
            raise WebhookSignatureError(
                "the webhook signature could not be verified"
            )
        return result

    def _apply_status(
        self, call, internal_status: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Move the call forward, never backwards."""
        current = call.status
        current_rank = STATUS_RANK.get(current, 0)
        new_rank = STATUS_RANK.get(internal_status, 0)

        values: Dict[str, Any] = {}
        if current in TERMINAL_STATUSES and internal_status != current:
            return {
                "applied": False,
                "error": "out_of_order",
                "reason_code": "out_of_order",
                "detail": (
                    f"the call is already '{current}'; the '{internal_status}' event "
                    "was ignored"
                ),
            }
        if new_rank < current_rank:
            return {
                "applied": False,
                "error": "out_of_order",
                "reason_code": "out_of_order",
                "detail": (
                    f"the '{internal_status}' event is older than the current status "
                    f"'{current}' and was ignored"
                ),
            }

        values["status"] = internal_status
        values["provider_payload"] = payload
        transcript = payload.get("transcript")
        if transcript:
            values["transcript"] = transcript
        summary = payload.get("summary")
        if summary:
            values["summary"] = summary
        duration = payload.get("conversation_duration")
        if duration is not None:
            try:
                values["duration_seconds"] = int(duration)
            except (TypeError, ValueError):
                pass
        cost = payload.get("total_cost")
        if cost is not None:
            values["cost"] = cost
        telephony = payload.get("telephony_data") or {}
        if isinstance(telephony, dict):
            recording = telephony.get("recording_url")
            if recording:
                values["recording_url"] = recording
            if telephony.get("provider_call_id") and not call.provider_call_id:
                values["provider_call_id"] = telephony["provider_call_id"]
        error_message = payload.get("error_message")
        if error_message:
            values["error_message"] = str(error_message)
        if internal_status in TERMINAL_STATUSES:
            values["ended_at"] = datetime.now(timezone.utc)

        repo.update_call(self._session, call, values)
        return {
            "applied": True,
            "error": None,
            "reason_code": None,
            "detail": f"the call status is now '{internal_status}'",
        }
