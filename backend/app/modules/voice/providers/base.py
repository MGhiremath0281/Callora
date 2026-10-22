"""The voice-provider port.

Everything the orchestrator needs from a telephony/voice provider is
expressed here. The orchestrator never imports a provider implementation, so
adding Plivo (or any other provider) later is a matter of writing a new
adapter — the call lifecycle, compliance gate and webhook handling stay
untouched.
"""

import abc
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from app.modules.voice.constants import CallStatus


@dataclass
class OutboundCallRequest:
    """A provider-agnostic request to place one outbound call."""

    to_number: str
    agent_id: str
    from_number: Optional[str] = None
    language: Optional[str] = None
    # Variables injected into the agent prompt / welcome message.
    user_data: Dict[str, Any] = field(default_factory=dict)
    scheduled_at: Optional[datetime] = None
    # Per-call provider overrides (voice id, ...).
    agent_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderCallResult:
    """The outcome of contacting a provider."""

    provider: str
    execution_id: Optional[str]
    status: str
    raw: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None


@dataclass
class ProviderEvent:
    """A normalized provider event ready to be persisted."""

    provider: str
    event_key: str
    event_type: str
    execution_id: Optional[str] = None
    status: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    signature_valid: Optional[bool] = None


def canonical_json(payload: Any) -> str:
    """Stable JSON representation used for event fingerprints."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def event_fingerprint(provider: str, execution_id: Optional[str], status: Optional[str], payload: Any) -> str:
    """Deterministic fingerprint of a provider event.

    Two deliveries of the same event produce the same fingerprint, which is
    what makes webhook handling idempotent. The payload is part of the
    fingerprint because Bolna sends progressively richer payloads for the
    same status (for example ``call-disconnected`` first without, then
    with, a transcript).
    """
    digest = hashlib.sha256()
    digest.update(provider.encode())
    digest.update(b"|")
    digest.update((execution_id or "").encode())
    digest.update(b"|")
    digest.update((status or "").encode())
    digest.update(b"|")
    digest.update(canonical_json(payload).encode())
    return digest.hexdigest()


class VoiceProvider(abc.ABC):
    """Adapter interface implemented by every voice provider."""

    #: Stable provider identifier persisted on every call record.
    name: str = "base"

    @abc.abstractmethod
    def start_call(self, request: OutboundCallRequest) -> ProviderCallResult:
        """Place an outbound call and return the provider's handle."""

    @abc.abstractmethod
    def get_call(self, execution_id: str) -> ProviderCallResult:
        """Fetch the current state of a call."""

    @abc.abstractmethod
    def stop_call(self, execution_id: str) -> ProviderCallResult:
        """Cancel a call that is still queued or scheduled."""

    @abc.abstractmethod
    def normalize_event(
        self, payload: Mapping[str, Any], headers: Optional[Mapping[str, str]] = None
    ) -> ProviderEvent:
        """Convert a raw provider payload into a normalized event."""

    def verify_signature(
        self, headers: Optional[Mapping[str, str]], raw_body: bytes
    ) -> Optional[bool]:
        """Verify a webhook signature.

        Returns ``True``/``False`` when the provider supports signed
        deliveries, and ``None`` when the provider does not support
        signatures at all (in which case the caller falls back to network
        level controls).
        """
        return None

    def is_configured(self) -> bool:
        """Whether the provider has everything it needs to place a call."""
        return True

    def to_internal_status(self, provider_status: Optional[str]) -> str:
        from app.modules.voice.constants import map_provider_status

        return map_provider_status(provider_status or "")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} name={self.name!r}>"


#: Internal status used when a provider reports a status the adapter does
#: not know. It is deliberately *not* terminal: a status Callora has
#: never seen says nothing about whether the call is over, so the call
#: keeps its status and the reconciliation sweep resolves it by polling
#: the provider.
UNKNOWN_PROVIDER_STATUS = CallStatus.UNKNOWN
