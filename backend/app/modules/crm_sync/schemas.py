"""Request and response schemas for CRM synchronization."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LeadSyncInput(BaseModel):
    """Standardized lead received from Module 1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    business_id: str
    name: str
    phone: str
    email: Optional[str] = None
    source: str
    language: Optional[str] = None
    deal_value: Optional[Decimal] = None
    last_contact_at: Optional[datetime] = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    external_id: Optional[str] = None
    created_at: datetime


class CRMSyncRequest(BaseModel):
    """Request to synchronize one lead."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str
    lead: LeadSyncInput
    idempotency_key: Optional[str] = None


class CRMSyncResponse(BaseModel):
    """Result of a synchronization attempt."""

    sync_id: str
    lead_id: str
    business_id: str
    status: str
    external_crm_id: Optional[str] = None
    message: str


class CRMConnectionResponse(BaseModel):
    """Read model for a CRM connection."""

    connection_id: str
    name: str
    is_active: bool
    crm_base_url: str
    crm_timeout_seconds: float
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
