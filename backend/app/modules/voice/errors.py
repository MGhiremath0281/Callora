"""Domain errors raised by the outbound voice module.

They extend ``app.errors.AppError`` so the shared exception handler renders
them with the documented error envelope:

    {"error": {"code": ..., "message": ..., "details": [...]}}
"""

from typing import Any, Dict, List, Optional

from app.errors import AppError


class VoiceError(AppError):
    """Base class for voice-module errors."""

    status_code = 400
    code = "voice_error"


class BusinessNotFoundError(VoiceError):
    status_code = 404
    code = "business_not_found"

    def __init__(self, business_id: str):
        super().__init__(f"Business '{business_id}' was not found")
        self.business_id = business_id


class CallNotFoundError(VoiceError):
    status_code = 404
    code = "call_not_found"

    def __init__(self, call_id: str):
        super().__init__(f"Call '{call_id}' was not found")
        self.call_id = call_id


class CallOutcomeNotFoundError(VoiceError):
    """The post-call workflow has not produced an outcome yet."""

    status_code = 404
    code = "call_outcome_not_found"

    def __init__(self, call_id: str):
        super().__init__(
            f"The post-call workflow has not produced an outcome for call "
            f"'{call_id}' yet"
        )
        self.call_id = call_id


class HandoffNotFoundError(VoiceError):
    status_code = 404
    code = "handoff_not_found"

    def __init__(self, handoff_id: str):
        super().__init__(f"Handoff '{handoff_id}' was not found")
        self.handoff_id = handoff_id


class JobNotFoundError(VoiceError):
    status_code = 404
    code = "job_not_found"

    def __init__(self, job_id: str):
        super().__init__(f"Job '{job_id}' was not found")
        self.job_id = job_id


class DndEntryNotFoundError(VoiceError):
    status_code = 404
    code = "dnd_entry_not_found"

    def __init__(self, entry_id: str):
        super().__init__(f"DND entry '{entry_id}' was not found")
        self.entry_id = entry_id


class IdempotencyConflictError(VoiceError):
    """The idempotency key was already used with a different payload."""

    status_code = 409
    code = "idempotency_conflict"

    def __init__(self, key: str):
        super().__init__(
            f"The idempotency key '{key}' was already used with a different payload"
        )
        self.key = key


class ComplianceBlockedError(VoiceError):
    """A mandatory compliance check blocked the call.

    ``details`` carries the full per-check breakdown so an operator can see
    exactly which rule failed and why.
    """

    status_code = 403
    code = "compliance_blocked"

    def __init__(self, reasons: List[Dict[str, Any]], decision_id: Optional[str] = None):
        summary = ", ".join(r.get("reason_code", "unknown") for r in reasons) or "unknown"
        super().__init__(
            "the call was blocked by the compliance gate",
            details=reasons,
        )
        self.reasons = reasons
        self.decision_id = decision_id
        self.summary = summary


class ProviderError(VoiceError):
    """The voice provider returned an error."""

    status_code = 502
    code = "voice_provider_error"


class ProviderTimeoutError(VoiceError):
    status_code = 504
    code = "voice_provider_timeout"


class ProviderConnectionError(VoiceError):
    """The provider could not be reached at all (request never sent).

    This is the only provider failure that is safe to retry automatically:
    the provider never saw the request, so no call was placed.
    """

    status_code = 503
    code = "voice_provider_unreachable"


class ProviderNotConfiguredError(VoiceError):
    status_code = 503
    code = "voice_provider_not_configured"


class WebhookSignatureError(VoiceError):
    """The webhook signature could not be verified."""

    status_code = 401
    code = "webhook_signature_invalid"


class WebhookPayloadError(VoiceError):
    """The webhook payload could not be understood."""

    status_code = 400
    code = "webhook_payload_invalid"


class RateLimitExceededError(VoiceError):
    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(self, limit: int, window_seconds: int = 60):
        super().__init__(
            f"rate limit of {limit} requests per {window_seconds}s exceeded",
            details=[{"limit": limit, "window_seconds": window_seconds}],
        )
        self.limit = limit


class LLMUnavailableError(VoiceError):
    """No LLM is configured (or the configured one failed)."""

    status_code = 503
    code = "llm_unavailable"
