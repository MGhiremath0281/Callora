"""Recovery workflow execution layer + voice attribution/audit columns.

Revision ID: 0009_recovery_workflow
Revises: 0008_voice_opportunities
Create Date: 2026-10-28

Adds:

* ``calls.campaign_id`` / ``calls.script_version_id``  — attribution so
  analytics can compare conversations made under different script
  versions (both nullable; older rows simply carry no attribution).
* ``call_outcomes.field_confidence`` / ``evidence`` — per-field
  confidence and the evidence the post-call workflow relied on. Empty
  means "not produced", never "certain".
* ``audit_logs.previous_hash`` / ``entry_hash`` — the tamper-evident
  audit hash chain. Rows written before the chain existed carry no hash
  and are reported as legacy, never invalid.
* The fifteen ``recovery_workflow`` tables: eligibility decisions,
  workflow runs, append-only workflow actions, follow-up tasks,
  appointments, reminders, conversation analyses, versioned business
  rules (+ versions), outcome records (+ evidence), cost records,
  revenue records, integration connections and integration sync state.

All workflow tables are business-scoped; nothing here places a call,
sends a message or books an external event by itself.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_recovery_workflow"
down_revision: Union[str, None] = "0008_voice_opportunities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- voice attribution + audit hash chain (additive, nullable) ------------
    with op.batch_alter_table("calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("campaign_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("script_version_id", sa.String(length=32), nullable=True)
        )

    with op.batch_alter_table("call_outcomes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("field_confidence", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}")
        )

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("previous_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=True))

    # -- workflow eligibility decisions ---------------------------------------
    op.create_table(
        "workflow_eligibility_decisions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workflow_eligibility_decisions_business_id",
        "workflow_eligibility_decisions",
        ["business_id"],
    )
    op.create_index(
        "ix_wf_elig_business_lead",
        "workflow_eligibility_decisions",
        ["business_id", "lead_id"],
    )
    op.create_index(
        "ix_wf_elig_business_created",
        "workflow_eligibility_decisions",
        ["business_id", "created_at"],
    )

    # -- workflow runs ---------------------------------------------------------
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "idempotency_key", name="uq_wf_runs_business_idem"
        ),
    )
    op.create_index(
        "ix_workflow_runs_business_id", "workflow_runs", ["business_id"]
    )
    op.create_index(
        "ix_wf_runs_business_status", "workflow_runs", ["business_id", "status"]
    )
    op.create_index("ix_wf_runs_case", "workflow_runs", ["case_id"])

    # -- workflow actions (append-only) ----------------------------------------
    op.create_table(
        "workflow_actions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_wf_actions_run", "workflow_actions", ["run_id", "created_at"]
    )
    op.create_index(
        "ix_wf_actions_business", "workflow_actions", ["business_id"]
    )

    # -- follow-up tasks --------------------------------------------------------
    op.create_table(
        "follow_up_tasks",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("assigned_to", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_follow_up_tasks_business_id", "follow_up_tasks", ["business_id"]
    )
    op.create_index(
        "ix_followups_business_status", "follow_up_tasks", ["business_id", "status"]
    )
    op.create_index(
        "ix_followups_due", "follow_up_tasks", ["business_id", "due_at"]
    )
    op.create_index("ix_followups_lead", "follow_up_tasks", ["lead_id"])

    # -- appointments ------------------------------------------------------------
    op.create_table(
        "appointments",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("attendee_name", sa.String(length=255), nullable=True),
        sa.Column("attendee_phone", sa.String(length=32), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("meeting_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("confirmation", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_appointments_business_id", "appointments", ["business_id"]
    )
    op.create_index(
        "ix_appointments_business_start",
        "appointments",
        ["business_id", "scheduled_start"],
    )
    op.create_index("ix_appointments_case", "appointments", ["case_id"])
    op.create_index("ix_appointments_lead", "appointments", ["lead_id"])

    # -- reminders ----------------------------------------------------------------
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("appointment_id", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reminders_business_id", "reminders", ["business_id"]
    )
    op.create_index(
        "ix_reminders_business_status", "reminders", ["business_id", "status"]
    )
    op.create_index("ix_reminders_due", "reminders", ["status", "remind_at"])

    # -- conversation analyses -----------------------------------------------------
    op.create_table(
        "conversation_analyses",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.String(length=32), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("transcript_hash", sa.String(length=64), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("objections", sa.JSON(), nullable=False),
        sa.Column("buying_signals", sa.JSON(), nullable=False),
        sa.Column("leakage_flags", sa.JSON(), nullable=False),
        sa.Column("sentiment", sa.String(length=32), nullable=True),
        sa.Column("intent", sa.String(length=64), nullable=True),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("call_id", name="uq_conversation_analysis_call"),
    )
    op.create_index(
        "ix_conversation_analyses_business_id",
        "conversation_analyses",
        ["business_id"],
    )
    op.create_index(
        "ix_conversation_analysis_business",
        "conversation_analyses",
        ["business_id", "analyzed_at"],
    )

    # -- versioned business rules ---------------------------------------------------
    op.create_table(
        "business_rules",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "key", name="uq_business_rules_key"),
    )
    op.create_index(
        "ix_business_rules_business_id", "business_rules", ["business_id"]
    )
    op.create_index(
        "ix_business_rules_business_active",
        "business_rules",
        ["business_id", "is_active"],
    )

    op.create_table(
        "business_rule_versions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rule_id", "version", name="uq_business_rule_version"),
    )
    op.create_index(
        "ix_business_rule_versions_business",
        "business_rule_versions",
        ["business_id"],
    )

    # -- outcome records (+ evidence) -------------------------------------------------
    op.create_table(
        "outcome_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=32), nullable=True),
        sa.Column("outcome_type", sa.String(length=32), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("verified_by", sa.String(length=64), nullable=True),
        sa.Column("verification_source", sa.String(length=100), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outcome_records_business_id", "outcome_records", ["business_id"]
    )
    op.create_index(
        "ix_outcomes_business_type", "outcome_records", ["business_id", "outcome_type"]
    )
    op.create_index("ix_outcomes_case", "outcome_records", ["case_id"])
    op.create_index("ix_outcomes_lead", "outcome_records", ["lead_id"])

    op.create_table(
        "outcome_evidence",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("outcome_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outcome_evidence_outcome", "outcome_evidence", ["outcome_id"]
    )

    # -- costs and revenue --------------------------------------------------------------
    op.create_table(
        "cost_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("cost_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cost_records_business_id", "cost_records", ["business_id"])
    op.create_index(
        "ix_costs_business_type", "cost_records", ["business_id", "cost_type"]
    )
    op.create_index(
        "ix_costs_business_incurred", "cost_records", ["business_id", "incurred_at"]
    )
    op.create_index("ix_costs_case", "cost_records", ["case_id"])

    op.create_table(
        "revenue_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("outcome_id", sa.String(length=32), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_revenue_records_business_id", "revenue_records", ["business_id"]
    )
    op.create_index(
        "ix_revenue_business_verified", "revenue_records", ["business_id", "verified"]
    )
    op.create_index("ix_revenue_case", "revenue_records", ["case_id"])

    # -- integration connections + sync state --------------------------------------------
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "provider",
            "name",
            name="uq_integration_business_provider_name",
        ),
    )
    op.create_index(
        "ix_integration_connections_business_id",
        "integration_connections",
        ["business_id"],
    )
    op.create_index(
        "ix_integrations_business_status",
        "integration_connections",
        ["business_id", "status"],
    )

    op.create_table(
        "integration_sync_states",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("cursor", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_synced", sa.Integer(), nullable=False),
        sa.Column("total_failed", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "connection_id",
            "direction",
            name="uq_integration_sync_direction",
        ),
    )
    op.create_index(
        "ix_integration_sync_states_business_id",
        "integration_sync_states",
        ["business_id"],
    )
    op.create_index(
        "ix_integration_sync_business_status",
        "integration_sync_states",
        ["business_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_sync_business_status", table_name="integration_sync_states"
    )
    op.drop_index(
        "ix_integration_sync_states_business_id", table_name="integration_sync_states"
    )
    op.drop_table("integration_sync_states")

    op.drop_index("ix_integrations_business_status", table_name="integration_connections")
    op.drop_index(
        "ix_integration_connections_business_id", table_name="integration_connections"
    )
    op.drop_table("integration_connections")

    op.drop_index("ix_revenue_case", table_name="revenue_records")
    op.drop_index("ix_revenue_business_verified", table_name="revenue_records")
    op.drop_index("ix_revenue_records_business_id", table_name="revenue_records")
    op.drop_table("revenue_records")

    op.drop_index("ix_costs_case", table_name="cost_records")
    op.drop_index("ix_costs_business_incurred", table_name="cost_records")
    op.drop_index("ix_costs_business_type", table_name="cost_records")
    op.drop_index("ix_cost_records_business_id", table_name="cost_records")
    op.drop_table("cost_records")

    op.drop_index("ix_outcome_evidence_outcome", table_name="outcome_evidence")
    op.drop_table("outcome_evidence")

    op.drop_index("ix_outcomes_lead", table_name="outcome_records")
    op.drop_index("ix_outcomes_case", table_name="outcome_records")
    op.drop_index("ix_outcomes_business_type", table_name="outcome_records")
    op.drop_index("ix_outcome_records_business_id", table_name="outcome_records")
    op.drop_table("outcome_records")

    op.drop_index("ix_business_rule_versions_business", table_name="business_rule_versions")
    op.drop_table("business_rule_versions")

    op.drop_index("ix_business_rules_business_active", table_name="business_rules")
    op.drop_index("ix_business_rules_business_id", table_name="business_rules")
    op.drop_table("business_rules")

    op.drop_index(
        "ix_conversation_analysis_business", table_name="conversation_analyses"
    )
    op.drop_index(
        "ix_conversation_analyses_business_id", table_name="conversation_analyses"
    )
    op.drop_table("conversation_analyses")

    op.drop_index("ix_reminders_due", table_name="reminders")
    op.drop_index("ix_reminders_business_status", table_name="reminders")
    op.drop_index("ix_reminders_business_id", table_name="reminders")
    op.drop_table("reminders")

    op.drop_index("ix_appointments_lead", table_name="appointments")
    op.drop_index("ix_appointments_case", table_name="appointments")
    op.drop_index("ix_appointments_business_start", table_name="appointments")
    op.drop_index("ix_appointments_business_id", table_name="appointments")
    op.drop_table("appointments")

    op.drop_index("ix_followups_lead", table_name="follow_up_tasks")
    op.drop_index("ix_followups_due", table_name="follow_up_tasks")
    op.drop_index("ix_followups_business_status", table_name="follow_up_tasks")
    op.drop_index("ix_follow_up_tasks_business_id", table_name="follow_up_tasks")
    op.drop_table("follow_up_tasks")

    op.drop_index("ix_wf_actions_business", table_name="workflow_actions")
    op.drop_index("ix_wf_actions_run", table_name="workflow_actions")
    op.drop_table("workflow_actions")

    op.drop_index("ix_wf_runs_case", table_name="workflow_runs")
    op.drop_index("ix_wf_runs_business_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_business_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")

    op.drop_index("ix_wf_elig_business_created", table_name="workflow_eligibility_decisions")
    op.drop_index("ix_wf_elig_business_lead", table_name="workflow_eligibility_decisions")
    op.drop_index(
        "ix_workflow_eligibility_decisions_business_id",
        table_name="workflow_eligibility_decisions",
    )
    op.drop_table("workflow_eligibility_decisions")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_column("entry_hash")
        batch_op.drop_column("previous_hash")

    with op.batch_alter_table("call_outcomes", schema=None) as batch_op:
        batch_op.drop_column("evidence")
        batch_op.drop_column("field_confidence")

    with op.batch_alter_table("calls", schema=None) as batch_op:
        batch_op.drop_column("script_version_id")
        batch_op.drop_column("campaign_id")
