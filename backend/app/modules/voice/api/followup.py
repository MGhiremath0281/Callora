"""Follow-up Question API and Bolna tool integration endpoints.

    POST /api/v1/voice/answer-question
    GET  /api/v1/voice/bolna/tool-definition

Allows Bolna AI voice agents to query verified business information mid-call,
maintain conversation memory, and seamlessly continue sales qualification.
"""

import hmac
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import AuthenticationError
from app.modules.voice.errors import CrossBusinessAccessError
from app.modules.voice.knowledge.service import FollowUpService
from app.modules.voice.schemas import (
    BolnaToolDefinitionResponse,
    FollowUpQuestionRequest,
    FollowUpQuestionResponse,
)

router = APIRouter(
    prefix="/voice",
    tags=["Voice — Follow-Up Questions"],
)


def _constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_business_authorization(
    payload: FollowUpQuestionRequest,
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Validate that the caller is authorized for the requested business_id."""
    from app.modules.voice import repository as repo
    from app.modules.voice.models import Call

    # 1. Resolve business_id if omitted
    if not payload.business_id:
        if payload.call_id:
            call = db.query(Call).filter(Call.id == payload.call_id).first()
            if call:
                payload.business_id = call.business_id
        elif payload.execution_id:
            call = repo.get_call_by_provider_id(db, "bolna", payload.execution_id)
            if call:
                payload.call_id = call.id
                payload.business_id = call.business_id

    # 2. Check tenant X-API-Key / Authorization header
    key = x_api_key
    if not key and authorization:
        if authorization.lower().startswith("bearer "):
            key = authorization[7:].strip()
        else:
            key = authorization.strip()

    if key:
        matched_biz = None
        for b_id, b_key in settings.api_key_map.items():
            if _constant_time_equals(b_key, key):
                matched_biz = b_id
                break
        if not matched_biz:
            raise AuthenticationError("the provided API key is not valid")
        if payload.business_id and matched_biz != payload.business_id:
            raise CrossBusinessAccessError(
                f"Authenticated as '{matched_biz}' but request was scoped to '{payload.business_id}'"
            )
        return matched_biz

    # 3. Check for voice provider headers or execution signatures
    req_headers = {k.lower(): v for k, v in request.headers.items()}
    has_provider_sig = any(
        h in req_headers
        for h in ("x-bolna-signature", "x-webhook-signature", "x-hub-signature-256", "x-signature")
    )
    if has_provider_sig or payload.execution_id:
        if payload.business_id:
            return payload.business_id

    # If no header key provided, require key unless debug or testing without configured keys
    if settings.api_key_map and not settings.debug:
        raise AuthenticationError("an X-API-Key header is required")

    return payload.business_id or ""


@router.post(
    "/answer-question",
    response_model=FollowUpQuestionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "Invalid call context or missing parameters."},
        401: {"description": "Missing or invalid API key."},
        403: {"description": "Cross-business access denied."},
        404: {"description": "Business or active call not found."},
    },
)
async def answer_followup_question(
    payload: FollowUpQuestionRequest,
    _authorized_business: str = Depends(verify_business_authorization),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FollowUpQuestionResponse:
    """Answer a customer follow-up question during a live voice call.

    Validates business and call context, retrieves verified business knowledge,
    evaluates AI guardrails, updates conversational memory, and returns a grounded
    answer with qualification continuation and escalation recommendations.
    """
    from app.modules.voice import repository as repo
    from app.modules.voice.models import Call

    # Ensure business_id and call_id are populated
    if not payload.business_id and payload.call_id:
        call = db.query(Call).filter(Call.id == payload.call_id).first()
        if call:
            payload.business_id = call.business_id
    elif not payload.call_id and payload.execution_id:
        call = repo.get_call_by_provider_id(db, "bolna", payload.execution_id)
        if call:
            payload.call_id = call.id
            payload.business_id = call.business_id

    service = FollowUpService(settings=settings, session=db)
    return service.answer_question(payload)


@router.get(
    "/bolna/tool-definition",
    response_model=BolnaToolDefinitionResponse,
    status_code=status.HTTP_200_OK,
)
async def get_bolna_tool_definition(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Return the tool configuration for Bolna AI voice agents.

    Provides both OpenAI-compatible function definition and Bolna native
    custom task schema connecting directly to Callora mid-call answering.
    """
    base_url = str(request.base_url).rstrip("/")
    endpoint_url = f"{base_url}/api/v1/voice/answer-question"
    webhook_url = f"{base_url}/api/v1/voice/webhooks/bolna/followup"

    openai_tool_schema = {
        "type": "function",
        "function": {
            "name": "answer_customer_question",
            "description": (
                "Answers customer follow-up questions during a live sales call using verified "
                "business information, properties, pricing, product features, eligibility, policies, and "
                "objection handling. Invoke this whenever the customer asks a question about the "
                "product, properties, prices, terms, or asks for human assistance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The exact question or statement from the customer.",
                    },
                    "language": {
                        "type": "string",
                        "description": "The language code of the conversation (e.g., 'en', 'hi', 'kn').",
                        "default": "en",
                    },
                    "business_id": {
                        "type": "string",
                        "description": "The unique identifier of the business (optional, resolved automatically).",
                    },
                    "call_id": {
                        "type": "string",
                        "description": "The unique identifier of the current active call (optional, resolved automatically).",
                    },
                },
                "required": ["question"],
            },
        },
        "api_config": {
            "url": endpoint_url,
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
            },
        },
    }

    bolna_custom_task = {
        "name": "answer_customer_question",
        "description": (
            "Answers customer questions regarding business pricing, properties, product features, "
            "eligibility, policies, and objection handling during a live call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The exact question asked by the customer.",
                },
                "language": {
                    "type": "string",
                    "description": "Language code (e.g. en, hi)",
                    "default": "en",
                },
            },
            "required": ["question"],
        },
        "key": "custom_task",
        "value": {
            "method": "POST",
            "url": webhook_url,
            "headers": {
                "Content-Type": "application/json",
            },
            "param": {
                "question": "%(question)s",
                "language": "%(language)s",
                "call_id": "%(call_id)s",
                "business_id": "%(business_id)s",
                "execution_id": "%(execution_id)s",
            },
        },
    }

    return {
        "tool": openai_tool_schema,
        "bolna_custom_task": bolna_custom_task,
        "webhook_url": webhook_url,
        "api_url": endpoint_url,
    }
