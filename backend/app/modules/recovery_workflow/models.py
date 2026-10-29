"""Recovery workflow models.

These tables are the execution layer that sits *downstream* of the
recovery-agent opportunity detection: eligibility decisions, workflow
runs/actions, follow-up tasks, appointments and reminders, conversation
analysis, versioned business rules, outcome/cost/revenue evidence and
integration sync state.

Every model is business-scoped, uses portable SQLAlchemy types (SQLite and
PostgreSQL) and stores no fabricated business numbers.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
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
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class EligibilityDecisionRecord(Base):
    """A persisted, auditable eligibility evaluation for one lead/channel."""

    __tablename__ = "workflow_eligibility_decisions"
    __table_args__ = (
        Index("ix_wf_elig_business_lead", "business_id", "lead_id"),
        Index("ix_wf_elig_business_created", "business_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("elig")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    checks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WorkflowRun(Base):
    """One recovery workflow execution.

    ``dry_run`` runs are fully evaluated and recorded but never place a
    call, send a message or book an appointment. ``status`` is the state
    machine: draft -> awaiting_approval -> approved -> running -> completed
    (or failed/cancelled/paused).
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "idempotency_key", name="uq_wf_runs_business_idem"
        ),
        Index("ix_wf_runs_business_status", "business_id", "status"),
        Index("ix_wf_runs_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("wfr")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(32), nullable=False, default="recovery_call")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128))
    input: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    current_step: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class WorkflowAction(Base):
    """An append-only record of one step performed by a workflow run."""

    __tablename__ = "workflow_actions"
    __table_args__ = (
        Index("ix_wf_actions_run", "run_id", "created_at"),
        Index("ix_wf_actions_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("wfa")
    )
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128))
    request: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FollowUpTask(Base):
    """A scheduled follow-up that still requires human approval to execute."""

    __tablename__ = "follow_up_tasks"
    __table_args__ = (
        Index("ix_followups_business_status", "business_id", "status"),
        Index("ix_followups_due", "business_id", "due_at"),
        Index("ix_followups_lead", "lead_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("fup")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_run_id: Mapped[Optional[str]] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="call")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    priority: Mapped[Optional[int]] = mapped_column(Integer)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    outcome: Mapped[Optional[str]] = mapped_column(String(64))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Appointment(Base):
    """A booking request and its honest provider status.

    ``provider='internal'`` is a Callora-owned record. External calendar
    providers are only marked ``confirmed`` after a provider response
    contains a real event id; otherwise the appointment stays ``requested``
    or ``failed`` with the provider error recorded.
    """

    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_business_start", "business_id", "scheduled_start"),
        Index("ix_appointments_case", "case_id"),
        Index("ix_appointments_lead", "lead_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("appt")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_run_id: Mapped[Optional[str]] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Recovery consultation")
    attendee_name: Mapped[Optional[str]] = mapped_column(String(255))
    attendee_phone: Mapped[Optional[str]] = mapped_column(String(32))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    meeting_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    confirmation: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Reminder(Base):
    """A reminder tied to an appointment.

    Delivery is only marked ``sent`` when a real channel adapter reports
    success. With no adapter configured the status is
    ``channel_not_configured`` — never a fabricated success.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_business_status", "business_id", "status"),
        Index("ix_reminders_due", "status", "remind_at"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("rem")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    appointment_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="sms")
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ConversationAnalysis(Base):
    """Persisted structured analysis of one call transcript."""

    __tablename__ = "conversation_analyses"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_conversation_analysis_call"),
        Index("ix_conversation_analysis_business", "business_id", "analyzed_at"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("ca")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    transcript_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    objections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    buying_signals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    leakage_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sentiment: Mapped[Optional[str]] = mapped_column(String(32))
    intent: Mapped[Optional[str]] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(64), nullable=False, default="rules")
    model: Mapped[Optional[str]] = mapped_column(String(128))
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class BusinessRule(Base):
    """A versioned, business-scoped workflow rule."""

    __tablename__ = "business_rules"
    __table_args__ = (
        UniqueConstraint("business_id", "key", name="uq_business_rules_key"),
        Index("ix_business_rules_business_active", "business_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("rule")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="workflow")
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class BusinessRuleVersion(Base):
    """Immutable version history for a business rule."""

    __tablename__ = "business_rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_business_rule_version"),
        Index("ix_business_rule_versions_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("rulev")
    )
    rule_id: Mapped[str] = mapped_column(String(32), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    change_summary: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OutcomeRecord(Base):
    """A recorded recovery outcome.

    ``verified`` is only true when a human/verified system supplied the
    outcome and evidence. Unverified outcomes are stored but never counted
    as recovered revenue.
    """

    __tablename__ = "outcome_records"
    __table_args__ = (
        Index("ix_outcomes_business_type", "business_id", "outcome_type"),
        Index("ix_outcomes_case", "case_id"),
        Index("ix_outcomes_lead", "lead_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("outc")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    workflow_run_id: Mapped[Optional[str]] = mapped_column(String(32))
    outcome_type: Mapped[str] = mapped_column(String(32), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_by: Mapped[Optional[str]] = mapped_column(String(64))
    verification_source: Mapped[Optional[str]] = mapped_column(String(100))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revenue_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recorded_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OutcomeEvidence(Base):
    """Evidence attached to one outcome record."""

    __tablename__ = "outcome_evidence"
    __table_args__ = (Index("ix_outcome_evidence_outcome", "outcome_id"),)

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("oev")
    )
    outcome_id: Mapped[str] = mapped_column(String(32), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(128))
    excerpt: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CostRecord(Base):
    """A real incurred cost (voice/LLM/message/infra)."""

    __tablename__ = "cost_records"
    __table_args__ = (
        Index("ix_costs_business_type", "business_id", "cost_type"),
        Index("ix_costs_business_incurred", "business_id", "incurred_at"),
        Index("ix_costs_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("cost")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    cost_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="manual")
    incurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RevenueRecord(Base):
    """Verified revenue linked to an outcome.

    Only rows with ``verified=True`` count toward verified revenue/ROI.
    """

    __tablename__ = "revenue_records"
    __table_args__ = (
        Index("ix_revenue_business_verified", "business_id", "verified"),
        Index("ix_revenue_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("rev")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    outcome_id: Mapped[Optional[str]] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="manual")
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class IntegrationConnection(Base):
    """A configured external integration (CRM, calendar, messaging...).

    ``config`` holds non-secret configuration only. Secrets are referenced
    by name and resolved by the deployment; they are never returned by the
    API.
    """

    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "provider", "name", name="uq_integration_business_provider_name"
        ),
        Index("ix_integrations_business_status", "business_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("intg")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="configured")
    config: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class IntegrationSyncState(Base):
    """Cursor and health of one integration synchronization direction."""

    __tablename__ = "integration_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "connection_id",
            "direction",
            name="uq_integration_sync_direction",
        ),
        Index("ix_integration_sync_business_status", "business_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("isync")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    connection_id: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="push")
    cursor: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
