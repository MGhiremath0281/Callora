"""SQLAlchemy models for Module 1 (Lead & Customer Import).

One canonical lead schema is shared by every input source and industry;
industry-specific attributes live in ``custom_fields`` and approved
external identifiers in ``external_id`` so Module 2 (CRM Sync) can link
records later without schema changes.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    DateTime,
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


class ImportStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class EventStatus:
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class Lead(Base):
    """Canonical lead / customer record."""

    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("business_id", "phone", name="uq_leads_business_phone"),
        UniqueConstraint("business_id", "external_id", name="uq_leads_business_external"),
        Index("ix_leads_business_source", "business_id", "source"),
        Index("ix_leads_business_created", "business_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: prefixed_id("lead"))
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(32))
    deal_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    last_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    custom_fields: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # ``external_id`` stores the originating system's identifier (e.g. CRM
    # record id). Module 2 uses it to synchronize without duplicating data.
    external_id: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ImportBatch(Base):
    """History and status of one import run."""

    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("imp")
    )
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ImportStatus.PENDING)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # csv_upload | api
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    mapping: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ImportRowError(Base):
    """Row-level validation error produced during an import."""

    __tablename__ = "import_row_errors"
    __table_args__ = (Index("ix_import_row_errors_import", "import_id", "row_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    import_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[Optional[str]] = mapped_column(String(100))
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OutboxEvent(Base):
    """Transactional outbox for ``lead.created`` events.

    The event row is committed in the same transaction as the lead itself,
    so a committed lead always has a reliable event record. Delivery (HTTP
    webhook when configured, otherwise downstream polling) happens after
    commit and is retried idempotently.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_status", "status", "created_at"),)

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: prefixed_id("evt")
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lead_id: Mapped[Optional[str]] = mapped_column(String(32))
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=EventStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    """Stores processed idempotency keys so retried requests replay safely."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("business_id", "scope", "key", name="uq_idempotency_scope_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    response_ref: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
