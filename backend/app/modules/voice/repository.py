"""Data access for the outbound voice module.

Every SQL statement of the module lives here, so routers and services stay
free of ORM details. All functions take an explicit ``Session``; the caller
owns the transaction.

Time-window helpers (``day_bounds``) translate a business-local calendar
day into absolute UTC bounds, which keeps the quota queries portable across
PostgreSQL and SQLite.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey, Lead
from app.modules.voice.constants import (
    ATTEMPTED_STATUSES,
    IN_FLIGHT_STATUSES,
    TERMINAL_STATUSES,
)
from app.modules.voice.models import (
    AuditLog,
    Business,
    Call,
    CallEvent,
    CallOutcome,
    ComplianceDecision,
    ConsentRecord,
    DndEntry,
    Handoff,
    VoiceLeadProfile,
    prefixed_id,
    utcnow,
)

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def resolve_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone, falling back to UTC when unknown."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def day_bounds(reference: datetime, tz_name: str) -> Tuple[datetime, datetime]:
    """Return the [start, end) UTC bounds of the local day of ``reference``."""
    tz = resolve_timezone(tz_name)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    local = reference.astimezone(tz)
    start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Business
# ---------------------------------------------------------------------------


def get_business(session: Session, business_id: str) -> Optional[Business]:
    return session.get(Business, business_id)


def list_businesses(session: Session, limit: int = 100, offset: int = 0) -> List[Business]:
    stmt = select(Business).order_by(Business.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def create_business(session: Session, business: Business) -> Business:
    session.add(business)
    session.flush()
    return business


def update_business(session: Session, business: Business, values: Dict[str, Any]) -> Business:
    for key, value in values.items():
        setattr(business, key, value)
    business.updated_at = now_utc()
    session.flush()
    return business


def get_or_create_business(
    session: Session,
    business_id: str,
    name: Optional[str] = None,
    timezone: str = "Asia/Kolkata",
    default_language: str = "en",
    supported_languages: Optional[List[str]] = None,
    calling_window_start: int = 10,
    calling_window_end: int = 19,
    max_calls_per_lead_per_day: int = 1,
    max_calls_per_business_per_day: int = 500,
    max_concurrent_calls: int = 5,
) -> Business:
    """Fetch the voice configuration of a business, creating it if needed.

    The API-key layer resolves a ``business_id`` from configuration, so a
    business may be addressable before it has a voice configuration row.
    Creating the row on first use keeps the two identifiers in sync
    without a separate provisioning step.
    """
    business = get_business(session, business_id)
    if business is not None:
        return business
    business = Business(
        id=business_id,
        name=name or business_id,
        timezone=timezone,
        default_language=default_language,
        supported_languages=list(supported_languages or []),
        calling_window_start=calling_window_start,
        calling_window_end=calling_window_end,
        max_calls_per_lead_per_day=max_calls_per_lead_per_day,
        max_calls_per_business_per_day=max_calls_per_business_per_day,
        max_concurrent_calls=max_concurrent_calls,
    )
    session.add(business)
    session.flush()
    return business


# ---------------------------------------------------------------------------
# Idempotency (shared with the other Callora modules)
# ---------------------------------------------------------------------------


def get_idempotency_record(
    session: Session, business_id: str, scope: str, key: str
) -> Optional[IdempotencyKey]:
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.business_id == business_id,
        IdempotencyKey.scope == scope,
        IdempotencyKey.key == key,
    )
    return session.execute(stmt).scalars().first()


def store_idempotency_record(
    session: Session,
    business_id: str,
    scope: str,
    key: str,
    response_ref: Optional[str] = None,
    payload_hash: Optional[str] = None,
) -> IdempotencyKey:
    """Persist the result of an idempotent request."""
    record = get_idempotency_record(session, business_id, scope, key)
    if record is None:
        record = IdempotencyKey(
            business_id=business_id,
            scope=scope,
            key=key,
            response_ref=response_ref,
            payload_hash=payload_hash,
        )
        session.add(record)
    else:
        record.response_ref = response_ref
        record.payload_hash = payload_hash
    session.flush()
    return record


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


def get_lead(session: Session, business_id: str, lead_id: str) -> Optional[Lead]:
    """Fetch a lead scoped to its business (404-safe for other tenants)."""
    stmt = select(Lead).where(Lead.id == lead_id, Lead.business_id == business_id)
    return session.execute(stmt).scalars().first()


def get_lead_by_phone(session: Session, business_id: str, phone: str) -> Optional[Lead]:
    stmt = select(Lead).where(Lead.business_id == business_id, Lead.phone == phone)
    return session.execute(stmt).scalars().first()


def list_leads(
    session: Session,
    business_id: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Lead]:
    stmt = (
        select(Lead)
        .where(Lead.business_id == business_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars().all())


def get_lead_profile(session: Session, business_id: str, lead_id: str) -> Optional[VoiceLeadProfile]:
    stmt = select(VoiceLeadProfile).where(
        VoiceLeadProfile.lead_id == lead_id,
        VoiceLeadProfile.business_id == business_id,
    )
    return session.execute(stmt).scalars().first()


def upsert_lead_profile(
    session: Session,
    business_id: str,
    lead_id: str,
    values: Dict[str, Any],
) -> VoiceLeadProfile:
    profile = get_lead_profile(session, business_id, lead_id)
    if profile is None:
        profile = VoiceLeadProfile(business_id=business_id, lead_id=lead_id)
        session.add(profile)
    for key, value in values.items():
        setattr(profile, key, value)
    profile.updated_at = now_utc()
    session.flush()
    return profile


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def get_consent(
    session: Session, business_id: str, lead_id: str, channel: str = "voice"
) -> Optional[ConsentRecord]:
    stmt = select(ConsentRecord).where(
        ConsentRecord.business_id == business_id,
        ConsentRecord.lead_id == lead_id,
        ConsentRecord.channel == channel,
    )
    return session.execute(stmt).scalars().first()


def list_consents(session: Session, business_id: str, lead_id: str) -> List[ConsentRecord]:
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.business_id == business_id, ConsentRecord.lead_id == lead_id)
        .order_by(ConsentRecord.updated_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def record_consent(
    session: Session,
    business_id: str,
    lead_id: str,
    channel: str,
    status: str,
    phone: Optional[str] = None,
    source: Optional[str] = None,
    granted_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    revoked_at: Optional[datetime] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> ConsentRecord:
    """Upsert the consent record for (business, lead, channel)."""
    record = get_consent(session, business_id, lead_id, channel)
    if record is None:
        record = ConsentRecord(
            id=prefixed_id("cons"),
            business_id=business_id,
            lead_id=lead_id,
            channel=channel,
        )
        session.add(record)
    record.phone = phone
    record.status = status
    record.source = source
    record.granted_at = granted_at
    record.expires_at = expires_at
    record.revoked_at = revoked_at
    record.evidence = evidence or {}
    record.updated_at = now_utc()
    session.flush()
    return record


# ---------------------------------------------------------------------------
# DND registry
# ---------------------------------------------------------------------------


def list_dnd_entries(
    session: Session, business_id: str, include_global: bool = True
) -> List[DndEntry]:
    stmt = select(DndEntry)
    if include_global:
        stmt = stmt.where(
            (DndEntry.scope == "global") | (DndEntry.business_id == business_id)
        )
    else:
        stmt = stmt.where(DndEntry.business_id == business_id)
    stmt = stmt.order_by(DndEntry.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def find_dnd_entry(
    session: Session, business_id: str, phone: str
) -> Optional[DndEntry]:
    """Return the active DND entry for a phone (global or business scoped)."""
    stmt = select(DndEntry).where(
        DndEntry.phone == phone,
        DndEntry.active.is_(True),
    ).where((DndEntry.scope == "global") | (DndEntry.business_id == business_id))
    return session.execute(stmt).scalars().first()


def add_dnd_entry(
    session: Session,
    business_id: str,
    phone: str,
    scope: str = "business",
    reason: Optional[str] = None,
    source: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> DndEntry:
    entry = DndEntry(
        id=prefixed_id("dnd"),
        scope=scope,
        business_id="" if scope == "global" else business_id,
        phone=phone,
        reason=reason,
        source=source,
        expires_at=expires_at,
    )
    session.add(entry)
    session.flush()
    return entry


def deactivate_dnd_entry(session: Session, entry: DndEntry) -> DndEntry:
    entry.active = False
    entry.updated_at = now_utc()
    session.flush()
    return entry


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


def create_call(session: Session, call: Call) -> Call:
    session.add(call)
    session.flush()
    return call


def get_call(session: Session, business_id: str, call_id: str) -> Optional[Call]:
    stmt = select(Call).where(Call.id == call_id, Call.business_id == business_id)
    return session.execute(stmt).scalars().first()


def get_call_by_provider_id(
    session: Session, provider: str, provider_call_id: str
) -> Optional[Call]:
    stmt = select(Call).where(
        Call.provider == provider, Call.provider_call_id == provider_call_id
    )
    return session.execute(stmt).scalars().first()


def get_call_any_business(session: Session, call_id: str) -> Optional[Call]:
    return session.get(Call, call_id)


def list_calls(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
    lead_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Call]:
    stmt = select(Call).where(Call.business_id == business_id)
    if status:
        stmt = stmt.where(Call.status == status)
    if lead_id:
        stmt = stmt.where(Call.lead_id == lead_id)
    stmt = stmt.order_by(Call.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def count_calls(
    session: Session,
    business_id: str,
    statuses: Optional[List[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    lead_id: Optional[str] = None,
) -> int:
    stmt = select(func.count()).select_from(Call).where(Call.business_id == business_id)
    if statuses:
        stmt = stmt.where(Call.status.in_(statuses))
    if start is not None:
        stmt = stmt.where(Call.created_at >= start)
    if end is not None:
        stmt = stmt.where(Call.created_at < end)
    if lead_id:
        stmt = stmt.where(Call.lead_id == lead_id)
    return int(session.execute(stmt).scalar_one())


def count_in_flight_calls(session: Session, business_id: str) -> int:
    return count_calls(session, business_id, statuses=list(IN_FLIGHT_STATUSES))


def count_attempts_today(
    session: Session, business_id: str, tz_name: str, lead_id: Optional[str] = None
) -> int:
    start, end = day_bounds(now_utc(), tz_name)
    return count_calls(
        session,
        business_id,
        statuses=list(ATTEMPTED_STATUSES),
        start=start,
        end=end,
        lead_id=lead_id,
    )


def find_recent_call_for_lead(
    session: Session,
    business_id: str,
    lead_id: str,
    since: datetime,
) -> Optional[Call]:
    """Most recent *attempted* call for a lead at or after ``since``."""
    stmt = (
        select(Call)
        .where(
            Call.business_id == business_id,
            Call.lead_id == lead_id,
            Call.created_at >= since,
            Call.status.in_(list(ATTEMPTED_STATUSES) + list(IN_FLIGHT_STATUSES)),
        )
        .order_by(Call.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def update_call(session: Session, call: Call, values: Dict[str, Any]) -> Call:
    for key, value in values.items():
        setattr(call, key, value)
    call.updated_at = now_utc()
    session.flush()
    return call


# ---------------------------------------------------------------------------
# Call events (webhook intake)
# ---------------------------------------------------------------------------


def insert_call_event(
    session: Session, event: CallEvent
) -> Tuple[Optional[CallEvent], bool]:
    """Insert a provider event.

    Returns ``(event, created)``. ``created`` is ``False`` when the event
    was already stored — that is the duplicate-delivery path and the caller
    must not re-apply it.

    The insert runs inside a savepoint so a duplicate delivery does not
    abort the surrounding transaction.
    """
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
        return event, True
    except IntegrityError:
        existing = session.execute(
            select(CallEvent).where(
                CallEvent.provider == event.provider,
                CallEvent.event_key == event.event_key,
            )
        ).scalars().first()
        return existing, False


def list_call_events(
    session: Session, business_id: str, call_id: str, limit: int = 100, offset: int = 0
) -> List[CallEvent]:
    stmt = (
        select(CallEvent)
        .where(CallEvent.business_id == business_id, CallEvent.call_id == call_id)
        .order_by(CallEvent.received_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def get_outcome(session: Session, call_id: str) -> Optional[CallOutcome]:
    stmt = select(CallOutcome).where(CallOutcome.call_id == call_id)
    return session.execute(stmt).scalars().first()


def upsert_outcome(session: Session, outcome: CallOutcome) -> CallOutcome:
    """Insert or update the outcome of a call (one row per call)."""
    existing = get_outcome(session, outcome.call_id)
    if existing is None:
        session.add(outcome)
        session.flush()
        return outcome
    for key, value in {
        "summary": outcome.summary,
        "disposition": outcome.disposition,
        "qualified": outcome.qualified,
        "qualification_score": outcome.qualification_score,
        "qualification_answers": outcome.qualification_answers,
        "refusal_category": outcome.refusal_category,
        "refusal_reason": outcome.refusal_reason,
        "follow_up_actions": outcome.follow_up_actions,
        "handoff_required": outcome.handoff_required,
        "handoff_briefing": outcome.handoff_briefing,
        "language_detected": outcome.language_detected,
        "model": outcome.model,
        "engine": outcome.engine,
        "raw_response": outcome.raw_response,
        "processed_at": outcome.processed_at or now_utc(),
    }.items():
        setattr(existing, key, value)
    existing.updated_at = now_utc()
    session.flush()
    return existing


# ---------------------------------------------------------------------------
# Handoffs
# ---------------------------------------------------------------------------


def create_handoff(session: Session, handoff: Handoff) -> Handoff:
    session.add(handoff)
    session.flush()
    return handoff


def get_handoff(session: Session, business_id: str, handoff_id: str) -> Optional[Handoff]:
    stmt = select(Handoff).where(Handoff.id == handoff_id, Handoff.business_id == business_id)
    return session.execute(stmt).scalars().first()


def list_handoffs(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Handoff]:
    stmt = select(Handoff).where(Handoff.business_id == business_id)
    if status:
        stmt = stmt.where(Handoff.status == status)
    if kind:
        stmt = stmt.where(Handoff.kind == kind)
    stmt = stmt.order_by(Handoff.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def update_handoff(session: Session, handoff: Handoff, values: Dict[str, Any]) -> Handoff:
    for key, value in values.items():
        setattr(handoff, key, value)
    handoff.updated_at = now_utc()
    session.flush()
    return handoff


# ---------------------------------------------------------------------------
# Compliance decisions + audit trail
# ---------------------------------------------------------------------------


def create_compliance_decision(
    session: Session, decision: ComplianceDecision
) -> ComplianceDecision:
    session.add(decision)
    session.flush()
    return decision


def list_compliance_decisions(
    session: Session,
    business_id: str,
    lead_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ComplianceDecision]:
    stmt = select(ComplianceDecision).where(ComplianceDecision.business_id == business_id)
    if lead_id:
        stmt = stmt.where(ComplianceDecision.lead_id == lead_id)
    stmt = stmt.order_by(ComplianceDecision.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def add_audit_log(
    session: Session,
    action: str,
    business_id: Optional[str] = None,
    actor: str = "system",
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    decision: Optional[str] = None,
    reason_code: Optional[str] = None,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    entry = AuditLog(
        id=prefixed_id("aud"),
        business_id=business_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        decision=decision,
        reason_code=reason_code,
        reason=reason,
        audit_metadata=metadata or {},
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_logs(
    session: Session,
    business_id: str,
    action: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.business_id == business_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Aggregate counters (used by the operations endpoints)
# ---------------------------------------------------------------------------


def count_calls_by_status(session: Session, business_id: str) -> Dict[str, int]:
    stmt = (
        select(Call.status, func.count())
        .where(Call.business_id == business_id)
        .group_by(Call.status)
    )
    return {str(status): int(count) for status, count in session.execute(stmt).all()}


def count_consent_by_status(session: Session, business_id: str) -> Dict[str, int]:
    stmt = (
        select(ConsentRecord.status, func.count())
        .where(ConsentRecord.business_id == business_id)
        .group_by(ConsentRecord.status)
    )
    return {str(status): int(count) for status, count in session.execute(stmt).all()}


def count_dnd_entries(session: Session, business_id: str, active_only: bool = True) -> int:
    stmt = select(func.count()).select_from(DndEntry).where(
        (DndEntry.scope == "global") | (DndEntry.business_id == business_id)
    )
    if active_only:
        stmt = stmt.where(DndEntry.active.is_(True))
    return int(session.execute(stmt).scalar_one())


def count_handoffs(
    session: Session, business_id: str, statuses: Optional[List[str]] = None
) -> int:
    stmt = select(func.count()).select_from(Handoff).where(Handoff.business_id == business_id)
    if statuses:
        stmt = stmt.where(Handoff.status.in_(statuses))
    return int(session.execute(stmt).scalar_one())


def sum_call_cost(session: Session, business_id: str) -> float:
    stmt = select(func.coalesce(func.sum(Call.cost), 0)).where(Call.business_id == business_id)
    return float(session.execute(stmt).scalar_one() or 0.0)
