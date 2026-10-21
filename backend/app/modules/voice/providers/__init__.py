"""Voice provider adapters.

``base`` holds the port every adapter implements, ``bolna`` is the
initial provider and ``registry`` resolves a provider by name so the
orchestrator stays provider-agnostic.
"""

from app.modules.voice.providers.base import (  # noqa: F401
    OutboundCallRequest,
    ProviderCallResult,
    ProviderEvent,
    VoiceProvider,
    event_fingerprint,
)
from app.modules.voice.providers.bolna import BolnaProvider  # noqa: F401
from app.modules.voice.providers.registry import (  # noqa: F401
    DEFAULT_PROVIDER,
    build_provider,
    register_provider,
)

__all__ = [
    "OutboundCallRequest",
    "ProviderCallResult",
    "ProviderEvent",
    "VoiceProvider",
    "event_fingerprint",
    "BolnaProvider",
    "DEFAULT_PROVIDER",
    "build_provider",
    "register_provider",
]
