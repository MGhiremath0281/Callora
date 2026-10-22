"""The call lifecycle state machine.

Every path that can change the status of a call goes through
:func:`apply_status`: the provider webhook, the operator refresh and the
reconciliation sweep. One implementation means one set of rules:

* a call never moves backwards — the status ranking is monotonic;
* a terminal status is never overwritten;
* an **unknown** provider status never terminates a call. It carries no
  information about whether the call is over, so the call keeps its
  status and the reconciliation sweep resolves it by polling the
  provider;
* a terminal status ends the call (``ended_at``), releases the
  in-flight duplicate-call guard (a terminal status is not an in-flight
  status) and queues the post-call work.

The module deliberately knows nothing about Bolna: it works on the
internal status vocabulary, so a second provider only has to map its own
statuses onto it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    STATUS_RANK,
    TERMINAL_STATUSES,
    AuditAction,
    CallStatus,
    JobType,
)
from app.modules.voice.jobs import queue

logger = get_logger("callora.voice.lifecycle")

#: Where a status change came from. It is recorded in the audit trail.
SOURCE_WEBHOOK = "webhook"
SOURCE_REFRESH = "refresh"
SOURCE_SYNC = "sync"


@dataclass
class StatusUpdate:
    """The outcome of one attempt to move a call to another status."""

    applied: bool
    changed: bool
    status: str
    reason_code: Optional[str] = None
    detail: str = ""

    @property
    def error(self) -> Optional[str]:
        """The processing error of the event (mirrors the webhook result)."""
        return self.reason_code


def apply_status(
    session: Session,
    call,
    internal_status: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    source: str = SOURCE_WEBHOOK,
    raw_status: Optional[str] = None,
    now: Optional[datetime] = None,
) -> StatusUpdate:
    """Move a call to ``internal_status`` — forward only.

    ``payload`` is the raw provider document; it is stored on the call so
    an operator can always see what the provider actually said.
    """
    payload = payload or {}
    now = now or datetime.now(timezone.utc)
    current = call.status
    current_rank = STATUS_RANK.get(current, 0)
    new_rank = STATUS_RANK.get(internal_status, 0)

    # 1. A finished call is final. A late event never revives it.
    if current in TERMINAL_STATUSES:
        return StatusUpdate(
            applied=False,
            changed=False,
            status=current,
            reason_code="out_of_order",
            detail=(
                f"the call is already '{current}'; the '{internal_status}' event "
                "was ignored"
            ),
        )

    # 2. An unknown provider status carries no verdict about the call.
    #    The status is left alone and the call keeps whatever protection
    #    an in-flight status gives it.
    if internal_status == CallStatus.UNKNOWN:
        repo.update_call(session, call, {"provider_payload": payload})
        logger.warning(
            "call_status_unknown",
            extra={
                "call_id": call.id,
                "provider_call_id": call.provider_call_id,
                "execution_id": call.provider_call_id,
                "provider_status": raw_status,
                "call_status": current,
                "source": source,
            },
        )
        return StatusUpdate(
            applied=False,
            changed=False,
            status=current,
            reason_code="unknown_provider_status",
            detail=(
                f"the provider reported the unknown status '{raw_status}'; "
                "the call status was left unchanged"
            ),
        )

    # 3. Never backwards.
    if new_rank < current_rank:
        return StatusUpdate(
            applied=False,
            changed=False,
            status=current,
            reason_code="out_of_order",
            detail=(
                f"the '{internal_status}' event is older than the current status "
                f"'{current}' and was ignored"
            ),
        )

    values: Dict[str, Any] = {
        "status": internal_status,
        "provider_payload": payload,
    }
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
    if internal_status == CallStatus.IN_PROGRESS and call.connected_at is None:
        values["connected_at"] = now
    if internal_status in TERMINAL_STATUSES:
        values["ended_at"] = now

    changed = internal_status != current
    repo.update_call(session, call, values)

    if changed:
        repo.add_audit_log(
            session,
            action=AuditAction.CALL_STATUS_UPDATED,
            business_id=call.business_id,
            actor=source,
            entity_type="call",
            entity_id=call.id,
            reason_code=f"status_{internal_status}",
            reason=f"the call status changed from '{current}' to '{internal_status}'",
            metadata={
                "call_id": call.id,
                "provider_call_id": call.provider_call_id,
                "provider_status": raw_status,
                "previous_status": current,
                "call_status": internal_status,
                "source": source,
            },
        )
        logger.info(
            "call_status_updated",
            extra={
                "call_id": call.id,
                "provider_call_id": call.provider_call_id,
                "execution_id": call.provider_call_id,
                "provider_status": raw_status,
                "previous_status": current,
                "call_status": internal_status,
                "source": source,
            },
        )

    return StatusUpdate(
        applied=True,
        changed=changed,
        status=internal_status,
        reason_code=None,
        detail=f"the call status is now '{internal_status}'",
    )


def queue_postcall(
    session: Session, settings: Settings, call
) -> Optional[str]:
    """Queue the post-call analysis for a call that reached a terminal state.

    The job is deduplicated per call *and* status, so a retried webhook or
    a repeated reconciliation never produces a second analysis.
    """
    if call.status not in TERMINAL_STATUSES:
        return None
    job, _created = queue.enqueue(
        session,
        JobType.POSTCALL_PROCESS,
        payload={
            "call_id": call.id,
            "business_id": call.business_id,
            "status": call.status,
        },
        dedupe_key=queue.postcall_dedupe_key(call.id, call.status),
        max_attempts=settings.voice_job_max_attempts,
        business_id=call.business_id,
    )
    return job.id if job is not None else None


def release_in_flight_guard(call) -> bool:
    """Whether the call no longer holds the duplicate-call guard.

    A terminal status (and a call whose line has dropped) releases the
    guard; a genuinely active call keeps it.
    """
    from app.modules.voice.constants import IN_FLIGHT_STATUSES

    return call.status not in IN_FLIGHT_STATUSES


__all__ = [
    "SOURCE_REFRESH",
    "SOURCE_SYNC",
    "SOURCE_WEBHOOK",
    "StatusUpdate",
    "apply_status",
    "queue_postcall",
    "release_in_flight_guard",
]
