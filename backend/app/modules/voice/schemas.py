"""Request and response schemas for the outbound voice API.

The schemas are the public contract: everything a client sends is
validated here before it reaches the orchestrator, and every response
body is produced from these models so the OpenAPI document is the single
source of truth.

All datetimes are timezone-aware. A naive datetime is interpreted as UTC
(the deployment reference time) and rejected when it cannot be made
sensible.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.voice.constants import (
    CallStatus,
    ConsentChannel,
    ConsentStatus,
    DndScope,
    FollowUpAction,
    HandoffKind,
    HandoffStatus,
    JobStatus,
    KnowledgeCategory,
    RefusalCategory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Interpret a naive datetime as UTC; keep an aware one as it is."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class VoiceModel(BaseModel):
    """Base model with the shared configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Business configuration
# ---------------------------------------------------------------------------


class QualificationQuestion(VoiceModel):
    """One qualification question asked by the voice agent."""

    key: str = Field(max_length=64)
    question: str = Field(max_length=500)
    type: str = Field(default="text", max_length=32)
    required: bool = False


class BusinessProfile(VoiceModel):
    """The voice configuration of one business."""

    business_id: str
    name: str
    timezone: str
    default_language: str
    supported_languages: List[str] = Field(default_factory=list)
    calling_window_start: int
    calling_window_end: int
    calling_days: List[int] = Field(default_factory=list)
    max_calls_per_lead_per_day: int
    max_calls_per_business_per_day: int
    max_concurrent_calls: int
    require_consent: bool
    dnd_enabled: bool
    bolna_agent_id: Optional[str] = None
    agent_instructions: Optional[str] = None
    qualification_questions: List[Dict[str, Any]] = Field(default_factory=list)
    warm_transfer_enabled: bool
    warm_transfer_number: Optional[str] = None
    handoff_target_number: Optional[str] = None
    is_active: bool
    outbound_calls_enabled: bool = Field(
        description=(
            "Deployment-wide kill switch. While false every accepted call is "
            "recorded as 'simulated' and no provider is contacted."
        )
    )
    provider_configured: bool = Field(
        description="Whether the voice provider credentials are present."
    )
    call_quotas_disabled: bool = Field(
        default=False,
        description=(
            "Whether the daily per-lead and per-business call quotas are "
            "skipped. Only ever true in a development environment."
        ),
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BusinessProfileUpdate(VoiceModel):
    """Partial update of the business voice configuration."""

    name: Optional[str] = Field(default=None, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=64)
    default_language: Optional[str] = Field(default=None, max_length=16)
    supported_languages: Optional[List[str]] = None
    calling_window_start: Optional[int] = Field(default=None, ge=0, le=23)
    calling_window_end: Optional[int] = Field(default=None, ge=0, le=23)
    calling_days: Optional[List[int]] = None
    max_calls_per_lead_per_day: Optional[int] = Field(default=None, ge=1, le=50)
    max_calls_per_business_per_day: Optional[int] = Field(default=None, ge=1, le=100_000)
    max_concurrent_calls: Optional[int] = Field(default=None, ge=1, le=1_000)
    require_consent: Optional[bool] = None
    dnd_enabled: Optional[bool] = None
    bolna_agent_id: Optional[str] = Field(default=None, max_length=128)
    agent_instructions: Optional[str] = None
    qualification_questions: Optional[List[Dict[str, Any]]] = None
    warm_transfer_enabled: Optional[bool] = None
    warm_transfer_number: Optional[str] = Field(default=None, max_length=32)
    handoff_target_number: Optional[str] = Field(default=None, max_length=32)
    is_active: Optional[bool] = None

    @field_validator("calling_days")
    @classmethod
    def _validate_days(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is None:
            return None
        for day in value:
            if day < 0 or day > 6:
                raise ValueError("calling_days entries must be between 0 (Mon) and 6 (Sun)")
        return sorted(set(value))

    @field_validator("supported_languages")
    @classmethod
    def _validate_languages(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return [str(item).strip().lower() for item in value if str(item).strip()]


# ---------------------------------------------------------------------------
# Leads, consent, DND
# ---------------------------------------------------------------------------


class VoiceLeadProfile(VoiceModel):
    """Voice-specific state of one lead."""

    lead_id: str
    business_id: str
    lead_name: Optional[str] = None
    phone: Optional[str] = None
    preferred_language: Optional[str] = None
    timezone: Optional[str] = None
    do_not_call: bool
    notes: Optional[str] = None
    call_attempts: int
    last_call_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VoiceLeadProfileUpdate(VoiceModel):
    """Partial update of the voice state of a lead."""

    preferred_language: Optional[str] = Field(default=None, max_length=16)
    timezone: Optional[str] = Field(default=None, max_length=64)
    do_not_call: Optional[bool] = None
    notes: Optional[str] = None


class ConsentRecordRequest(VoiceModel):
    """Record (or change) the consent evidence for one lead."""

    status: str
    channel: str = ConsentChannel.VOICE
    phone: Optional[str] = Field(default=None, max_length=32)
    source: Optional[str] = Field(default=None, max_length=100)
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        allowed = {ConsentStatus.GRANTED, ConsentStatus.REVOKED, ConsentStatus.EXPIRED}
        key = str(value).strip().lower()
        if key not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return key

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str) -> str:
        allowed = {
            ConsentChannel.VOICE,
            ConsentChannel.SMS,
            ConsentChannel.EMAIL,
            ConsentChannel.WHATSAPP,
        }
        key = str(value).strip().lower()
        if key not in allowed:
            raise ValueError(f"channel must be one of {sorted(allowed)}")
        return key

    @field_validator("granted_at", "expires_at", "revoked_at")
    @classmethod
    def _normalize_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)


class ConsentRecordResponse(VoiceModel):
    """The stored consent evidence."""

    id: str
    business_id: str
    lead_id: str
    phone: Optional[str] = None
    channel: str
    status: str
    source: Optional[str] = None
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DndEntryCreate(VoiceModel):
    """Add a number to the do-not-disturb registry."""

    phone: str = Field(min_length=3, max_length=32)
    scope: str = DndScope.BUSINESS
    reason: Optional[str] = Field(default=None, max_length=255)
    source: Optional[str] = Field(default=None, max_length=100)
    expires_at: Optional[datetime] = None

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        key = str(value).strip().lower()
        if key not in (DndScope.BUSINESS, DndScope.GLOBAL):
            raise ValueError("scope must be 'business' or 'global'")
        return key

    @field_validator("expires_at")
    @classmethod
    def _normalize_expires(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)


class DndEntryResponse(VoiceModel):
    """One do-not-disturb registry entry."""

    id: str
    scope: str
    business_id: str
    phone: str
    reason: Optional[str] = None
    source: Optional[str] = None
    active: bool
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


class CallInitiationRequest(VoiceModel):
    """Request to place one outbound call."""

    lead_id: str = Field(min_length=1, max_length=32)
    language: Optional[str] = Field(default=None, max_length=16)
    scheduled_at: Optional[datetime] = None
    from_number: Optional[str] = Field(default=None, max_length=32)
    agent_id: Optional[str] = Field(default=None, max_length=128)
    extra_context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(
        default=False,
        description=(
            "Evaluate the compliance gate and record the call without "
            "contacting the provider, even when outbound calling is enabled."
        ),
    )

    @field_validator("scheduled_at")
    @classmethod
    def _normalize_scheduled(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)


class CallResponse(VoiceModel):
    """One outbound call attempt."""

    id: str
    business_id: str
    lead_id: str
    provider: str
    provider_call_id: Optional[str] = None
    status: str
    blocked_reason: Optional[str] = None
    error_message: Optional[str] = None
    to_number: Optional[str] = None
    from_number: Optional[str] = None
    language: Optional[str] = None
    direction: str = "outbound"
    scheduled_at: Optional[datetime] = None
    initiated_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    cost: Optional[float] = None
    currency: str = "INR"
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    compliance_decision_id: Optional[str] = None
    simulated: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CallInitiationResponse(VoiceModel):
    """The result of a call initiation attempt."""

    call: CallResponse
    compliance_decision_id: str
    compliance_decision: str
    compliance_checks: List[Dict[str, Any]] = Field(default_factory=list)
    simulated: bool = False
    message: str = ""


class CallEventResponse(VoiceModel):
    """One provider event received on the webhook endpoint."""

    id: str
    call_id: Optional[str] = None
    provider: str
    event_key: str
    event_type: str
    provider_status: Optional[str] = None
    provider_event_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    signature_valid: Optional[bool] = None
    processed: bool
    processing_error: Optional[str] = None
    received_at: Optional[datetime] = None


class CallOutcomeResponse(VoiceModel):
    """The structured post-call intelligence of one call."""

    id: str
    business_id: str
    call_id: str
    lead_id: str
    summary: str
    disposition: str
    qualified: Optional[bool] = None
    qualification_score: Optional[float] = None
    qualification_answers: Dict[str, Any] = Field(default_factory=dict)
    refusal_category: Optional[str] = None
    refusal_reason: Optional[str] = None
    follow_up_actions: List[Any] = Field(default_factory=list)
    handoff_required: bool = False
    handoff_briefing: Optional[str] = None
    language_detected: Optional[str] = None
    model: Optional[str] = None
    engine: str
    processed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CallListResponse(VoiceModel):
    """A page of calls."""

    items: List[CallResponse] = Field(default_factory=list)
    business_id: str
    limit: int
    offset: int


class CallStatusResponse(VoiceModel):
    """The refreshed status of one call."""

    call: CallResponse
    refreshed: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Handoffs
# ---------------------------------------------------------------------------


class CallbackRequest(VoiceModel):
    """Ask a salesperson to call a lead back."""

    lead_id: Optional[str] = Field(default=None, max_length=32)
    call_id: Optional[str] = Field(default=None, max_length=32)
    reason: Optional[str] = None
    briefing: Optional[str] = None
    target_number: Optional[str] = Field(default=None, max_length=32)
    requested_by: str = "api"

    @field_validator("requested_by")
    @classmethod
    def _validate_requester(cls, value: str) -> str:
        key = str(value).strip().lower()
        if key not in ("api", "ai", "agent", "human"):
            raise ValueError("requested_by must be one of api, ai, agent, human")
        return key


class HandoffUpdate(VoiceModel):
    """Update the status or the outcome of a handoff."""

    status: Optional[str] = None
    outcome: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = None


class HandoffResponse(VoiceModel):
    """One human-handoff record."""

    id: str
    business_id: str
    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = None
    kind: str = HandoffKind.CALLBACK
    status: str = HandoffStatus.REQUESTED
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    requested_by: str = "ai"
    target_number: Optional[str] = None
    briefing: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    outcome: Optional[str] = None
    outcome_note: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class JobResponse(VoiceModel):
    """One background job."""

    id: str
    type: str
    business_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: str
    attempts: int
    max_attempts: int
    last_error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    dedupe_key: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AuditLogResponse(VoiceModel):
    """One audit-trail entry."""

    id: str
    business_id: Optional[str] = None
    actor: str
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    decision: Optional[str] = None
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class ComplianceDecisionResponse(VoiceModel):
    """One persisted compliance decision."""

    id: str
    business_id: str
    lead_id: Optional[str] = None
    call_id: Optional[str] = None
    decision: str
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    blocked_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    policy_version: str
    created_at: Optional[datetime] = None


class VoiceStatsResponse(VoiceModel):
    """A small operational summary of the voice activity of one business."""

    business_id: str
    calls_total: int
    calls_by_status: Dict[str, int] = Field(default_factory=dict)
    calls_today: int
    blocked_today: int
    handoffs_open: int
    consent_granted: int
    consent_missing: int
    dnd_entries: int
    jobs_pending: int
    jobs_failed: int
    total_cost: float = 0.0
    currency: str = "INR"


class StatusSyncResponse(VoiceModel):
    """The result of one reconciliation sweep.

    ``outcomes`` describes what happened to every call that was checked:
    ``applied`` (the provider moved the call forward), ``unchanged`` (the
    provider agrees with Callora), ``out_of_order`` (the provider reported
    an older status), ``unknown_provider_status`` (the provider reported a
    status Callora does not know — the call was left alone),
    ``provider_unreachable`` / ``provider_error`` (the provider could not be
    asked — the call was left alone), ``execution_missing`` (the provider
    does not know the execution any more) and ``timeout`` (the documented
    timeout policy closed the call).
    """

    business_id: str
    checked: int = 0
    applied: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    outcomes: List[Dict[str, Any]] = Field(default_factory=list)


class WebhookAck(BaseModel):
    """The acknowledgement returned to the voice provider."""

    model_config = ConfigDict(extra="allow")

    status: str
    call_id: Optional[str] = None
    event_id: Optional[str] = None
    call_status: Optional[str] = None
    job_id: Optional[str] = None
    signature_valid: Optional[bool] = None
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Knowledge Base & Follow-Up Questions
# ---------------------------------------------------------------------------


class KnowledgeItemCreate(VoiceModel):
    category: str = Field(default=KnowledgeCategory.FAQ, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    tags: List[str] = Field(default_factory=list)
    source_reference: str = Field(default="Verified Business Knowledge", max_length=255)
    is_active: bool = True
    priority: int = 0
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeItemUpdate(VoiceModel):
    category: Optional[str] = Field(default=None, max_length=64)
    title: Optional[str] = Field(default=None, max_length=255)
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    source_reference: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    extra_metadata: Optional[Dict[str, Any]] = None


class KnowledgeItemResponse(VoiceModel):
    id: str
    business_id: str
    category: str
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    source_reference: str
    is_active: bool
    priority: int
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class KnowledgeItemListResponse(VoiceModel):
    items: List[KnowledgeItemResponse] = Field(default_factory=list)
    total: int = 0
    business_id: str


class KnowledgeItemBatchCreate(VoiceModel):
    items: List[KnowledgeItemCreate] = Field(default_factory=list)


class FollowUpQuestionRequest(BaseModel):
    """Payload sent by Bolna (or test clients) when a follow-up question occurs."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    business_id: str = Field(min_length=1, max_length=64)
    call_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    language: Optional[str] = "en"
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)


class FollowUpQuestionResponse(BaseModel):
    """Answer and metadata returned to Bolna mid-conversation."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    answer: str
    source_references: List[str] = Field(default_factory=list)
    should_handoff: bool = False
    handoff_recommended: bool = False
    handoff_reason: Optional[str] = None
    handoff_type: Optional[str] = None
    handoff_id: Optional[str] = None
    action: str = Field(default=FollowUpAction.ANSWER)
    confidence: float = 1.0
    qualification_continuation: Optional[str] = None
    memory_state: Optional[Dict[str, Any]] = None


class BolnaToolDefinitionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool: Dict[str, Any]


__all__ = [
    "AuditLogResponse",
    "BolnaToolDefinitionResponse",
    "BusinessProfile",
    "BusinessProfileUpdate",
    "CallEventResponse",
    "CallInitiationRequest",
    "CallInitiationResponse",
    "CallListResponse",
    "CallOutcomeResponse",
    "CallResponse",
    "CallStatusResponse",
    "CallbackRequest",
    "ComplianceDecisionResponse",
    "ConsentRecordRequest",
    "ConsentRecordResponse",
    "DndEntryCreate",
    "DndEntryResponse",
    "FollowUpAction",
    "FollowUpQuestionRequest",
    "FollowUpQuestionResponse",
    "HandoffResponse",
    "HandoffUpdate",
    "JobResponse",
    "KnowledgeCategory",
    "KnowledgeItemBatchCreate",
    "KnowledgeItemCreate",
    "KnowledgeItemListResponse",
    "KnowledgeItemResponse",
    "KnowledgeItemUpdate",
    "QualificationQuestion",
    "VoiceLeadProfile",
    "VoiceLeadProfileUpdate",
    "VoiceStatsResponse",
    "WebhookAck",
    "CallStatus",
    "RefusalCategory",
]
