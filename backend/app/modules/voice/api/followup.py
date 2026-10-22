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
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Validate that the caller is authorized for the requested business_id."""
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
        if matched_biz != payload.business_id:
            raise CrossBusinessAccessError(
                f"Authenticated as '{matched_biz}' but request was scoped to '{payload.business_id}'"
            )
        return matched_biz

    # If no header key provided, require key unless debug or testing without configured keys
    if settings.api_key_map and not settings.debug:
        raise AuthenticationError("an X-API-Key header is required")

    return payload.business_id


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
    """Return the OpenAI-compatible custom function tool definition for Bolna agents.

    Provides the schema and api_config that Bolna agents use to trigger
    POST /api/v1/voice/answer-question mid-call.
    """
    base_url = str(request.base_url).rstrip("/")
    endpoint_url = f"{base_url}/api/v1/voice/answer-question"

    tool_schema = {
        "type": "function",
        "function": {
            "name": "answer_customer_question",
            "description": (
                "Answers customer follow-up questions during a live sales call using verified "
                "business information, pricing, product features, eligibility, policies, and "
                "objection handling. Invoke this when the customer asks for details about the "
                "product, prices, terms, or asks for human assistance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "business_id": {
                        "type": "string",
                        "description": "The unique identifier of the business.",
                    },
                    "call_id": {
                        "type": "string",
                        "description": "The unique identifier of the current active call.",
                    },
                    "question": {
                        "type": "string",
                        "description": "The exact question or statement from the customer.",
                    },
                    "language": {
                        "type": "string",
                        "description": "The language code of the conversation (e.g., 'en', 'hi', 'kn').",
                        "default": "en",
                    },
                },
                "required": ["business_id", "call_id", "question"],
            },
        },
        "api_config": {
            "url": endpoint_url,
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-API-Key": "<YOUR_CALLORA_API_KEY>",
            },
        },
    }

    return {"tool": tool_schema}
