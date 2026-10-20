"""Module 3 (Opportunity Detection) — recovery cases + agent lead created_at.

Revision ID: 0003_recovery
Revises: 0002_crm_sync
Create Date: 2026-10-20

Two changes:

1. ``agent_leads.created_at`` — the opportunity-detection engine needs
   the lead's creation time for the never-contacted signal; the
   projection previously only carried ``updated_at``. Existing rows are
   backfilled from ``updated_at`` (the best available approximation).
2. ``recovery_cases`` — one row per detected recovery opportunity,
   idempotent per (business_id, lead_id); the hand-off surface for
   downstream workflow steps.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recovery"
down_revision: Union[str, None] = "0002_crm_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. agent_leads.created_at -------------------------------------
    op.add_column(
        "agent_leads",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: updated_at is the closest proxy for pre-existing rows.
    op.execute("UPDATE agent_leads SET created_at = updated_at WHERE created_at IS NULL")
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "agent_leads",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
    # SQLite cannot ALTER COLUMN to NOT NULL; the column stays nullable
    # there (local dev / tests), and the ORM always supplies a value.

    # --- 2. recovery_cases ---------------------------------------------
    op.create_table(
        "recovery_cases",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("lead_name", sa.String(length=255), nullable=True),
        sa.Column("score", sa.Numeric(4, 3), nullable=True),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("route", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("needs_human_approval", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "lead_id", name="uq_recovery_cases_business_lead"),
    )
    op.create_index("ix_recovery_cases_business", "recovery_cases", ["business_id"])
    op.create_index("ix_recovery_cases_status", "recovery_cases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_recovery_cases_status", table_name="recovery_cases")
    op.drop_index("ix_recovery_cases_business", table_name="recovery_cases")
    op.drop_table("recovery_cases")
    op.drop_column("agent_leads", "created_at")
