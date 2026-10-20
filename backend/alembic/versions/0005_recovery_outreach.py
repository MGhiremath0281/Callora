"""Recovery Dashboard — outreach content and outreach history tables.

Revision ID: 0005_outreach
Revises: 0004_priority
Create Date: 2026-10-20

Adds the two tables the Recovery Dashboard persists to:

``recovery_outreach_content``  the current AI-generated outreach pack for
                              one recovery case (replacing it in place on
                              regeneration, so reads never need the LLM);
``recovery_outreach_events``  the append-only outreach history — content
                              generation events today, ACT-stage
                              execution events (call/message outcomes)
                              once that stage exists. Nothing here ever
                              records an execution that did not happen.

No existing table is modified; Modules 1-5 keep their exact behaviour.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_outreach"
down_revision: Union[str, None] = "0004_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recovery_outreach_content",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("generation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regenerated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "case_id", name="uq_recovery_outreach_business_case"
        ),
    )
    op.create_index(
        "ix_recovery_outreach_content_business_id",
        "recovery_outreach_content",
        ["business_id"],
    )
    op.create_index(
        "ix_recovery_outreach_content_lead",
        "recovery_outreach_content",
        ["lead_id"],
    )

    op.create_table(
        "recovery_outreach_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("engine", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recovery_outreach_events_business_id",
        "recovery_outreach_events",
        ["business_id"],
    )
    op.create_index(
        "ix_recovery_outreach_events_case",
        "recovery_outreach_events",
        ["case_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recovery_outreach_events_case", table_name="recovery_outreach_events"
    )
    op.drop_index(
        "ix_recovery_outreach_events_business_id", table_name="recovery_outreach_events"
    )
    op.drop_table("recovery_outreach_events")
    op.drop_index(
        "ix_recovery_outreach_content_lead", table_name="recovery_outreach_content"
    )
    op.drop_index(
        "ix_recovery_outreach_content_business_id", table_name="recovery_outreach_content"
    )
    op.drop_table("recovery_outreach_content")
