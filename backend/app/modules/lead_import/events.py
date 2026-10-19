"""Reliable publication of ``lead.created`` events.

Events are written to a transactional outbox in the *same* database
transaction that commits the lead, so a committed lead always has a
corresponding event record. After commit, pending events are delivered:

* when ``CALLORA_EVENT_WEBHOOK_URL`` is configured, each event is POSTed
  to that URL;
* otherwise the outbox row itself is the delivery mechanism and
  downstream modules (2 and 3) read events from it.

Delivery is idempotent: consumers deduplicate on ``event.id``. Failed
deliveries stay pending with an attempt counter and are retried on the
next publish pass until ``event_publish_max_attempts`` is reached.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.models import EventStatus, OutboxEvent

logger = get_logger(__name__)

EVENT_LEAD_CREATED = "lead.created"


def enqueue_lead_created(
    session: Session, business_id: str, lead_id: str
) -> OutboxEvent:
    """Stage a ``lead.created`` event in the caller's transaction.

    The caller commits the lead and this event together; flush() surfaces
    constraint violations immediately so the whole transaction can roll
    back rather than committing a lead without its event.
    """
    event = OutboxEvent(
        event_type=EVENT_LEAD_CREATED,
        business_id=business_id,
        lead_id=lead_id,
        payload={"event": EVENT_LEAD_CREATED, "lead_id": lead_id, "business_id": business_id},
        status=EventStatus.PENDING,
    )
    session.add(event)
    try:
        session.flush()
    except IntegrityError:
        logger.error(
            "failed to stage lead.created event",
            extra={"lead_id": lead_id},
        )
        raise
    return event


def pending_events(session: Session, limit: int = 500) -> List[OutboxEvent]:
    return list(
        session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == EventStatus.PENDING)
            .order_by(OutboxEvent.created_at)
            .limit(limit)
        ).scalars()
    )


def _post_webhook(url: str, event: OutboxEvent, timeout: float = 5.0) -> None:
    import httpx

    response = httpx.post(url, json=event.payload, timeout=timeout)
    response.raise_for_status()


def publish_pending_events(session: Session, settings: Settings) -> dict:
    """Deliver pending events. Returns a small summary for logging/tests."""
    delivered = 0
    failed = 0
    skipped = 0
    for event in pending_events(session):
        if event.attempts >= settings.event_publish_max_attempts:
            event.status = EventStatus.FAILED
            skipped += 1
            continue
        event.attempts += 1
        if settings.event_webhook_url:
            try:
                _post_webhook(settings.event_webhook_url, event)
            except Exception as exc:  # noqa: BLE001 - delivery must never crash import
                event.last_error = str(exc)[:2000]
                logger.warning(
                    "event delivery failed",
                    extra={"event_id": event.id, "attempt": event.attempts},
                )
                failed += 1
                continue
        event.status = EventStatus.PUBLISHED
        event.published_at = datetime.now(timezone.utc)
        event.last_error = None
        delivered += 1
    session.flush()
    return {"delivered": delivered, "failed": failed, "exhausted": skipped}
