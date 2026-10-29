"""Authentication for the Bolna custom functions.

Bolna calls the tool endpoint server-to-server. The documented mechanism
its custom functions support is a fixed token in the request
(``value.api_token``), which arrives as an ``Authorization`` header
(``Bearer <token>``) — so that is what Callora verifies:

* a token matching a **business API key** (``CALLORA_API_KEYS``) resolves
  to that business — the same credentials the rest of the platform uses;
* a token matching the shared **voice tool secret**
  (``CALLORA_VOICE_TOOL_SECRET``) identifies the Bolna runtime; the
  ``business_id`` in the body is then *resolved and verified*, never
  trusted blindly.

Either way the resolved business must exist in the voice configuration
(``businesses``) and be active. A ``business_id`` supplied by the model is
cross-checked against the authenticated business — a mismatch is a
cross-business access attempt, and when the request carries a provider
execution id the persisted call's business is authoritative.

The integration is off unless a token matches: an unconfigured or empty
secret means every tool request is rejected (401), so the application
keeps running safely with the Bolna integration disabled.
"""

import hmac
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, Request

from app.config import Settings, get_settings
from app.errors import AuthenticationError
from app.modules.voice import repository as repo
from app.modules.voice.cache import get_cache
from app.modules.voice.errors import CrossBusinessAccessError, RateLimitExceededError
from sqlalchemy.orm import Session


@dataclass
class ToolIdentity:
    """Who the tool call acts as."""

    business_id: str
    #: "api_key" — a business's own key; "tool_secret" — the shared Bolna
    #: runtime secret (the model-supplied business_id was verified).
    method: str


def _constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _extract_token(
    authorization: Optional[str], x_api_key: Optional[str]
) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.strip():
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token or None
    return None


def resolve_tool_identity(
    request: Request,
    db: Session,
    business_id: Optional[str],
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> ToolIdentity:
    """Authenticate a tool request and resolve its authorized business.

    Raises ``AuthenticationError`` (401) when no token is supplied or no
    token matches, and ``CrossBusinessAccessError`` (403) when the
    requested business does not match the token or the business does not
    exist / is deactivated.
    """
    settings = settings or get_settings()
    token = _extract_token(authorization, x_api_key)
    if not token:
        raise AuthenticationError(
            "an Authorization bearer token is required for this endpoint"
        )

    matched_business: Optional[str] = None
    method = "api_key"
    for candidate_business, candidate_key in settings.api_key_map.items():
        if _constant_time_equals(candidate_key, token):
            matched_business = candidate_business
            break

    if matched_business is None:
        secret = settings.voice_tool_secret
        if secret and _constant_time_equals(secret, token):
            method = "tool_secret"
            if not (business_id and business_id.strip()):
                raise AuthenticationError(
                    "business_id is required when authenticating with the "
                    "shared voice tool secret"
                )
            matched_business = business_id.strip()

    if matched_business is None:
        raise AuthenticationError("the provided credentials are not valid")

    if business_id and business_id.strip() and business_id.strip() != matched_business:
        raise CrossBusinessAccessError(
            f"the request scoped itself to business '{business_id.strip()}' "
            f"while the credentials belong to '{matched_business}'"
        )

    business = repo.get_business(db, matched_business)
    if business is None:
        raise CrossBusinessAccessError(
            f"business '{matched_business}' has no voice configuration"
        )
    if not business.is_active:
        raise CrossBusinessAccessError(f"business '{matched_business}' is deactivated")
    return ToolIdentity(business_id=matched_business, method=method)


def verify_call_context(
    db: Session,
    identity: ToolIdentity,
    execution_id: Optional[str],
    call_id: Optional[str],
) -> Optional[object]:
    """Resolve the persisted call of the conversation, if any.

    When the request carries an execution id that matches a stored call,
    that call's business is authoritative: a tool call claiming another
    business is rejected. Returns ``None`` when the conversation is not
    linked to a Callora-initiated call (an inbound call, for example) —
    the request then still acts inside its authenticated business only.
    """
    if not execution_id and not call_id:
        return None
    call = None
    if execution_id:
        call = repo.get_call_by_provider_id(db, "bolna", execution_id)
        if call is None:
            from app.modules.voice.models import Call

            call = (
                db.query(Call)
                .filter(Call.provider_call_id == str(execution_id))
                .first()
            )
    if call is None and call_id:
        from app.modules.voice.models import Call

        call = db.query(Call).filter(Call.id == str(call_id)).first()
    if call is not None and call.business_id != identity.business_id:
        raise CrossBusinessAccessError(
            "the call belongs to another business"
        )
    return call


def enforce_tool_rate_limit(
    request: Request,
    business_id: str,
    settings: Optional[Settings] = None,
) -> None:
    """Advisory per-business rate limit for the tool endpoints.

    Same contract as the call-initiation limiter: Redis is an accelerator,
    an unreachable Redis never blocks a request, and the limit protects
    the backend from a runaway agent — never a compliance decision.
    """
    settings = settings or get_settings()
    if not settings.redis_enabled:
        return
    cache = get_cache(settings)
    key = cache.key("voice", "ratelimit", "tools", business_id)
    count = cache.incr(key, 60)
    if count is None:
        return
    if count > settings.voice_tool_rate_limit_per_minute:
        raise RateLimitExceededError(settings.voice_tool_rate_limit_per_minute, 60)


__all__ = [
    "ToolIdentity",
    "enforce_tool_rate_limit",
    "resolve_tool_identity",
    "verify_call_context",
]
