"""Module 1: lead import schema

Revision ID: 0001_module1
Revises:
Create Date: 2026-10-18

Creates the canonical lead table, import batch history, row-level
validation errors, the transactional outbox for lead.created events and
the idempotency key store.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_module1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("deal_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "phone", name="uq_leads_business_phone"),
        sa.UniqueConstraint("business_id", "external_id", name="uq_leads_business_external"),
    )
    op.create_index("ix_leads_business_id", "leads", ["business_id"])
    op.create_index("ix_leads_business_source", "leads", ["business_id", "source"])
    op.create_index("ix_leads_business_created", "leads", ["business_id", "created_at"])

    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mapping", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_import_batches_business_id", "import_batches", ["business_id"])

    op.create_table(
        "import_row_errors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "import_id",
            sa.String(length=32),
            sa.ForeignKey("import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_import_row_errors_import", "import_row_errors", ["import_id", "row_number"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_events_business_id", "outbox_events", ["business_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status", "created_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("response_ref", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "scope", "key", name="uq_idempotency_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_business_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_import_row_errors_import", table_name="import_row_errors")
    op.drop_table("import_row_errors")
    op.drop_index("ix_import_batches_business_id", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_leads_business_created", table_name="leads")
    op.drop_index("ix_leads_business_source", table_name="leads")
    op.drop_index("ix_leads_business_id", table_name="leads")
    op.drop_table("leads")
