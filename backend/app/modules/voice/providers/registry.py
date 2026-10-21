"""Provider registry.

The orchestrator asks the registry for a provider by name; it never imports
a concrete adapter. Swapping Bolna for another provider therefore only
means adding an entry here.
"""

from typing import Callable, Dict, Optional

from app.config import Settings
from app.modules.voice.providers.base import VoiceProvider
from app.modules.voice.providers.bolna import BolnaProvider

#: name -> factory
_FACTORIES: Dict[str, Callable[[Settings], VoiceProvider]] = {
    BolnaProvider.name: lambda settings: BolnaProvider(settings),
}

#: The provider used when a caller does not name one.
DEFAULT_PROVIDER = BolnaProvider.name


def register_provider(name: str, factory: Callable[[Settings], VoiceProvider]) -> None:
    """Register an additional provider adapter."""
    _FACTORIES[name] = factory


def available_providers() -> Dict[str, Callable[[Settings], VoiceProvider]]:
    return dict(_FACTORIES)


def build_provider(name: Optional[str] = None, settings: Optional[Settings] = None) -> VoiceProvider:
    """Instantiate a provider adapter by name."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    key = (name or DEFAULT_PROVIDER).strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        raise KeyError(f"unknown voice provider '{key}'")
    return factory(settings)
