"""Voice Agent — voice-created sales opportunities.

Revision ID: 0008_voice_opportunities
Revises: 8519e5a60ac0
Create Date: 2026-10-28

Adds:
``voice_opportunities``   one sales opportunity per (business, lead,
                          qualifying event) created by the Bolna custom
                          functions. Recovery opportunities stay in
                          ``recovery_cases`` (Module 3); this table records
                          opportunities opened by a qualified live
                          conversation, together with the rule-based
                          qualification result that justified them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_voice_opportunities"
down_revision: Union[str, None] = "8519e5a60ac0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_opportunities",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("call_id", sa.String(length=32), nullable=True),
        sa.Column("provider_call_id", sa.String(length=128), nullable=True),
        sa.Column("source_event_key", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="voice"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("qualification_result", sa.JSON(), nullable=False),
        sa.Column("qualification_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "business_id",
            "lead_id",
            "source_event_key",
            name="uq_voice_opportunities_biz_lead_event",
        ),
    )
    op.create_index(
        "ix_voice_opportunities_business_status",
        "voice_opportunities",
        ["business_id", "status"],
    )
    op.create_index(
        "ix_voice_opportunities_lead",
        "voice_opportunities",
        ["lead_id"],
    )
    op.create_index(
        "ix_voice_opportunities_business_id",
        "voice_opportunities",
        ["business_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_voice_opportunities_business_id", table_name="voice_opportunities"
    )
    op.drop_index("ix_voice_opportunities_lead", table_name="voice_opportunities")
    op.drop_index(
        "ix_voice_opportunities_business_status", table_name="voice_opportunities"
    )
    op.drop_table("voice_opportunities")
