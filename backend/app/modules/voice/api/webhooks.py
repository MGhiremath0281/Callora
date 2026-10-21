"""Provider webhook endpoints.

    POST /api/v1/voice/webhooks/bolna/execution      call lifecycle events
    POST /api/v1/voice/webhooks/bolna/pre-transfer   warm-transfer events

These endpoints are **not** protected by the ``X-API-Key`` header: the
caller is the voice provider, not a tenant. They are protected by the
webhook signature instead (HMAC-SHA256 over the raw body when
``CALLORA_BOLNA_WEBHOOK_SECRET`` is configured).

The endpoint always answers with a 2xx for a payload it understood, even
when the event was a duplicate or arrived out of order: the provider must
not retry a delivery that was already recorded. Only a payload that
cannot be parsed, or a signature that does not verify, is rejected.
"""

from typing import Any, Dict, Mapping, Optional

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.modules.voice.errors import WebhookPayloadError, WebhookSignatureError
from app.modules.voice.providers.registry import build_provider
from app.modules.voice.webhooks.service import WebhookService

router = APIRouter(
    prefix="/voice/webhooks/bolna",
    tags=["Voice — Webhooks"],
)


async def _json_body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if not raw:
        raise WebhookPayloadError("the webhook body was empty")
    try:
        import json

        payload = json.loads(raw)
    except ValueError as exc:
        raise WebhookPayloadError(f"the webhook body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WebhookPayloadError("the webhook body must be a JSON object")
    return payload


def _headers(request: Request) -> Mapping[str, str]:
    return {key.lower(): value for key, value in request.headers.items()}


@router.post("/execution", status_code=status.HTTP_200_OK)
async def receive_execution_event(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Handle a Bolna execution event.

    The event is de-duplicated, applied to the call (never backwards) and
    the heavy post-call analysis is queued instead of executed inline.
    """
    raw_body = await request.body()
    payload = await _json_body(request)
    provider = build_provider(settings=settings)
    service = WebhookService(settings, db, provider)
    try:
        result = service.process_execution_event(
            payload, headers=_headers(request), raw_body=raw_body
        )
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    return result.to_dict()


@router.post("/pre-transfer", status_code=status.HTTP_200_OK)
async def receive_pre_transfer_event(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Handle the pre-transfer event Bolna fires before a warm transfer."""
    raw_body = await request.body()
    payload = await _json_body(request)
    provider = build_provider(settings=settings)
    service = WebhookService(settings, db, provider)
    try:
        result = service.process_pre_transfer_event(
            payload, headers=_headers(request), raw_body=raw_body
        )
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    return result.to_dict()


__all__ = ["router", "WebhookSignatureError"]
