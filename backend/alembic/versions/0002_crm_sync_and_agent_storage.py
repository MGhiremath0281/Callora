"""Module 2: CRM sync and agent storage tables

Revision ID: 0002_crm_sync
Revises: 0001_module1
Create Date: 2026-10-19

Creates crm_connections, crm_sync_records, crm_sync_idempotency and
agent_leads for Module 2 (CRM Synchronization) and downstream agent storage.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_crm_sync"
down_revision: Union[str, None] = "0001_module1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crm_connections",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("crm_base_url", sa.String(length=500), nullable=False),
        sa.Column("crm_api_key", sa.String(length=500), nullable=True),
        sa.Column("crm_timeout_seconds", sa.Float(), nullable=False, server_default="15.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "connection_id", name="uq_crm_conn_business_connection"
        ),
    )
    op.create_index("ix_crm_connections_business", "crm_connections", ["business_id"])

    op.create_table(
        "crm_sync_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("lead_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("external_crm_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crm_sync_lead", "crm_sync_records", ["lead_id"])
    op.create_index("ix_crm_sync_connection", "crm_sync_records", ["connection_id"])
    op.create_index("ix_crm_sync_business", "crm_sync_records", ["business_id"])

    op.create_table(
        "crm_sync_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("response_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="processing"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "connection_id", "idempotency_key", name="uq_crm_sync_idem"
        ),
    )

    op.create_table(
        "agent_leads",
        sa.Column("lead_id", sa.String(length=32), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="New"),
        sa.Column("external_crm_id", sa.String(length=128), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("deal_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_leads_business", "agent_leads", ["business_id"])
    op.create_index("ix_agent_leads_status", "agent_leads", ["status"])
    op.create_index("ix_agent_leads_external", "agent_leads", ["external_crm_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_leads_external", table_name="agent_leads")
    op.drop_index("ix_agent_leads_status", table_name="agent_leads")
    op.drop_index("ix_agent_leads_business", table_name="agent_leads")
    op.drop_table("agent_leads")

    op.drop_table("crm_sync_idempotency")

    op.drop_index("ix_crm_sync_business", table_name="crm_sync_records")
    op.drop_index("ix_crm_sync_connection", table_name="crm_sync_records")
    op.drop_index("ix_crm_sync_lead", table_name="crm_sync_records")
    op.drop_table("crm_sync_records")

    op.drop_index("ix_crm_connections_business", table_name="crm_connections")
    op.drop_table("crm_connections")
