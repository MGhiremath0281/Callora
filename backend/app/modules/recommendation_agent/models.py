"""SQLAlchemy models for the Recommendation Agent.

All models follow Callora patterns: prefixed IDs, UTC timestamps, JSON fields
for flexibility, proper indexes, and business-scoped tenancy.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
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
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class RecommendationStatus:
    NEW = "new"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"


class RecommendationCategory:
    SCRIPT_OPTIMIZATION = "script_optimization"
    OBJECTION_HANDLING = "objection_handling"
    QUALIFICATION = "qualification"
    LEAD_RECOVERY = "lead_recovery"
    SALES_LEAKAGE = "sales_leakage"
    CONVERSATION_FLOW = "conversation_flow"
    TIMING = "timing"
    COMPLIANCE = "compliance"
    OTHER = "other"


class ScriptSuggestionStatus:
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    SUPERSEDED = "superseded"


class Recommendation(Base):
    """A prioritized, evidence-backed recommendation."""

    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_business_status", "business_id", "status"),
        Index("ix_recommendations_business_priority", "business_id", "priority_score"),
        Index("ix_recommendations_business_category", "business_id", "category"),
        Index("ix_recommendations_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("rec")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)  # main recommendation text
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)  # P1/P2/P3
    priority_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    # Scoring components
    impact_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    frequency_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    recoverability_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    urgency_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    evidence_strength_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_direction: Mapped[str] = mapped_column(Text)
    suggested_next_action: Mapped[str] = mapped_column(Text)
    relevant_funnel_stage: Mapped[Optional[str]] = mapped_column(String(64))
    relevant_script_section: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RecommendationStatus.NEW)
    source_type: Mapped[str] = mapped_column(String(64))  # analytics/llm/rules
    model_version: Mapped[Optional[str]] = mapped_column(String(64))
    extra_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    superseded_by: Mapped[Optional[str]] = mapped_column(String(32))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RecommendationEvidence(Base):
    """Evidence linking recommendations to calls, transcripts, and analytics."""

    __tablename__ = "recommendation_evidence"
    __table_args__ = (
        Index("ix_rec_evidence_recommendation", "recommendation_id"),
        Index("ix_rec_evidence_call", "call_id"),
        Index("ix_rec_evidence_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("eve"))
    recommendation_id: Mapped[str] = mapped_column(String(32), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[Optional[str]] = mapped_column(String(32))
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)  # call/transcript/analytics/objection
    finding_supported: Mapped[str] = mapped_column(Text)
    transcript_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    timestamp_in_call: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RecommendationAuditLog(Base):
    """Audit trail for recommendation lifecycle changes."""

    __tablename__ = "recommendation_audit_logs"
    __table_args__ = (
        Index("ix_rec_audit_recommendation", "recommendation_id"),
        Index("ix_rec_audit_business_created", "business_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("raud"))
    recommendation_id: Mapped[str] = mapped_column(String(32), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(32))
    to_status: Mapped[Optional[str]] = mapped_column(String(32))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    audit_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ScriptSuggestion(Base):
    """Script optimization suggestions."""

    __tablename__ = "script_suggestions"
    __table_args__ = (
        Index("ix_script_suggestions_business", "business_id"),
        Index("ix_script_suggestions_recommendation", "recommendation_id"),
        Index("ix_script_suggestions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("ssg"))
    recommendation_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("recommendations.id", ondelete="SET NULL"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    current_script: Mapped[Optional[str]] = mapped_column(Text)
    proposed_script: Mapped[Optional[str]] = mapped_column(Text)
    script_section: Mapped[Optional[str]] = mapped_column(String(128))
    before_text: Mapped[Optional[str]] = mapped_column(Text)
    after_text: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Text] = mapped_column(Text)
    reasoning: Mapped[Text] = mapped_column(Text)
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text)
    uncertainty: Mapped[Optional[str]] = mapped_column(Text)
    potential_downsides: Mapped[Optional[str]] = mapped_column(Text)
    supporting_call_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ScriptSuggestionStatus.PROPOSED)
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[Optional[str]] = mapped_column(String(64))
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extra_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ScriptVersion(Base):
    """Versioned scripts with approval history."""

    __tablename__ = "script_versions"
    __table_args__ = (
        Index("ix_script_versions_business", "business_id"),
        Index("ix_script_versions_active", "business_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("sver"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    script_content: Mapped[Text] = mapped_column(Text)
    agent_instructions: Mapped[Optional[Text]] = mapped_column(Text)
    qualification_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    change_summary: Mapped[Optional[str]] = mapped_column(Text)
    source_suggestion_id: Mapped[Optional[str]] = mapped_column(String(32))
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RecoveryRecommendation(Base):
    """Lead recovery recommendations."""

    __tablename__ = "recovery_recommendations"
    __table_args__ = (
        Index("ix_recovery_rec_business", "business_id"),
        Index("ix_recovery_rec_lead", "lead_id"),
        Index("ix_recovery_rec_status", "status"),
        Index("ix_recovery_rec_recommendation", "recommendation_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("rrec"))
    recommendation_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("recommendations.id", ondelete="SET NULL"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_id: Mapped[str] = mapped_column(String(32), nullable=False)
    recovery_reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_next_action: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_timing: Mapped[Optional[str]] = mapped_column(Text)
    supporting_call_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_lead_status: Mapped[Optional[str]] = mapped_column(String(64))
    current_crm_status: Mapped[Optional[str]] = mapped_column(String(64))
    consent_status: Mapped[Optional[str]] = mapped_column(String(32))
    dnd_status: Mapped[Optional[bool]] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text)
    compliance_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    extra_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RecommendationOutcome(Base):
    """Track recommendation effectiveness and outcomes."""

    __tablename__ = "recommendation_outcomes"
    __table_args__ = (
        Index("ix_rec_outcomes_recommendation", "recommendation_id"),
        Index("ix_rec_outcomes_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("rout"))
    recommendation_id: Mapped[str] = mapped_column(String(32), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    implemented: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    implementation_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    implemented_by: Mapped[Optional[str]] = mapped_column(String(64))
    script_version_id: Mapped[Optional[str]] = mapped_column(String(32))
    baseline_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    baseline_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    post_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    post_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    baseline_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    post_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    observed_change: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_causal_claim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending/insufficient_data/evaluated
    sample_size_baseline: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_size_post: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
