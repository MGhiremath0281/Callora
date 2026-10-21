"""Redis-backed temporary state, rate limiting and caching.

Redis is an *accelerator* in this module, never an authority:

* the DND registry, the consent records and the call history live in
  PostgreSQL and are always queried there for compliance decisions;
* rate-limit counters are advisory — when Redis is unreachable the request
  is allowed through and the degradation is logged;
* temporary call state is short-lived and reconstructible from the call row.

Every method degrades to a safe no-op when Redis is unavailable, so the
backend keeps working (slower) without a cache. A missing Redis must never
turn a compliance block into an approval — that is why no compliance
decision is ever taken from a cache alone.
"""

import logging
import threading
from typing import Any, Optional

from app.config import Settings

logger = logging.getLogger("callora.voice.cache")


class RedisCache:
    """Thin, failure-tolerant wrapper around ``redis.Redis``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._disabled = not settings.redis_enabled
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def _client_or_none(self) -> Any:
        if self._disabled:
            return None
        with self._lock:
            if self._client is None:
                try:
                    import redis  # imported lazily: Redis is optional
                except ImportError:  # pragma: no cover - dependency missing
                    logger.warning("redis package is not installed; cache disabled")
                    self._disabled = True
                    return None
                try:
                    client = redis.Redis.from_url(
                        self._settings.redis_url,
                        socket_timeout=self._settings.redis_socket_timeout_seconds,
                        socket_connect_timeout=self._settings.redis_socket_timeout_seconds,
                        decode_responses=True,
                    )
                    client.ping()
                    self._client = client
                    logger.info("redis cache connected")
                except Exception as exc:  # pragma: no cover - env dependent
                    logger.warning("redis unavailable, continuing without cache: %s", exc)
                    self._disabled = True
                    return None
            return self._client

    def ping(self) -> bool:
        client = self._client_or_none()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except Exception:  # pragma: no cover - env dependent
            return False

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # pragma: no cover
                    pass
                self._client = None

    # -- key helpers -------------------------------------------------------

    def key(self, *parts: str) -> str:
        prefix = self._settings.redis_key_prefix
        return ":".join([prefix, *[str(p) for p in parts]])

    # -- operations --------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        client = self._client_or_none()
        if client is None:
            return None
        try:
            return client.get(key)
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("redis GET failed for %s: %s", key, exc)
            return None

    def setex(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> bool:
        client = self._client_or_none()
        if client is None:
            return False
        ttl = ttl_seconds if ttl_seconds is not None else self._settings.voice_cache_ttl_seconds
        try:
            client.setex(key, ttl, value)
            return True
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("redis SETEX failed for %s: %s", key, exc)
            return False

    def delete(self, *keys: str) -> int:
        client = self._client_or_none()
        if client is None or not keys:
            return 0
        try:
            return int(client.delete(*keys))
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("redis DELETE failed: %s", exc)
            return 0

    def incr(self, key: str, ttl_seconds: int = 60) -> Optional[int]:
        """Increment a counter, applying the TTL on first use.

        Returns ``None`` when Redis is unavailable so callers can tell
        "not counted" from "counted as 1".
        """
        client = self._client_or_none()
        if client is None:
            return None
        try:
            value = int(client.incr(key))
            if value == 1:
                client.expire(key, ttl_seconds)
            return value
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("redis INCR failed for %s: %s", key, exc)
            return None

    def setnx(self, key: str, value: str, ttl_seconds: int = 30) -> bool:
        """Acquire a short-lived lock. ``False`` means the lock was held."""
        client = self._client_or_none()
        if client is None:
            return True  # no lock service: do not block the caller
        try:
            return bool(client.set(key, value, nx=True, ex=ttl_seconds))
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("redis SETNX failed for %s: %s", key, exc)
            return True

    def set_json(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        import json

        try:
            return self.setex(key, json.dumps(value, default=str), ttl_seconds)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return False

    def get_json(self, key: str) -> Any:
        import json

        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return None


#: Process-wide cache instance. Redis connections are created lazily.
_cache: Optional[RedisCache] = None
_cache_lock = threading.Lock()


def get_cache(settings: Optional[Settings] = None) -> RedisCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            if settings is None:
                from app.config import get_settings

                settings = get_settings()
            _cache = RedisCache(settings)
        return _cache


def reset_cache() -> None:
    """Drop the cached client (used by tests and on settings changes)."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            _cache.close()
        _cache = None
