"""Request and response schemas for CRM synchronization."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LeadSyncInput(BaseModel):
    """Standardized lead received from Module 1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    business_id: str
    name: str
    phone: str
    email: str | None = None
    source: str
    language: str | None = None
    deal_value: Decimal | None = None
    last_contact_at: datetime | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = None
    created_at: datetime


class CRMSyncRequest(BaseModel):
    """Request to synchronize one lead."""

    connection_id: str
    lead: LeadSyncInput
    idempotency_key: str | None = None


class CRMSyncResponse(BaseModel):
    """Result of a synchronization attempt."""

    sync_id: str
    lead_id: str
    business_id: str
    status: str
    external_crm_id: str | None = None
    message: str