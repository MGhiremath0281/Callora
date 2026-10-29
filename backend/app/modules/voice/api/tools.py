"""HTTP endpoints for the Bolna custom functions.

    POST /api/v1/voice/tools/lead-details
    POST /api/v1/voice/tools/save-outcome
    POST /api/v1/voice/tools/qualify-lead
    POST /api/v1/voice/tools/create-opportunity
    POST /api/v1/voice/tools/schedule-callback
    POST /api/v1/voice/tools/crm-sync
    GET  /api/v1/voice/bolna/custom-functions

The six tool endpoints are called by the Bolna runtime, server to server,
while the agent is speaking to the customer. They authenticate with a
fixed bearer token (``tools.security``) and always answer with the flat
tool envelope the language model reads — never the platform's standard
error envelope (that mapping lives in ``app.errors`` for these paths).

``GET /voice/bolna/custom-functions`` emits the ready-to-paste Bolna
Agent Studio definitions of all six functions, with ``%(param)s``
specifiers Bolna fills from the conversation. The ``api_token`` value in
the definitions is a placeholder — the real secret lives in the
deployment environment and is never served over HTTP.
"""

import inspect
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import AppError
from app.modules.voice.tools.schemas import (
    CreateOpportunityRequest,
    CrmSyncToolRequest,
    LeadDetailsRequest,
    QualifyLeadRequest,
    SaveOutcomeRequest,
    ScheduleCallbackRequest,
    tool_error,
    tool_success,
)
from app.modules.voice.tools.security import (
    ToolIdentity,
    enforce_tool_rate_limit,
    resolve_tool_identity,
)
from app.modules.voice.tools.service import VoiceToolService

router = APIRouter(
    prefix="/voice",
    tags=["Voice — Bolna Custom Functions"],
)

_TOOL_PREFIX = "/api/v1/voice/tools"


def _tool_response(content: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=content)


async def _invoke_tool(
    request: Request,
    db: Session,
    settings: Settings,
    payload: Any,
    handler: Callable[[ToolIdentity, VoiceToolService], Any],
) -> JSONResponse:
    """Authenticate, rate-limit, execute and answer one tool call.

    The tool envelope is produced here for *every* failure of a tool
    request (auth, validation downstream, domain errors) so the language
    model always reads the same shape.
    """
    authorization: Optional[str] = request.headers.get("authorization")
    x_api_key: Optional[str] = request.headers.get("x-api-key")
    business_id = getattr(payload, "business_id", None)

    try:
        identity = resolve_tool_identity(
            request,
            db,
            business_id=business_id,
            authorization=authorization,
            x_api_key=x_api_key,
            settings=settings,
        )
        enforce_tool_rate_limit(request, identity.business_id, settings)
        service = VoiceToolService(settings, db)
        result = handler(identity, service)
        if inspect.isawaitable(result):
            result = await result
    except AppError as exc:
        return _tool_response(
            tool_error(exc.code.upper(), exc.message),
            status_code=exc.status_code,
        )
    return _tool_response(result)


@router.post(
    "/tools/lead-details",
    status_code=status.HTTP_200_OK,
    summary="Tool: get the lead the conversation is with",
)
async def tool_lead_details(
    request: Request,
    payload: LeadDetailsRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.get_lead_details(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


@router.post(
    "/tools/save-outcome",
    status_code=status.HTTP_200_OK,
    summary="Tool: persist what the conversation established",
)
async def tool_save_outcome(
    request: Request,
    payload: SaveOutcomeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.save_call_outcome(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


@router.post(
    "/tools/qualify-lead",
    status_code=status.HTTP_200_OK,
    summary="Tool: evaluate the qualification answers against the rules",
)
async def tool_qualify_lead(
    request: Request,
    payload: QualifyLeadRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.qualify_lead(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


@router.post(
    "/tools/create-opportunity",
    status_code=status.HTTP_200_OK,
    summary="Tool: open a sales opportunity for the lead",
)
async def tool_create_opportunity(
    request: Request,
    payload: CreateOpportunityRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.create_opportunity(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


@router.post(
    "/tools/schedule-callback",
    status_code=status.HTTP_200_OK,
    summary="Tool: record a callback the customer asked for",
)
async def tool_schedule_callback(
    request: Request,
    payload: ScheduleCallbackRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.schedule_callback(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


@router.post(
    "/tools/crm-sync",
    status_code=status.HTTP_200_OK,
    summary="Tool: push the lead and its outcome to the CRM",
)
async def tool_crm_sync(
    request: Request,
    payload: CrmSyncToolRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    def call(identity: ToolIdentity, service: VoiceToolService):
        return service.sync_lead_to_crm(identity, payload)

    return await _invoke_tool(request, db, settings, payload, call)


# ---------------------------------------------------------------------------
# Bolna Agent Studio definitions
# ---------------------------------------------------------------------------


def _shared_properties() -> Dict[str, Any]:
    """Properties every tool shares (Bolna injects the runtime variables)."""
    return {
        "lead_id": {
            "type": "string",
            "description": "The unique identifier of the lead this conversation belongs to.",
        },
        "execution_id": {
            "type": "string",
            "description": "The Bolna execution id of the live call (auto-injected by Bolna).",
        },
        "call_id": {
            "type": "string",
            "description": "The Callora call id, when the conversation is linked to one.",
        },
    }


def _definition(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: List[str],
    path: str,
    extra_param: Dict[str, str],
) -> Dict[str, Any]:
    param: Dict[str, str] = {
        "lead_id": "%(lead_id)s",
        "execution_id": "%(execution_id)s",
        "call_id": "%(call_id)s",
    }
    param.update(extra_param)
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {**_shared_properties(), **properties},
            "required": required,
        },
        "key": "custom_task",
        "value": {
            "method": "POST",
            "url": path,
            # Placeholder only — set the real value to the deployment's
            # CALLORA_VOICE_TOOL_SECRET in the Bolna dashboard.
            "api_token": "Bearer <CALLORA_VOICE_TOOL_SECRET>",
            "headers": {"Content-Type": "application/json"},
            "param": param,
        },
    }


@router.get(
    "/bolna/custom-functions",
    status_code=status.HTTP_200_OK,
    summary="Bolna Agent Studio definitions of the six custom functions",
)
async def get_bolna_custom_functions(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Emit the Bolna ``custom_task`` definitions for the six tools.

    ``business_id`` is deliberately not a runtime variable (Bolna has
    none for it): in each tool configuration the business fills in its
    own literal id, and the backend verifies it against the token.
    """
    base_url = str(request.base_url).rstrip("/")
    tool_url = lambda slug: f"{base_url}{_TOOL_PREFIX}/{slug}"  # noqa: E731
    configured = bool(settings.voice_tool_secret)

    functions: List[Dict[str, Any]] = [
        _definition(
            "get_lead_details",
            (
                "Retrieve the details of the lead the current conversation "
                "belongs to: name, preferred language, current status, known "
                "qualification answers and the previous call outcome. Call "
                "this at the start of a conversation or when you need to "
                "confirm who you are speaking with or what was already "
                "settled."
            ),
            {},
            ["lead_id"],
            tool_url("lead-details"),
            {"business_id": "%(business_id)s"},
        ),
        _definition(
            "save_call_outcome",
            (
                "Save the result of the conversation on the lead: whether the "
                "customer is interested or not, the answers they gave to the "
                "qualification questions, a short summary, and a callback "
                "request when they asked for one. Call this exactly once, "
                "near the end of the conversation, once the outcome is clear."
            ),
            {
                "outcome": {
                    "type": "string",
                    "enum": [
                        "interested",
                        "not_interested",
                        "callback_requested",
                        "qualified",
                        "unqualified",
                        "no_answer",
                        "busy",
                        "failed",
                    ],
                    "description": "The controlled outcome of this call.",
                },
                "customer_interest": {
                    "type": "string",
                    "enum": ["interested", "maybe", "not_interested", "unknown"],
                    "description": "How the customer signalled their interest.",
                },
                "qualification_answers": {
                    "type": "object",
                    "description": (
                        "The answers the customer actually gave, keyed by the "
                        "question key (for example {\"budget\": 3500000}). "
                        "Never invent a value for a question that was not "
                        "answered."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "A short factual summary of the conversation.",
                },
                "callback_requested": {
                    "type": "boolean",
                    "description": "True when the customer asked to be called back.",
                },
                "callback_time": {
                    "type": "string",
                    "format": "date-time",
                    "description": (
                        "The requested callback time in UTC (ISO 8601), when "
                        "the customer named one."
                    ),
                },
                "callback_timezone": {
                    "type": "string",
                    "description": "The customer's timezone (IANA name), e.g. 'Asia/Kolkata'.",
                },
                "callback_reason": {
                    "type": "string",
                    "description": "Why the customer wants to be called back.",
                },
            },
            ["lead_id", "outcome"],
            tool_url("save-outcome"),
            {"business_id": "%(business_id)s"},
        ),
        _definition(
            "qualify_lead",
            (
                "Evaluate the qualification answers collected during the "
                "conversation against the business's configured rules and "
                "return a rule-based verdict: qualified, not_qualified or "
                "insufficient_data. Call this once all the required "
                "questions have been answered — or to check what is still "
                "missing."
            ),
            {
                "answers": {
                    "type": "object",
                    "description": (
                        "The answers the customer actually gave, keyed by the "
                        "question key. Leave out any question that was not "
                        "answered — the engine preserves unknowns."
                    ),
                },
            },
            ["lead_id"],
            tool_url("qualify-lead"),
            {"business_id": "%(business_id)s", "answers": "%(answers)s"},
        ),
        _definition(
            "create_opportunity",
            (
                "Open a sales opportunity for the lead once the lead passed "
                "the business's qualification rules. Call this only after "
                "qualify_lead returned 'qualified' and the customer confirmed "
                "they want to proceed."
            ),
            {},
            ["lead_id"],
            tool_url("create-opportunity"),
            {"business_id": "%(business_id)s"},
        ),
        _definition(
            "schedule_callback",
            (
                "Record a callback the customer explicitly asked for, at the "
                "time and in the timezone they named. The endpoint checks "
                "consent, do-not-disturb and calling hours before it "
                "confirms. Call this when the customer wants to continue the "
                "conversation at a specific later time."
            ),
            {
                "requested_at": {
                    "type": "string",
                    "format": "date-time",
                    "description": (
                        "The requested callback time. A naive time is "
                        "interpreted in 'timezone'; a time with an explicit "
                        "UTC offset is taken as the absolute moment."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": (
                        "The IANA timezone the customer's requested time is "
                        "expressed in, e.g. 'Asia/Kolkata'."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why the customer wants the callback.",
                },
            },
            ["lead_id", "requested_at", "timezone"],
            tool_url("schedule-callback"),
            {"business_id": "%(business_id)s"},
        ),
        _definition(
            "sync_lead_to_crm",
            (
                "Push the lead and its latest outcome to the business's "
                "connected CRM. Call this once, at the end of a productive "
                "conversation (after save_call_outcome), when the business "
                "has a CRM connection configured."
            ),
            {
                "connection_id": {
                    "type": "string",
                    "description": "The CRM connection to use; 'default' is used when omitted.",
                },
            },
            ["lead_id"],
            tool_url("crm-sync"),
            {"business_id": "%(business_id)s"},
        ),
    ]

    return {
        "functions": functions,
        "base_url": base_url,
        "endpoints": {fn["name"]: fn["value"]["url"] for fn in functions},
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "note": (
                "Set value.api_token to the deployment's "
                "CALLORA_VOICE_TOOL_SECRET (never served here); a business "
                "API key from CALLORA_API_KEYS is also accepted."
            ),
        },
        "business_id_note": (
            "Fill %(business_id)s in each tool's param mapping with the "
            "business's literal Callora business id — Bolna does not inject "
            "it, and the backend verifies it against the token."
        ),
        "configured": configured,
    }
