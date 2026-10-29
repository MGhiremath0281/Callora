"""Request and response schemas for the Bolna custom functions.

These are the live, mid-call tool contracts: Bolna takes the function
definition (see ``GET /api/v1/voice/bolna/custom-functions``), collects the
parameters from the conversation and POSTs them as a JSON body built from
the ``param`` mapping of the tool configuration. The response JSON is
handed back to the language model, so it must be small, flat and
predictable — every response is wrapped in the shared tool envelope:

    {"success": true,  "message": ..., "data": {...}}
    {"success": false, "error": {"code": ..., "message": ...}}

Extra keys that Bolna injects into the body (``agent_id``, ``call_sid``,
``from_number`` ...) are ignored, never trusted for authorization.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.voice.schemas import _as_utc


class ToolModel(BaseModel):
    """Base model for tool requests.

    ``extra="ignore"`` on purpose: Bolna may add runtime-injected keys to
    the body. Authorization never comes from the body — it comes from the
    bearer token (``tools.security``) — so extra keys are data, never
    privileges.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ToolOutcome(str, Enum):
    """The controlled set of call outcomes the LLM may report.

    The values are mapped onto the existing lead-status vocabulary in
    ``tools.service`` — no new status space is invented.
    """

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    CALLBACK_REQUESTED = "callback_requested"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"


class CustomerInterest(str, Enum):
    INTERESTED = "interested"
    MAYBE = "maybe"
    NOT_INTERESTED = "not_interested"
    UNKNOWN = "unknown"


class LeadDetailsRequest(ToolModel):
    """``get_lead_details`` — retrieve the lead the conversation is with."""

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)


class SaveOutcomeRequest(ToolModel):
    """``save_call_outcome`` — persist what the conversation established."""

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)
    outcome: ToolOutcome
    qualification_answers: Optional[Dict[str, Any]] = Field(default=None)
    customer_interest: Optional[CustomerInterest] = None
    follow_up_preference: Optional[str] = Field(default=None, max_length=200)
    callback_requested: Optional[bool] = None
    callback_time: Optional[datetime] = None
    callback_timezone: Optional[str] = Field(default=None, max_length=64)
    callback_reason: Optional[str] = Field(default=None, max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("callback_time")
    @classmethod
    def _normalize_callback_time(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)

    @field_validator("qualification_answers")
    @classmethod
    def _bound_answers(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if len(value) > 50:
            raise ValueError("qualification_answers may not carry more than 50 entries")
        clean: Dict[str, Any] = {}
        for key, item in value.items():
            if not str(key).strip():
                raise ValueError("qualification_answers keys must not be empty")
            if isinstance(item, (str, int, float, bool)) or item is None:
                clean[str(key)[:64]] = item
            else:
                # Nested structures are flattened to their string form so a
                # chatty model cannot smuggle arbitrary JSON into storage.
                clean[str(key)[:64]] = str(item)[:500]
        return clean


class QualifyLeadRequest(ToolModel):
    """``qualify_lead`` — evaluate the collected answers against the rules."""

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)
    answers: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator("answers")
    @classmethod
    def _bound_answers(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if len(value) > 50:
            raise ValueError("answers may not carry more than 50 entries")
        return {str(k)[:64]: v for k, v in value.items()}


class CreateOpportunityRequest(ToolModel):
    """``create_opportunity`` — open a sales opportunity for a lead."""

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)
    answers: Optional[Dict[str, Any]] = Field(default=None)
    # Only honoured for a request authenticated with a business API key
    # (an operator path), never for the shared Bolna tool token.
    force: bool = False

    @field_validator("answers")
    @classmethod
    def _bound_answers(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if len(value) > 50:
            raise ValueError("answers may not carry more than 50 entries")
        return {str(k)[:64]: v for k, v in value.items()}


class ScheduleCallbackRequest(ToolModel):
    """``schedule_callback`` — record a callback the customer asked for.

    ``requested_at`` is interpreted *in* ``timezone`` when it is naive
    (the natural contract for a spoken "tomorrow at 11") and as an
    absolute moment when it carries an explicit UTC offset. The service
    performs the interpretation; storage is always UTC.
    """

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)
    requested_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=500)


class CrmSyncToolRequest(ToolModel):
    """``sync_lead_to_crm`` — push the lead and its outcome to the CRM."""

    business_id: Optional[str] = Field(default=None, max_length=64)
    lead_id: str = Field(min_length=1, max_length=32)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    call_id: Optional[str] = Field(default=None, max_length=32)
    connection_id: str = Field(default="default", max_length=100)


# ---------------------------------------------------------------------------
# Qualification result
# ---------------------------------------------------------------------------


class QualificationResultModel(BaseModel):
    """The structured, rule-based qualification result."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        description=(
            "qualified | not_qualified | insufficient_data — 'insufficient_data' "
            "means a required question was never answered; the rules never "
            "invent an answer."
        )
    )
    qualified: Optional[bool] = None
    score: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    answers: Dict[str, Any] = Field(default_factory=dict)
    unknown_keys: List[str] = Field(default_factory=list)
    engine: str = "rules"
    evaluated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Tool response envelope
# ---------------------------------------------------------------------------


def tool_success(data: Dict[str, Any], message: str) -> Dict[str, Any]:
    """The response the voice agent reads on success."""
    return {"success": True, "message": message, "data": data}


def tool_error(code: str, message: str) -> Dict[str, Any]:
    """The response the voice agent reads on failure.

    It never carries stack traces, SQL text or configuration values.
    """
    return {
        "success": False,
        "error": {"code": code, "message": message},
    }


__all__ = [
    "CreateOpportunityRequest",
    "CrmSyncToolRequest",
    "CustomerInterest",
    "LeadDetailsRequest",
    "QualificationResultModel",
    "QualifyLeadRequest",
    "SaveOutcomeRequest",
    "ScheduleCallbackRequest",
    "ToolModel",
    "ToolOutcome",
    "tool_error",
    "tool_success",
]
