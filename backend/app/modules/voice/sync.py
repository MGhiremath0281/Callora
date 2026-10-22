"""Status reconciliation between the voice provider and Callora.

The provider webhook is the fast path: Bolna pushes an execution event
whenever the status of a call changes. It is also the *only* path the
original implementation had, which is why a call could stay in
``scheduled`` forever: if the webhook is not delivered — the agent has no
``webhook_url``, the request is dropped, the payload is rejected, or the
event races the creation of the call record — nothing ever moved the call
forward again. A frozen ``scheduled`` call also holds the duplicate-call
guard and a slot of the business' concurrency budget, so one lost webhook
blocks that lead for good.

This module is the safety net. It polls the provider for the executions
Callora knows about and applies what the provider says, using the same
state machine the webhook uses.

Two rules shape it:

* **the provider is asked before anything is changed.** A call is only
  closed because time passed when the provider confirms that it is still
  sitting in a pre-dial state, and only when the documented timeout
  policy allows it;
* **a live conversation is never closed because time passed.** A call in
  ``initiated``, ``ringing`` or ``in_progress`` is polled, but only the
  provider can end it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    PRE_DIAL_STATUSES,
    RECONCILABLE_STATUSES,
    AuditAction,
    CallStatus,
    map_provider_status,
)
from app.modules.voice.errors import (
    ProviderConnectionError,
    ProviderError,
    ProviderExecutionNotFoundError,
    ProviderTimeoutError,
)
from app.modules.voice.lifecycle import SOURCE_SYNC, apply_status, queue_postcall
from app.modules.voice.models import Call, CallEvent
from app.modules.voice.providers.base import VoiceProvider
from app.modules.voice.providers.registry import build_provider

logger = get_logger("callora.voice.sync")

#: The call moved to the status the provider reported.
ACTION_APPLIED = "applied"
#: The provider reported the status the call already had.
ACTION_UNCHANGED = "unchanged"
#: The provider reported an older status than the one Callora holds.
ACTION_OUT_OF_ORDER = "out_of_order"
#: The provider reported a status Callora does not know.
ACTION_UNKNOWN_STATUS = "unknown_provider_status"
#: The provider could not be reached; the call was left untouched.
ACTION_PROVIDER_UNREACHABLE = "provider_unreachable"
#: The provider answered with an error; the call was left untouched.
ACTION_PROVIDER_ERROR = "provider_error"
#: The provider does not know the execution any more.
ACTION_EXECUTION_MISSING = "execution_missing"
#: The documented timeout policy closed the call.
ACTION_TIMEOUT = "timeout"
#: The call was never placed with the provider.
ACTION_NOT_PLACED = "not_placed"
#: The call is not due yet (a scheduled call before its time).
ACTION_NOT_DUE = "not_due"


@dataclass
class SyncOutcome:
    """What one reconciliation attempt did."""

    call_id: str
    action: str
    status: str
    previous_status: str = ""
    execution_id: Optional[str] = None
    provider_status: Optional[str] = None
    detail: str = ""
    job_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "execution_id": self.execution_id,
            "action": self.action,
            "previous_status": self.previous_status,
            "status": self.status,
            "provider_status": self.provider_status,
            "detail": self.detail,
            "job_id": self.job_id,
            **self.metadata,
        }


@dataclass
class SyncReport:
    """The result of one sweep over the calls that need a check."""

    checked: int = 0
    applied: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    outcomes: List[SyncOutcome] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "applied": self.applied,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise a datetime to UTC.

    SQLite returns naive datetimes even for timezone-aware columns, so
    every timestamp that comes out of the database is normalised before
    it is compared with "now".
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def due_at(call: Call) -> datetime:
    """When the call was supposed to happen.

    A scheduled call is due at its ``scheduled_at``; every other call is
    due from the moment the provider accepted it.
    """
    scheduled = as_utc(call.scheduled_at)
    if scheduled is not None and call.status in PRE_DIAL_STATUSES:
        return scheduled
    return as_utc(call.initiated_at) or as_utc(call.created_at) or utcnow()


def last_checked_at(call: Call) -> datetime:
    """When the provider was asked about this call the last time."""
    metadata = call.call_metadata or {}
    raw = metadata.get("last_status_check_at")
    if raw:
        try:
            parsed = as_utc(datetime.fromisoformat(str(raw)))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return parsed
    return as_utc(call.initiated_at) or as_utc(call.created_at) or utcnow()


def is_due(call: Call, settings: Settings, now: datetime) -> bool:
    """Whether the call is due for a check.

    A call is only polled once per ``voice_status_poll_interval_seconds``
    and never before it was supposed to happen.
    """
    if now < due_at(call):
        return False
    interval = timedelta(seconds=max(0, settings.voice_status_poll_interval_seconds))
    return now >= last_checked_at(call) + interval


def is_timed_out(call: Call, settings: Settings, now: datetime) -> bool:
    """Whether the call stayed in a pre-dial state for too long."""
    timeout = timedelta(seconds=max(0, settings.voice_scheduled_call_timeout_seconds))
    return now >= due_at(call) + timeout


def sync_call(
    session: Session,
    settings: Settings,
    call: Call,
    provider: VoiceProvider,
    now: Optional[datetime] = None,
) -> SyncOutcome:
    """Ask the provider for the status of one call and apply it.

    The call is only closed when the provider confirms it: either with a
    terminal status, or — for a call that never got past the dial queue —
    by still reporting it as pre-dial after the documented timeout.
    """
    now = now or utcnow()
    previous_status = call.status
    execution_id = call.provider_call_id

    if not execution_id:
        return SyncOutcome(
            call_id=call.id,
            action=ACTION_NOT_PLACED,
            status=call.status,
            previous_status=previous_status,
            detail="the call was never placed with the provider",
        )

    try:
        result = provider.get_call(execution_id)
    except ProviderExecutionNotFoundError as exc:
        # The provider answered and said the execution does not exist:
        # the call can never happen.
        return _close_missing_execution(
            session, settings, call, previous_status, execution_id, str(exc), now
        )
    except (ProviderConnectionError, ProviderTimeoutError) as exc:
        logger.warning(
            "call_status_sync_unreachable",
            extra={
                "call_id": call.id,
                "provider_call_id": execution_id,
                "execution_id": execution_id,
                "call_status": call.status,
                "error": str(exc),
            },
        )
        return SyncOutcome(
            call_id=call.id,
            action=ACTION_PROVIDER_UNREACHABLE,
            status=call.status,
            previous_status=previous_status,
            execution_id=execution_id,
            detail=f"the provider could not be reached: {exc}",
        )
    except ProviderError as exc:
        logger.warning(
            "call_status_sync_failed",
            extra={
                "call_id": call.id,
                "provider_call_id": execution_id,
                "execution_id": execution_id,
                "call_status": call.status,
                "error": str(exc),
            },
        )
        return SyncOutcome(
            call_id=call.id,
            action=ACTION_PROVIDER_ERROR,
            status=call.status,
            previous_status=previous_status,
            execution_id=execution_id,
            detail=f"the provider returned an error: {exc}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        # A provider double that raises something unexpected must not take
        # the whole sweep down: the call is left untouched and the sweep
        # continues with the next one.
        logger.exception(
            "call_status_sync_failed",
            extra={
                "call_id": call.id,
                "provider_call_id": execution_id,
                "execution_id": execution_id,
                "call_status": call.status,
                "error": str(exc),
            },
        )
        return SyncOutcome(
            call_id=call.id,
            action=ACTION_PROVIDER_ERROR,
            status=call.status,
            previous_status=previous_status,
            execution_id=execution_id,
            detail=f"the provider could not be read: {exc}",
        )

    raw_status = (result.raw or {}).get("status")
    # The adapter hands out the internal status, but a provider double
    # may hand out the raw one. Mapping is idempotent over the internal
    # vocabulary, so this is safe either way.
    internal_status = map_provider_status(result.status or "")
    update = apply_status(
        session,
        call,
        internal_status,
        result.raw,
        source=SOURCE_SYNC,
        raw_status=raw_status,
        now=now,
    )

    job_id = None
    if update.applied and update.changed:
        job_id = queue_postcall(session, settings, call)
        record_sync_event(session, call, raw_status, result.raw or {})

    if update.applied and update.changed:
        action = ACTION_APPLIED
    elif update.applied:
        # The provider agrees with what Callora already knows.
        action = ACTION_UNCHANGED
    elif update.reason_code == "unknown_provider_status":
        action = ACTION_UNKNOWN_STATUS
    elif update.reason_code == "out_of_order":
        action = ACTION_OUT_OF_ORDER
    else:
        action = ACTION_UNCHANGED

    outcome = SyncOutcome(
        call_id=call.id,
        action=action,
        status=call.status,
        previous_status=previous_status,
        execution_id=execution_id,
        provider_status=raw_status,
        detail=update.detail,
        job_id=job_id,
    )

    # The documented timeout policy: a call that never got past the dial
    # queue and that the provider still reports as pre-dial long after it
    # was due is closed as failed. A call that is talking to somebody is
    # never closed this way, and a status the provider did not report
    # clearly never closes anything either.
    if (
        # The provider confirms that the execution never got past the
        # dial queue.
        internal_status in PRE_DIAL_STATUSES
        # and Callora does not believe a conversation is running.
        and call.status in PRE_DIAL_STATUSES
        and is_timed_out(call, settings, now)
        and settings.voice_stuck_call_timeout_action == "fail"
    ):
        outcome = _apply_timeout_policy(
            session, settings, call, previous_status, execution_id, raw_status, now
        )

    _remember_check(session, call, now)
    logger.info(
        "call_status_synced",
        extra={
            "call_id": call.id,
            "provider_call_id": execution_id,
            "execution_id": execution_id,
            "provider_status": raw_status,
            "previous_status": previous_status,
            "call_status": call.status,
            "action": outcome.action,
        },
    )
    return outcome


def _close_missing_execution(
    session: Session,
    settings: Settings,
    call: Call,
    previous_status: str,
    execution_id: str,
    message: str,
    now: datetime,
) -> SyncOutcome:
    """Close a call the provider does not know any more."""
    repo.update_call(
        session,
        call,
        {
            "status": CallStatus.FAILED,
            "error_message": message,
            "ended_at": now,
        },
    )
    repo.add_audit_log(
        session,
        action=AuditAction.CALL_STATUS_SYNCED,
        business_id=call.business_id,
        actor=SOURCE_SYNC,
        entity_type="call",
        entity_id=call.id,
        reason_code="provider_execution_not_found",
        reason="the provider does not know this execution any more",
        metadata={
            "call_id": call.id,
            "provider_call_id": execution_id,
            "execution_id": execution_id,
            "previous_status": previous_status,
            "call_status": call.status,
        },
    )
    job_id = queue_postcall(session, settings, call)
    logger.warning(
        "call_execution_missing",
        extra={
            "call_id": call.id,
            "provider_call_id": execution_id,
            "execution_id": execution_id,
            "previous_status": previous_status,
            "call_status": call.status,
        },
    )
    return SyncOutcome(
        call_id=call.id,
        action=ACTION_EXECUTION_MISSING,
        status=call.status,
        previous_status=previous_status,
        execution_id=execution_id,
        detail="the provider does not know this execution any more",
        job_id=job_id,
    )


def _apply_timeout_policy(
    session: Session,
    settings: Settings,
    call: Call,
    previous_status: str,
    execution_id: Optional[str],
    provider_status: Optional[str],
    now: datetime,
) -> SyncOutcome:
    """Fail a call that never got past the dial queue.

    The provider has just confirmed that the execution is still sitting
    in a pre-dial state, long after the call was due. Nothing is talking
    on that line, so closing it does not cut a conversation short.
    """
    timeout = settings.voice_scheduled_call_timeout_seconds
    message = (
        f"the provider kept the call in '{provider_status or previous_status}' "
        f"for more than {timeout} seconds after it was due"
    )
    repo.update_call(
        session,
        call,
        {
            "status": CallStatus.FAILED,
            "error_message": message,
            "ended_at": now,
        },
    )
    repo.add_audit_log(
        session,
        action=AuditAction.CALL_TIMEOUT,
        business_id=call.business_id,
        actor=SOURCE_SYNC,
        entity_type="call",
        entity_id=call.id,
        reason_code="provider_timeout",
        reason=message,
        metadata={
            "call_id": call.id,
            "provider_call_id": execution_id,
            "execution_id": execution_id,
            "provider_status": provider_status,
            "previous_status": previous_status,
            "call_status": call.status,
            "timeout_seconds": timeout,
        },
    )
    job_id = queue_postcall(session, settings, call)
    logger.warning(
        "call_timeout_failed",
        extra={
            "call_id": call.id,
            "provider_call_id": execution_id,
            "execution_id": execution_id,
            "provider_status": provider_status,
            "previous_status": previous_status,
            "call_status": call.status,
            "timeout_seconds": timeout,
        },
    )
    return SyncOutcome(
        call_id=call.id,
        action=ACTION_TIMEOUT,
        status=call.status,
        previous_status=previous_status,
        execution_id=execution_id,
        provider_status=provider_status,
        detail=message,
        job_id=job_id,
    )


def _remember_check(session: Session, call: Call, now: datetime) -> None:
    """Remember when the provider was asked about this call."""
    metadata = dict(call.call_metadata or {})
    metadata["last_status_check_at"] = now.isoformat()
    repo.update_call(session, call, {"call_metadata": metadata})


def sweep(
    session: Session,
    settings: Settings,
    provider: Optional[VoiceProvider] = None,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
    business_id: Optional[str] = None,
) -> SyncReport:
    """Reconcile every call that is due for a check.

    The provider is polled once per call; the result is applied with the
    shared state machine. The caller owns the transaction.
    """
    now = now or utcnow()
    limit = limit or settings.voice_status_sync_max_calls_per_sweep
    owns_provider = provider is None
    provider = provider or build_provider(settings=settings)
    report = SyncReport()
    try:
        candidates = repo.find_calls_needing_sync(
            session,
            statuses=sorted(RECONCILABLE_STATUSES),
            before=now - timedelta(
                seconds=max(0, settings.voice_status_poll_interval_seconds)
            ),
            limit=max(limit * 3, limit),
            business_id=business_id,
        )
        for call in candidates:
            if len(report.outcomes) >= limit:
                break
            if not is_due(call, settings, now):
                report.skipped += 1
                continue
            outcome = sync_call(session, settings, call, provider, now=now)
            report.checked += 1
            report.outcomes.append(outcome)
            if outcome.action == ACTION_APPLIED:
                report.applied += 1
            elif outcome.action in (ACTION_UNCHANGED, ACTION_OUT_OF_ORDER):
                report.unchanged += 1
            elif outcome.action in (
                ACTION_PROVIDER_UNREACHABLE,
                ACTION_PROVIDER_ERROR,
            ):
                report.failed += 1
            else:
                report.skipped += 1
    finally:
        if owns_provider:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
    return report


def record_sync_event(
    session: Session,
    call: Call,
    provider_status: Optional[str],
    payload: Dict[str, Any],
) -> CallEvent:
    """Store the provider document a reconciliation was based on."""
    from app.modules.voice.providers.base import event_fingerprint

    key = event_fingerprint(
        call.provider,
        call.provider_call_id,
        f"sync:{provider_status}:{payload.get('updated_at') or payload.get('status')}",
        payload,
    )
    event = CallEvent(
        business_id=call.business_id,
        call_id=call.id,
        provider=call.provider,
        event_key=key,
        event_type="status.sync",
        provider_status=provider_status,
        provider_event_id=call.provider_call_id,
        payload=payload,
        processed=True,
    )
    stored, _created = repo.insert_call_event(session, event)
    return stored or event


__all__ = [
    "ACTION_APPLIED",
    "ACTION_EXECUTION_MISSING",
    "ACTION_NOT_DUE",
    "ACTION_NOT_PLACED",
    "ACTION_OUT_OF_ORDER",
    "ACTION_PROVIDER_ERROR",
    "ACTION_PROVIDER_UNREACHABLE",
    "ACTION_TIMEOUT",
    "ACTION_UNCHANGED",
    "ACTION_UNKNOWN_STATUS",
    "SyncOutcome",
    "SyncReport",
    "as_utc",
    "due_at",
    "is_due",
    "is_timed_out",
    "last_checked_at",
    "record_sync_event",
    "sweep",
    "sync_call",
]
