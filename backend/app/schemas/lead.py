"""Pydantic request/response schemas for the lead endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.normalization import normalize_name, normalize_phone


class LeadCreate(BaseModel):
    """JSON payload accepted by ``POST /leads``.

    ``business_id`` is intentionally absent: it always comes from the
    authenticated business context, never from client-supplied data.
    """

    name: str = Field(..., min_length=1, max_length=255, examples=["Rahul Sharma"])
    phone: str = Field(..., min_length=1, max_length=32, examples=["9876543210"])
    source: str = Field(..., min_length=1, max_length=100, examples=["99acres"])
    email: Optional[EmailStr] = None
    language: Optional[str] = Field(default=None, max_length=32)
    deal_value: Optional[Decimal] = Field(default=None, ge=0)
    last_contact_at: Optional[datetime] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    external_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = normalize_name(value)
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error or not normalized:
            raise ValueError(error or "phone is required")
        return normalized

    @field_validator("source")
    @classmethod
    def _strip_source(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("source must not be blank")
        return cleaned[:100]

    @field_validator("language")
    @classmethod
    def _strip_language(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().lower()[:32] if value and value.strip() else None

    @field_validator("custom_fields")
    @classmethod
    def _limit_custom_fields(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(value) > 50:
            raise ValueError("at most 50 custom fields are allowed")
        return value


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    name: str
    phone: str
    email: Optional[str] = None
    source: str
    language: Optional[str] = None
    deal_value: Optional[Decimal] = None
    last_contact_at: Optional[datetime] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    external_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    items: List[LeadRead]
    total: int
    page: int
    page_size: int
