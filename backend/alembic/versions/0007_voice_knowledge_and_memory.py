"""Voice Agent — Business knowledge base and live conversation memory.

Revision ID: 0007_voice_knowledge_memory
Revises: 0006_voice
Create Date: 2026-10-22

Adds:
``business_knowledge_items``   verified business FAQs, product features, pricing,
                               eligibility, policies, objection handling and escalation rules;
``voice_call_memories``        live conversational state, answered questions,
                               qualification progress, and handoff tracking.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_voice_knowledge_memory"
down_revision: Union[str, None] = "0006_voice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- business knowledge items ---------------------------------------------
    op.create_table(
        "business_knowledge_items",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="faq"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column(
            "source_reference",
            sa.String(length=255),
            nullable=False,
            server_default="Verified Business Knowledge",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_knowledge_business_category",
        "business_knowledge_items",
        ["business_id", "category"],
    )
    op.create_index(
        "ix_knowledge_business_active",
        "business_knowledge_items",
        ["business_id", "is_active"],
    )
    op.create_index(
        "ix_business_knowledge_items_business_id",
        "business_knowledge_items",
        ["business_id"],
    )

    # -- voice call memories --------------------------------------------------
    op.create_table(
        "voice_call_memories",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("call_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
        sa.Column("customer_intent", sa.String(length=64), nullable=True),
        sa.Column("questions_answered", sa.JSON(), nullable=False),
        sa.Column("qualification_fields", sa.JSON(), nullable=False),
        sa.Column("pending_qualification_keys", sa.JSON(), nullable=False),
        sa.Column("handoff_status", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("opt_out_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("turns_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_customer_question", sa.Text(), nullable=True),
        sa.Column("last_agent_answer", sa.Text(), nullable=True),
        sa.Column("memory_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("call_id", name="uq_voice_call_memories_call"),
    )
    op.create_index(
        "ix_voice_call_memories_business",
        "voice_call_memories",
        ["business_id"],
    )
    op.create_index(
        "ix_voice_call_memories_call_id",
        "voice_call_memories",
        ["call_id"],
    )


def downgrade() -> None:
    op.drop_table("voice_call_memories")
    op.drop_table("business_knowledge_items")
