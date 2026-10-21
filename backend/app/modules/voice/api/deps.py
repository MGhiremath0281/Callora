"""Shared dependencies for the voice API.

The only authentication mechanism is the ``X-API-Key`` header that
``app.auth.authenticate_business`` resolves into a ``business_id``. Every
voice endpoint is scoped to that business, so a caller can never read or
write another tenant's calls, consent records or handoffs.
"""

from typing import Optional

from fastapi import Depends, Header

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.errors import InvalidParameterError
from app.modules.voice.cache import get_cache
from app.modules.voice.errors import RateLimitExceededError


def current_business(
    business_id: str = Depends(authenticate_business),
) -> str:
    """The business the caller acts on behalf of."""
    return business_id


def optional_idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    """The optional idempotency key of a mutating request."""
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    if not key:
        return None
    if len(key) > 128:
        raise InvalidParameterError(
            "the Idempotency-Key header must not be longer than 128 characters"
        )
    return key


def enforce_call_rate_limit(
    business_id: str = Depends(current_business),
    settings: Settings = Depends(get_settings),
) -> None:
    """Apply the per-business call-initiation rate limit.

    The counter lives in Redis, which is an *advisory* accelerator: when
    Redis is unreachable the request is allowed through and the
    degradation is logged. The limit therefore protects the provider
    quota, never the compliance decision.
    """
    if not settings.redis_enabled:
        return
    cache = get_cache(settings)
    key = cache.key("voice", "ratelimit", "calls", business_id)
    count = cache.incr(key, 60)
    if count is None:
        return
    if count > settings.voice_rate_limit_per_minute:
        raise RateLimitExceededError(settings.voice_rate_limit_per_minute, 60)
