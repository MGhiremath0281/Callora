"""Provider webhook intake.

``service`` holds the :class:`WebhookService` that verifies, de-duplicates
and applies provider events; the HTTP surface lives in
``app.modules.voice.api.webhooks``.
"""

from app.modules.voice.webhooks.service import (  # noqa: F401
    RESULT_DUPLICATE,
    RESULT_IGNORED,
    RESULT_PROCESSED,
    RESULT_UNMATCHED,
    WebhookResult,
    WebhookService,
)

__all__ = [
    "WebhookService",
    "WebhookResult",
    "RESULT_PROCESSED",
    "RESULT_DUPLICATE",
    "RESULT_UNMATCHED",
    "RESULT_IGNORED",
]
