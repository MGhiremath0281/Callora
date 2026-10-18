"""Business-level API key authentication.

Every Module 1 endpoint is scoped to one business. The current
implementation resolves keys from configuration
(``CALLORA_API_KEYS="biz_001:key1,biz_002:key2"``) and is designed to be
swapped for a database-backed key store without touching route code.
"""

from typing import Optional

from fastapi import Header

from app.config import get_settings
from app.errors import AuthenticationError


def authenticate_business(x_api_key: Optional[str] = Header(default=None)) -> str:
    """Resolve the authenticated ``business_id`` from the API key header."""
    if not x_api_key:
        raise AuthenticationError("an X-API-Key header is required")
    for business_id, key in get_settings().api_key_map.items():
        if _constant_time_equals(key, x_api_key):
            return business_id
    raise AuthenticationError("the provided API key is not valid")


def _constant_time_equals(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())
