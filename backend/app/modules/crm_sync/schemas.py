"""Request and response schemas for CRM synchronization."""

import ipaddress
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.crm_sync.destination_guard import (
    BLOCKED_HOSTNAMES,
    host_port_from_url,
    is_dev_allowed_destination,
    is_forbidden_ip,
)


def validate_crm_base_url(value: str) -> str:
    """Validate an externally supplied CRM base URL (SSRF guard).

    Allows only absolute http(s) URLs without embedded credentials and
    rejects loopback, link-local/metadata, and private-range destinations.
    A single exact ``host:port`` may be opted in for development via
    ``CALLORA_CRM_DEV_ALLOWED_DESTINATIONS`` (empty by default);
    ``CALLORA_CRM_ALLOW_PRIVATE_URLS=true`` remains the broader legacy
    local-testing escape hatch.
    """
    from app.config import get_settings

    settings = get_settings()
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            "crm_base_url must be an absolute http(s) URL, "
            "e.g. https://crm.example.com"
        )
    if parsed.username or parsed.password:
        raise ValueError("crm_base_url must not contain embedded credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("crm_base_url must include a hostname")
    pair = host_port_from_url(url)
    if pair is None:
        raise ValueError("crm_base_url must include a valid port (1-65535)")
    _, port = pair
    if settings.crm_allow_private_urls:
        return url
    if is_dev_allowed_destination(host, port, settings):
        return url
    if host in BLOCKED_HOSTNAMES or host.endswith(".internal"):
        raise ValueError(
            f"crm_base_url host '{host}' is not an allowed external destination"
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url  # hostname — re-checked after DNS resolution per request
    if is_forbidden_ip(ip):
        raise ValueError(
            f"crm_base_url IP '{host}' is not an allowed external destination"
        )
    return url


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
    """Read model for a CRM connection.

    The external CRM's API key is never included — only whether one is
    configured (``has_api_key``).
    """

    connection_id: str
    name: str
    is_active: bool
    crm_base_url: str
    has_api_key: bool
    crm_timeout_seconds: float
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CRMConnectionUpsertRequest(BaseModel):
    """Business-supplied configuration for its external CRM connection.

    ``crm_x_api_key`` is the external CRM's own x-api-key credential —
    it is stored server-side for outgoing requests and never returned.
    It is unrelated to Callora's inbound X-API-Key authentication.
    """

    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(
        default="default",
        min_length=1,
        max_length=100,
        description="Identifier used later by POST /crm/sync.",
    )
    name: str = Field(..., min_length=1, max_length=255)
    crm_base_url: str = Field(..., min_length=1, max_length=500)
    crm_x_api_key: str = Field(..., min_length=1, max_length=500)
    crm_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    @field_validator("crm_base_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return validate_crm_base_url(value)


class CRMConnectionSaveResponse(BaseModel):
    """Result of saving (and validating) a CRM connection."""

    connection: CRMConnectionResponse
    verified: bool
    message: str
