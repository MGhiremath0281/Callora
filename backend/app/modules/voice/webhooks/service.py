"""Webhook intake.

Provider webhooks are the fast path: the provider pushes an event
whenever the status of a call changes. They are not the only path — the
reconciliation sweep (``voice/sync.py``) polls the provider for the
calls that never reported — so this service has to be exactly right:

* **verify** the signature when the provider supports one;
* **deduplicate** — a retried delivery must not create a second record or
  a second background job;
* **tolerate reordering** — Bolna sends ``call-disconnected`` right before
  ``completed``; a late event may never move a call backwards;
* **never terminate a call on an unknown status** — a status Callora has
  never seen says nothing about whether the call is over;
* **persist before acknowledging** — the event row is committed with the
  call update, so a crash after the 200 response can never lose data;
* **stay fast** — the heavy post-call analysis is queued, not executed
  inline.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    TERMINAL_STATUSES,
    AuditAction,
)
from app.modules.voice.errors import WebhookSignatureError
from app.modules.voice.lifecycle import (
    SOURCE_WEBHOOK,
    apply_status,
    queue_postcall,
)
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
            logger.info(
                "webhook_execution_duplicate",
                extra={
                    "call_id": stored.call_id if stored else None,
                    "provider_call_id": event.execution_id,
                    "execution_id": event.execution_id,
                    "provider_status": event.status,
                    "event_id": stored.id if stored else None,
                },
            )
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
            logger.warning(
                "webhook_execution_unmatched",
                extra={
                    "call_id": None,
                    "provider_call_id": event.execution_id,
                    "execution_id": event.execution_id,
                    "provider_status": event.status,
                    "event_id": stored.id,
                },
            )
            return WebhookResult(
                status=RESULT_UNMATCHED,
                event_id=stored.id,
                signature_valid=signature_valid,
                detail="no call matches this provider call id",
            )

        stored.business_id = call.business_id
        stored.call_id = call.id

        internal_status = self._provider.to_internal_status(event.status)
        update = apply_status(
            self._session,
            call,
            internal_status,
            event.payload,
            source=SOURCE_WEBHOOK,
            raw_status=event.status,
        )
        applied = {
            "applied": update.applied,
            "error": update.reason_code,
            "reason_code": update.reason_code,
            "detail": update.detail,
        }

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
                "provider_call_id": call.provider_call_id,
                "execution_id": call.provider_call_id,
                "provider_status": event.status,
                "call_status": call.status,
            },
        )

        job_id = None
        if applied["applied"] and call.status in TERMINAL_STATUSES:
            job_id = queue_postcall(self._session, self._settings, call)

        self._session.commit()
        logger.info(
            "webhook_execution_processed",
            extra={
                "call_id": call.id,
                "provider_call_id": call.provider_call_id,
                "execution_id": call.provider_call_id,
                "provider_status": event.status,
                "call_status": call.status,
                "applied": applied["applied"],
                "reason_code": applied.get("reason_code"),
                "event_id": stored.id,
            },
        )
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

    def process_followup_event(
        self,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
        raw_body: bytes = b"",
    ) -> Dict[str, Any]:
        """Handle a mid-call follow-up question / tool webhook from Bolna."""
        from app.modules.voice.knowledge.service import FollowUpService
        from app.modules.voice.models import Call
        from app.modules.voice.schemas import FollowUpQuestionRequest

        signature_valid = self._verify(headers, raw_body)
        data = dict(payload)

        # 1. Resolve execution ID and/or call ID
        nested_param = data.get("param") if isinstance(data.get("param"), dict) else {}
        nested_body = data.get("body") if isinstance(data.get("body"), dict) else {}
        nested_data = data.get("data") if isinstance(data.get("data"), dict) else {}

        execution_id = (
            data.get("execution_id")
            or data.get("id")
            or data.get("executionId")
            or nested_param.get("execution_id")
            or nested_param.get("id")
            or nested_body.get("execution_id")
            or nested_body.get("id")
            or nested_data.get("execution_id")
            or nested_data.get("id")
        )
        call_id = (
            data.get("call_id")
            or nested_param.get("call_id")
            or nested_body.get("call_id")
            or nested_data.get("call_id")
        )

        call = None
        if execution_id:
            call = repo.get_call_by_provider_id(
                self._session, self._provider.name, str(execution_id)
            )
            if call is None:
                call = self._session.query(Call).filter(Call.provider_call_id == str(execution_id)).first()

        if call is None and call_id:
            call = self._session.query(Call).filter(Call.id == str(call_id)).first()

        # Fallback to business_id's most recent active call if available
        if call is None:
            biz_id = data.get("business_id") or nested_param.get("business_id")
            if biz_id:
                call = (
                    self._session.query(Call)
                    .filter(Call.business_id == str(biz_id))
                    .order_by(Call.created_at.desc())
                    .first()
                )

        # 2. Extract question
        question = (
            data.get("question")
            or data.get("user_query")
            or data.get("query")
            or data.get("text")
            or data.get("message")
            or data.get("input")
            or nested_param.get("question")
            or nested_param.get("query")
            or nested_body.get("question")
            or nested_body.get("query")
            or (data.get("args") if isinstance(data.get("args"), dict) else {}).get("question")
            or (data.get("parameters") if isinstance(data.get("parameters"), dict) else {}).get("question")
        )

        if not question or not str(question).strip():
            msg = "Could you please specify your question?"
            return {
                "status": RESULT_PROCESSED,
                "response": msg,
                "answer": msg,
                "text": msg,
                "signature_valid": signature_valid,
            }

        question_str = str(question).strip()

        if call is None:
            fallback = (
                "I don't have that specific information right in front of me, but our team can help you. "
                "Would you like me to connect you with our specialist?"
            )
            return {
                "status": RESULT_UNMATCHED,
                "call_id": None,
                "signature_valid": signature_valid,
                "response": fallback,
                "answer": fallback,
                "text": fallback,
                "should_handoff": True,
                "handoff_recommended": True,
                "detail": "no active call matches this provider call id",
            }

        # 3. Extract language & context
        language = (
            data.get("language")
            or nested_param.get("language")
            or call.language
            or "en"
        )
        context = data.get("context") or nested_param.get("context") or {}

        # 4. Invoke FollowUpService
        service = FollowUpService(self._settings, self._session)
        request_obj = FollowUpQuestionRequest(
            business_id=call.business_id,
            call_id=call.id,
            execution_id=call.provider_call_id or (str(execution_id) if execution_id else None),
            question=question_str,
            language=str(language),
            context=context if isinstance(context, dict) else {},
        )
        resp = service.answer_question(request_obj)

        return {
            "status": RESULT_PROCESSED,
            "call_id": call.id,
            "execution_id": call.provider_call_id or (str(execution_id) if execution_id else None),
            "signature_valid": signature_valid,
            "response": resp.answer,
            "answer": resp.answer,
            "text": resp.answer,
            "source_references": resp.source_references,
            "should_handoff": resp.should_handoff,
            "handoff_recommended": resp.handoff_recommended,
            "handoff_type": resp.handoff_type,
            "handoff_id": resp.handoff_id,
            "action": resp.action,
            "confidence": resp.confidence,
            "qualification_continuation": resp.qualification_continuation,
            "memory_state": resp.memory_state,
        }

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
