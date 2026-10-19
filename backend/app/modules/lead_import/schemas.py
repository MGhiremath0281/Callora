"""Pydantic request/response schemas shared by every lead entry point.

``LeadRead`` is the single, strictly enforced lead output contract. Both
input formats (CSV upload via ``POST /leads/import`` and direct JSON
submission via ``POST /leads``) resolve to this exact schema, so
downstream modules (CRM Sync, Campaigns) can rely on one shape:

* ``phone`` is always a string (no numeric coercion, country codes kept
  as given);
* ``deal_value`` is always a decimal string with two fractional digits
  (e.g. ``"7500000.00"``), never a float.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, field_serializer

from app.modules.lead_import.normalizer import normalize_name, normalize_phone


# ---------------------------------------------------------------------------
# Lead schemas (canonical output contract)
# ---------------------------------------------------------------------------


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
    """Standardized lead output shared by CSV imports and JSON creation."""

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

    @field_serializer("deal_value")
    def _serialize_deal_value(self, value: Optional[Decimal]) -> Optional[str]:
        """Always emit deal_value as a 2-decimal-digit string."""
        if value is None:
            return None
        return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


class LeadListResponse(BaseModel):
    items: List[LeadRead]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Import report schemas
# ---------------------------------------------------------------------------


class MappingSuggestionModel(BaseModel):
    column: str
    canonical_field: Optional[str] = None
    confidence: float
    matched_on: Optional[str] = None
    ambiguous: bool = False
    alternatives: List[str] = Field(default_factory=list)


class ImportSummary(BaseModel):
    import_id: str
    status: str
    total_rows: int
    created: int
    duplicates: int
    rejected: int
    errors_url: str


class ImportStatusResponse(ImportSummary):
    source_type: str
    original_filename: Optional[str] = None
    mapping: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ImportErrorModel(BaseModel):
    row_number: int
    field: Optional[str] = None
    error_code: str
    message: str
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class ImportErrorListResponse(BaseModel):
    import_id: str
    total: int
    page: int
    page_size: int
    items: List[ImportErrorModel]
