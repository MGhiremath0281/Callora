"""SQLAlchemy models for the outbound voice agent module.

The module reuses the canonical ``leads`` table (Module 1) so there is a
single source of truth for a lead, and adds the voice-specific state in a
companion profile table. Everything else — businesses, consent, DND, calls,
provider events, outcomes, handoffs, compliance decisions, the audit trail
and the background job queue — is defined here.

All columns use portable types so the same models run on PostgreSQL
(production) and SQLite (tests / local development).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def prefixed_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Business configuration
# ---------------------------------------------------------------------------


class Business(Base):
    """Per-business voice configuration.

    ``id`` is the same business identifier the API-key layer resolves, so a
    business created here is immediately usable with the caller's
    ``X-API-Key``.
    """

    __tablename__ = "businesses"
    __table_args__ = (Index("ix_businesses_active", "is_active"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # IANA timezone used to evaluate the permitted calling window.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    default_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    supported_languages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Permitted calling window in *local* (business timezone) hours.
    calling_window_start: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    calling_window_end: Mapped[int] = mapped_column(Integer, nullable=False, default=19)
    # Weekdays the business may be called on (0=Monday .. 6=Sunday).
    calling_days: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_calls_per_lead_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_calls_per_business_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    require_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dnd_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bolna agent that runs the conversation for this business.
    bolna_agent_id: Mapped[Optional[str]] = mapped_column(String(128))
    bolna_from_number: Mapped[Optional[str]] = mapped_column(String(32))
    # Free-form business instructions injected into the agent prompt.
    agent_instructions: Mapped[Optional[str]] = mapped_column(Text)
    # Business-defined qualification questions, e.g.
    # [{"key": "budget", "question": "What is your budget?", "type": "text"}]
    qualification_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Human handoff configuration.
    warm_transfer_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warm_transfer_number: Mapped[Optional[str]] = mapped_column(String(32))
    handoff_target_number: Mapped[Optional[str]] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class VoiceLeadProfile(Base):
    """Voice-specific state for a lead from the canonical ``leads`` table."""

    __tablename__ = "voice_lead_profiles"
    __table_args__ = (Index("ix_voice_lead_profiles_business", "business_id"),)

    lead_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    preferred_language: Mapped[Optional[str]] = mapped_column(String(16))
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    # Per-lead do-not-call switch, independent of the DND registry.
    do_not_call: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    call_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_call_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ConsentRecord(Base):
    """Consent evidence for one lead and one channel."""

    __tablename__ = "consent_records"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "lead_id", "channel", name="uq_consent_business_lead_channel"
        ),
        Index("ix_consent_records_business_status", "business_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("cons"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="granted")
    source: Mapped[Optional[str]] = mapped_column(String(100))
    granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DndEntry(Base):
    """Do-not-disturb registry entry.

    ``scope='global'`` entries apply to every business (for example a
    number registered on a national registry); ``scope='business'`` entries
    only suppress calls for the owning business.
    """

    __tablename__ = "dnd_entries"
    __table_args__ = (
        UniqueConstraint("scope", "business_id", "phone", name="uq_dnd_scope_business_phone"),
        Index("ix_dnd_entries_phone", "phone"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("dnd"))
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    source: Mapped[Optional[str]] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


class Call(Base):
    """One outbound call attempt and everything known about it."""

    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("provider", "provider_call_id", name="uq_calls_provider_execution"),
        Index("ix_calls_business_status", "business_id", "status"),
        Index("ix_calls_business_created", "business_id", "created_at"),
        Index("ix_calls_lead", "lead_id"),
        Index("ix_calls_provider_call_id", "provider_call_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("call"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="bolna")
    # Provider execution id (Bolna ``execution_id``). NULL until the
    # provider accepted the call.
    provider_call_id: Mapped[Optional[str]] = mapped_column(String(128))
    provider_agent_id: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    # Why the call was blocked (compliance) or why it failed.
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(255))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    to_number: Mapped[Optional[str]] = mapped_column(String(32))
    from_number: Mapped[Optional[str]] = mapped_column(String(32))
    language: Mapped[Optional[str]] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="outbound")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    initiated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    recording_url: Mapped[Optional[str]] = mapped_column(String(500))
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    # Last provider payload (transcript, extracted data, telephony data...).
    provider_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Call-scoped metadata (request context, dry-run marker, ...).
    # Named ``call_metadata`` because ``metadata`` is reserved by the
    # SQLAlchemy declarative API.
    call_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "call_metadata", JSON, nullable=False, default=dict
    )
    compliance_decision_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CallEvent(Base):
    """A provider event received on the webhook endpoint.

    ``event_key`` is a deterministic fingerprint of the event. The unique
    constraint on ``(provider, event_key)`` is what makes webhook handling
    idempotent: a retried delivery collides and is discarded.
    """

    __tablename__ = "call_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_call_events_provider_key"),
        Index("ix_call_events_call", "call_id"),
        Index("ix_call_events_received", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("evt"))
    business_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="bolna")
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="execution.status")
    provider_status: Mapped[Optional[str]] = mapped_column(String(32))
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(128))
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    signature_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_error: Mapped[Optional[str]] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CallOutcome(Base):
    """Structured post-call intelligence produced by the LangGraph workflow."""

    __tablename__ = "call_outcomes"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_call_outcomes_call"),
        Index("ix_call_outcomes_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("out"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    qualified: Mapped[Optional[bool]] = mapped_column(Boolean)
    qualification_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    qualification_answers: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    refusal_category: Mapped[Optional[str]] = mapped_column(String(32))
    refusal_reason: Mapped[Optional[str]] = mapped_column(Text)
    follow_up_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    handoff_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handoff_briefing: Mapped[Optional[str]] = mapped_column(Text)
    language_detected: Mapped[Optional[str]] = mapped_column(String(16))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(64), nullable=False, default="langgraph")
    raw_response: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Handoff(Base):
    """A human-handoff request: live warm transfer or callback."""

    __tablename__ = "handoffs"
    __table_args__ = (
        Index("ix_handoffs_business_status", "business_id", "status"),
        Index("ix_handoffs_call", "call_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("ho"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_name: Mapped[Optional[str]] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="callback")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    reason_code: Mapped[Optional[str]] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(16), nullable=False, default="ai")
    target_number: Mapped[Optional[str]] = mapped_column(String(32))
    briefing: Mapped[Optional[str]] = mapped_column(Text)
    context: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    outcome: Mapped[Optional[str]] = mapped_column(String(64))
    outcome_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Compliance + audit
# ---------------------------------------------------------------------------


class ComplianceDecision(Base):
    """The persisted result of one pre-call compliance evaluation."""

    __tablename__ = "compliance_decisions"
    __table_args__ = (
        Index("ix_compliance_decisions_business_created", "business_id", "created_at"),
        Index("ix_compliance_decisions_lead", "lead_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("cmp")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    checks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    blocked_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditLog(Base):
    """Append-only audit trail for compliance decisions and call outcomes."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_business_created", "business_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("aud"))
    business_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    decision: Mapped[Optional[str]] = mapped_column(String(16))
    reason_code: Mapped[Optional[str]] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    audit_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "audit_metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class Job(Base):
    """Durable background job.

    The queue lives in PostgreSQL so it survives restarts, supports
    retries with backoff and is safe to process concurrently (a claim is an
    atomic conditional update).
    """

    __tablename__ = "voice_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_voice_jobs_dedupe_key"),
        Index("ix_voice_jobs_status_scheduled", "status", "scheduled_at"),
        Index("ix_voice_jobs_type", "type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("job"))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    business_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # Idempotency key: a second job with the same key is never created.
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(128))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
