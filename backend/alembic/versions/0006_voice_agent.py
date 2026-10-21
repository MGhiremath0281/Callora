"""Outbound Voice Agent — business configuration, compliance, calls and jobs.

Revision ID: 0006_voice
Revises: 0005_outreach
Create Date: 2026-10-21

Adds the tables the outbound voice module persists to:

``businesses``            the voice configuration (policy) of one business;
``voice_lead_profiles``   voice-specific state of a canonical lead;
``consent_records``       consent evidence per lead and channel;
``dnd_entries``           the do-not-disturb registry (global + business);
``calls``                 one outbound call attempt and everything known
                          about it;
``call_events``           provider events received on the webhook endpoint
                          (deduplicated by a deterministic fingerprint);
``call_outcomes``         the structured post-call intelligence;
``handoffs``              warm transfers and callback requests;
``compliance_decisions``  the persisted result of every pre-call gate
                          evaluation;
``audit_logs``            the append-only audit trail;
``voice_jobs``            the durable background job queue.

The shared ``idempotency_keys`` table gains a nullable ``payload_hash``
column so a reused idempotency key with a *different* body can be
detected instead of replaying a stale response.

No existing table loses data; Module 1-5 keep their exact behaviour.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_voice"
down_revision: Union[str, None] = "0005_outreach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- business configuration -------------------------------------------
    op.create_table(
        "businesses",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("default_language", sa.String(length=16), nullable=False),
        sa.Column("supported_languages", sa.JSON(), nullable=False),
        sa.Column("calling_window_start", sa.Integer(), nullable=False),
        sa.Column("calling_window_end", sa.Integer(), nullable=False),
        sa.Column("calling_days", sa.JSON(), nullable=False),
        sa.Column("max_calls_per_lead_per_day", sa.Integer(), nullable=False),
        sa.Column("max_calls_per_business_per_day", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False),
        sa.Column("require_consent", sa.Boolean(), nullable=False),
        sa.Column("dnd_enabled", sa.Boolean(), nullable=False),
        sa.Column("bolna_agent_id", sa.String(length=128), nullable=True),
        sa.Column("bolna_from_number", sa.String(length=32), nullable=True),
        sa.Column("agent_instructions", sa.Text(), nullable=True),
        sa.Column("qualification_questions", sa.JSON(), nullable=False),
        sa.Column("warm_transfer_enabled", sa.Boolean(), nullable=False),
        sa.Column("warm_transfer_number", sa.String(length=32), nullable=True),
        sa.Column("handoff_target_number", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_businesses_active", "businesses", ["is_active"])

    # -- voice lead profiles ----------------------------------------------
    op.create_table(
        "voice_lead_profiles",
        sa.Column(
            "lead_id",
            sa.String(length=32),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("preferred_language", sa.String(length=16), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("do_not_call", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("call_attempts", sa.Integer(), nullable=False),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_voice_lead_profiles_business", "voice_lead_profiles", ["business_id"]
    )

    # -- consent ------------------------------------------------------------
    op.create_table(
        "consent_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "lead_id", "channel", name="uq_consent_business_lead_channel"
        ),
    )
    op.create_index("ix_consent_records_business_id", "consent_records", ["business_id"])
    op.create_index("ix_consent_records_lead_id", "consent_records", ["lead_id"])
    op.create_index(
        "ix_consent_records_business_status", "consent_records", ["business_id", "status"]
    )

    # -- do-not-disturb registry --------------------------------------------
    op.create_table(
        "dnd_entries",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope", "business_id", "phone", name="uq_dnd_scope_business_phone"
        ),
    )
    op.create_index("ix_dnd_entries_phone", "dnd_entries", ["phone"])

    # -- calls ----------------------------------------------------------------
    op.create_table(
        "calls",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_call_id", sa.String(length=128), nullable=True),
        sa.Column("provider_agent_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("blocked_reason", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("to_number", sa.String(length=32), nullable=True),
        sa.Column("from_number", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("recording_url", sa.String(length=500), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=False),
        sa.Column("call_metadata", sa.JSON(), nullable=False),
        sa.Column("compliance_decision_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_call_id", name="uq_calls_provider_execution"
        ),
    )
    op.create_index("ix_calls_business_id", "calls", ["business_id"])
    op.create_index("ix_calls_business_status", "calls", ["business_id", "status"])
    op.create_index("ix_calls_business_created", "calls", ["business_id", "created_at"])
    op.create_index("ix_calls_lead", "calls", ["lead_id"])
    op.create_index("ix_calls_provider_call_id", "calls", ["provider_call_id"])

    # -- provider events --------------------------------------------------------
    op.create_table(
        "call_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=True),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "event_key", name="uq_call_events_provider_key"
        ),
    )
    op.create_index("ix_call_events_business_id", "call_events", ["business_id"])
    op.create_index("ix_call_events_call", "call_events", ["call_id"])
    op.create_index("ix_call_events_received", "call_events", ["received_at"])

    # -- post-call outcomes -------------------------------------------------------
    op.create_table(
        "call_outcomes",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.String(length=32), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("qualified", sa.Boolean(), nullable=True),
        sa.Column("qualification_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("qualification_answers", sa.JSON(), nullable=False),
        sa.Column("refusal_category", sa.String(length=32), nullable=True),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("follow_up_actions", sa.JSON(), nullable=False),
        sa.Column("handoff_required", sa.Boolean(), nullable=False),
        sa.Column("handoff_briefing", sa.Text(), nullable=True),
        sa.Column("language_detected", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("call_id", name="uq_call_outcomes_call"),
    )
    op.create_index("ix_call_outcomes_business", "call_outcomes", ["business_id"])

    # -- handoffs -----------------------------------------------------------------
    op.create_table(
        "handoffs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("lead_name", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("requested_by", sa.String(length=16), nullable=False),
        sa.Column("target_number", sa.String(length=32), nullable=True),
        sa.Column("briefing", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_handoffs_business_status", "handoffs", ["business_id", "status"])
    op.create_index("ix_handoffs_call", "handoffs", ["call_id"])

    # -- compliance decisions ------------------------------------------------------
    op.create_table(
        "compliance_decisions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blocked_reasons", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_compliance_decisions_business_created",
        "compliance_decisions",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_compliance_decisions_lead", "compliance_decisions", ["lead_id"]
    )

    # -- audit trail ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("audit_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_business_id", "audit_logs", ["business_id"])
    op.create_index(
        "ix_audit_logs_business_created", "audit_logs", ["business_id", "created_at"]
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    # -- background job queue -------------------------------------------------------------
    op.create_table(
        "voice_jobs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_voice_jobs_dedupe_key"),
    )
    op.create_index("ix_voice_jobs_business_id", "voice_jobs", ["business_id"])
    op.create_index(
        "ix_voice_jobs_status_scheduled", "voice_jobs", ["status", "scheduled_at"]
    )
    op.create_index("ix_voice_jobs_type", "voice_jobs", ["type"])

    # -- shared idempotency table -------------------------------------------------------
    op.add_column(
        "idempotency_keys", sa.Column("payload_hash", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("idempotency_keys", "payload_hash")

    op.drop_index("ix_voice_jobs_type", table_name="voice_jobs")
    op.drop_index("ix_voice_jobs_status_scheduled", table_name="voice_jobs")
    op.drop_index("ix_voice_jobs_business_id", table_name="voice_jobs")
    op.drop_table("voice_jobs")

    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_business_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_business_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_compliance_decisions_lead", table_name="compliance_decisions")
    op.drop_index(
        "ix_compliance_decisions_business_created", table_name="compliance_decisions"
    )
    op.drop_table("compliance_decisions")

    op.drop_index("ix_handoffs_call", table_name="handoffs")
    op.drop_index("ix_handoffs_business_status", table_name="handoffs")
    op.drop_table("handoffs")

    op.drop_index("ix_call_outcomes_business", table_name="call_outcomes")
    op.drop_table("call_outcomes")

    op.drop_index("ix_call_events_received", table_name="call_events")
    op.drop_index("ix_call_events_call", table_name="call_events")
    op.drop_index("ix_call_events_business_id", table_name="call_events")
    op.drop_table("call_events")

    op.drop_index("ix_calls_provider_call_id", table_name="calls")
    op.drop_index("ix_calls_lead", table_name="calls")
    op.drop_index("ix_calls_business_created", table_name="calls")
    op.drop_index("ix_calls_business_status", table_name="calls")
    op.drop_index("ix_calls_business_id", table_name="calls")
    op.drop_table("calls")

    op.drop_index("ix_dnd_entries_phone", table_name="dnd_entries")
    op.drop_table("dnd_entries")

    op.drop_index("ix_consent_records_business_status", table_name="consent_records")
    op.drop_index("ix_consent_records_lead_id", table_name="consent_records")
    op.drop_index("ix_consent_records_business_id", table_name="consent_records")
    op.drop_table("consent_records")

    op.drop_index("ix_voice_lead_profiles_business", table_name="voice_lead_profiles")
    op.drop_table("voice_lead_profiles")

    op.drop_index("ix_businesses_active", table_name="businesses")
    op.drop_table("businesses")
