"""Appointment booking and reminders.

Booking is honest about what it can do:

* ``provider="internal"`` (the default, ``CALLORA_BOOKING_PROVIDER``)
  records a Callora-owned appointment. It is ``confirmed`` because the
  record itself *is* the booking.
* An external calendar provider is only ever ``confirmed`` after the
  provider answers with a real event id. No external adapter is
  implemented, so an external provider answers
  ``provider_not_configured`` and the appointment stays ``requested``
  with the error recorded — never a fabricated confirmation.

Reminders follow the same rule: with no delivery adapter configured
(``CALLORA_REMINDER_DELIVERY_PROVIDER=none``) a due reminder moves to
``channel_not_configured`` and reports it — it is never marked sent.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import InvalidParameterError, LeadNotFoundError
from app.modules.recovery_workflow.models import Appointment, Reminder
from app.modules.voice import repository as voice_repo

REMINDER_STATUSES = {
    "pending",
    "sent",
    "failed",
    "cancelled",
    "channel_not_configured",
}

DEFAULT_REMINDER_OFFSETS_HOURS = (24, 1)


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def book_appointment(
    session: Session,
    settings: Settings,
    *,
    business_id: str,
    lead_id: str,
    scheduled_start: Any,
    scheduled_end: Optional[Any] = None,
    title: str = "Recovery consultation",
    attendee_name: Optional[str] = None,
    attendee_phone: Optional[str] = None,
    location: Optional[str] = None,
    meeting_url: Optional[str] = None,
    notes: Optional[str] = None,
    timezone_name: str = "UTC",
    case_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    provider: Optional[str] = None,
) -> Appointment:
    """Record one appointment request with an honest provider status."""
    lead = voice_repo.get_lead(session, business_id, lead_id)
    if lead is None:
        raise LeadNotFoundError(lead_id)

    start = _coerce_datetime(scheduled_start)
    if start is None:
        raise InvalidParameterError(
            "scheduled_start must be an ISO-8601 datetime or unix seconds"
        )
    end = _coerce_datetime(scheduled_end) if scheduled_end else None
    if end is not None and end <= start:
        raise InvalidParameterError("scheduled_end must be after scheduled_start")

    provider_name = (provider or settings.booking_provider or "internal").strip().lower()
    appointment = Appointment(
        id=_prefixed_id("appt"),
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        workflow_run_id=workflow_run_id,
        provider=provider_name,
        status="requested",
        scheduled_start=start,
        scheduled_end=end,
        timezone=timezone_name,
        title=title,
        attendee_name=attendee_name or lead.name,
        attendee_phone=attendee_phone or lead.phone,
        location=location,
        meeting_url=meeting_url,
        notes=notes,
    )

    if provider_name == "internal":
        # The Callora-owned record is the booking itself. The internal
        # provider identifier makes that explicit in the audit trail.
        appointment.status = "confirmed"
        appointment.provider_event_id = None
        appointment.confirmation = {
            "provider": "internal",
            "confirmed_at": _utcnow().isoformat(),
            "note": "Callora-internal booking; no external calendar was contacted.",
        }
    else:
        # No external calendar adapter is implemented. The request is
        # recorded, the failure is recorded, and nothing is confirmed.
        appointment.status = "failed"
        appointment.error_message = (
            f"calendar provider {provider_name!r} is not configured; "
            "no external event was created"
        )
        appointment.confirmation = {
            "provider": provider_name,
            "reason_code": "provider_not_configured",
        }

    session.add(appointment)
    voice_repo.add_audit_log(
        session,
        action="appointment.booked",
        business_id=business_id,
        actor="workflow",
        entity_type="appointment",
        entity_id=appointment.id,
        decision=appointment.status,
        reason=(
            "internal booking confirmed"
            if appointment.status == "confirmed"
            else appointment.error_message
        ),
        metadata={
            "lead_id": lead_id,
            "provider": provider_name,
            "scheduled_start": start.isoformat(),
            "workflow_run_id": workflow_run_id,
        },
    )
    session.flush()
    return appointment


def cancel_appointment(
    session: Session, business_id: str, appointment: Appointment
) -> Appointment:
    if appointment.business_id != business_id:
        raise InvalidParameterError("appointment belongs to another business")
    appointment.status = "cancelled"
    appointment.updated_at = _utcnow()
    session.flush()
    return appointment


def list_appointments(
    session: Session,
    business_id: str,
    *,
    lead_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Appointment]:
    stmt = select(Appointment).where(Appointment.business_id == business_id)
    if lead_id:
        stmt = stmt.where(Appointment.lead_id == lead_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    stmt = stmt.order_by(Appointment.scheduled_start.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def get_appointment(
    session: Session, business_id: str, appointment_id: str
) -> Optional[Appointment]:
    return session.execute(
        select(Appointment).where(
            Appointment.business_id == business_id,
            Appointment.id == appointment_id,
        )
    ).scalars().first()


def schedule_reminders(
    session: Session,
    settings: Settings,
    appointment: Appointment,
    *,
    offsets_hours: Optional[List[int]] = None,
    channel: str = "sms",
) -> List[Reminder]:
    """Create the reminder rows for one confirmed appointment.

    Reminders are only scheduled for a confirmed appointment — a failed
    or cancelled booking never generates reminder noise.
    """
    if appointment.status != "confirmed":
        return []
    rule_offsets = offsets_hours
    if rule_offsets is None:
        from app.modules.recovery_workflow.rules import RuleEngine

        rule = RuleEngine(session, appointment.business_id).get_value(
            "reminders.enabled", {"enabled": True, "offsets_hours": list(DEFAULT_REMINDER_OFFSETS_HOURS)}
        )
        if isinstance(rule, dict) and not rule.get("enabled", True):
            return []
        raw = (rule or {}).get("offsets_hours") if isinstance(rule, dict) else None
        rule_offsets = [int(h) for h in (raw or DEFAULT_REMINDER_OFFSETS_HOURS)]

    reminders: List[Reminder] = []
    for hours in rule_offsets:
        remind_at = appointment.scheduled_start - timedelta(hours=hours)
        reminder = Reminder(
            id=_prefixed_id("rem"),
            business_id=appointment.business_id,
            appointment_id=appointment.id,
            channel=channel,
            remind_at=remind_at,
            status="pending",
            detail={"offset_hours": hours, "lead_id": appointment.lead_id},
        )
        session.add(reminder)
        reminders.append(reminder)
    session.flush()
    return reminders


def dispatch_due_reminders(
    session: Session, settings: Settings, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Attempt the due reminders and report the honest per-channel result.

    With no delivery adapter configured every due reminder becomes
    ``channel_not_configured`` with the error recorded. A reminder is
    only ``sent`` when a real adapter reports success.
    """
    now = now or _utcnow()
    provider = (settings.reminder_delivery_provider or "none").strip().lower()
    due = list(
        session.execute(
            select(Reminder).where(
                Reminder.status == "pending",
                Reminder.remind_at <= now,
            )
        ).scalars().all()
    )
    sent = failed = not_configured = 0
    for reminder in due:
        reminder.attempts = int(reminder.attempts or 0) + 1
        if provider in ("", "none"):
            reminder.status = "channel_not_configured"
            reminder.error_message = (
                "no reminder delivery adapter is configured "
                f"(CALLORA_REMINDER_DELIVERY_PROVIDER={provider or 'none'}); "
                "the reminder was not sent"
            )
            not_configured += 1
        else:
            # No other adapter is implemented; a configured-but-unknown
            # provider is an error, never a silent success.
            reminder.status = "failed"
            reminder.error_message = (
                f"reminder delivery provider {provider!r} is not implemented"
            )
            failed += 1
        reminder.updated_at = _utcnow()
    session.flush()
    return {
        "due": len(due),
        "sent": sent,
        "failed": failed,
        "channel_not_configured": not_configured,
        "provider": provider,
    }


def list_reminders(
    session: Session,
    business_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Reminder]:
    stmt = select(Reminder).where(Reminder.business_id == business_id)
    if status:
        stmt = stmt.where(Reminder.status == status)
    stmt = stmt.order_by(Reminder.remind_at.asc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
