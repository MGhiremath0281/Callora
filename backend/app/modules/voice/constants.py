"""Canonical vocabulary shared by the whole voice module.

Every provider status is mapped onto the internal call status vocabulary
below. The mapping is one-way (provider -> internal) so that the internal
state machine stays stable even when a provider adds new status strings.
"""

from typing import Dict, List

# ---------------------------------------------------------------------------
# Call status vocabulary
# ---------------------------------------------------------------------------


class CallStatus:
    """Internal call lifecycle states."""

    BLOCKED = "blocked"          # never left the system (compliance gate)
    SIMULATED = "simulated"      # allowed, but outbound calling is disabled
    PENDING = "pending"          # accepted, provider not contacted yet
    SCHEDULED = "scheduled"      # accepted for a future time slot
    QUEUED = "queued"            # provider accepted the call
    INITIATED = "initiated"      # provider dialled out
    RINGING = "ringing"          # remote side is ringing
    IN_PROGRESS = "in_progress"  # conversation running
    DISCONNECTED = "disconnected"  # line dropped, finalisation pending
    COMPLETED = "completed"      # conversation finished successfully
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    CANCELED = "canceled"
    STOPPED = "stopped"
    ERROR = "error"
    BALANCE_LOW = "balance_low"


#: Statuses that mean "the call is over"; a post-call job is queued for each.
TERMINAL_STATUSES = frozenset(
    {
        CallStatus.COMPLETED,
        CallStatus.NO_ANSWER,
        CallStatus.BUSY,
        CallStatus.FAILED,
        CallStatus.CANCELED,
        CallStatus.STOPPED,
        CallStatus.ERROR,
        CallStatus.BALANCE_LOW,
    }
)

#: Statuses that mean a call attempt was actually placed (used for quota
#: accounting). ``blocked`` and ``simulated`` never count.
ATTEMPTED_STATUSES = frozenset(
    {
        CallStatus.QUEUED,
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
        CallStatus.DISCONNECTED,
        CallStatus.COMPLETED,
        CallStatus.NO_ANSWER,
        CallStatus.BUSY,
        CallStatus.FAILED,
        CallStatus.CANCELED,
        CallStatus.STOPPED,
        CallStatus.ERROR,
        CallStatus.BALANCE_LOW,
        CallStatus.SCHEDULED,
    }
)

#: Statuses that mean a call is currently in flight.
IN_FLIGHT_STATUSES = frozenset(
    {
        CallStatus.PENDING,
        CallStatus.SCHEDULED,
        CallStatus.QUEUED,
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
        CallStatus.DISCONNECTED,
    }
)

#: Monotonic ordering of the call lifecycle. Used to reject out-of-order
#: webhook events: an event may never move a call backwards.
STATUS_RANK: Dict[str, int] = {
    CallStatus.BLOCKED: 0,
    CallStatus.SIMULATED: 0,
    CallStatus.PENDING: 1,
    CallStatus.SCHEDULED: 2,
    CallStatus.QUEUED: 3,
    CallStatus.INITIATED: 4,
    CallStatus.RINGING: 5,
    CallStatus.IN_PROGRESS: 6,
    CallStatus.DISCONNECTED: 7,
    CallStatus.NO_ANSWER: 8,
    CallStatus.BUSY: 8,
    CallStatus.FAILED: 8,
    CallStatus.CANCELED: 8,
    CallStatus.STOPPED: 8,
    CallStatus.ERROR: 8,
    CallStatus.BALANCE_LOW: 8,
    CallStatus.COMPLETED: 9,
}

#: Bolna execution statuses -> internal call status.
BOLNA_STATUS_MAP: Dict[str, str] = {
    "scheduled": CallStatus.SCHEDULED,
    "queued": CallStatus.QUEUED,
    "rescheduled": CallStatus.SCHEDULED,
    "initiated": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    "call-disconnected": CallStatus.DISCONNECTED,
    "completed": CallStatus.COMPLETED,
    "no-answer": CallStatus.NO_ANSWER,
    "no_answer": CallStatus.NO_ANSWER,
    "busy": CallStatus.BUSY,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELED,
    "cancelled": CallStatus.CANCELED,
    "stopped": CallStatus.STOPPED,
    "error": CallStatus.ERROR,
    "balance-low": CallStatus.BALANCE_LOW,
    "balance_low": CallStatus.BALANCE_LOW,
}


def map_provider_status(provider_status: str) -> str:
    """Map a provider status onto the internal vocabulary.

    Unknown provider statuses become ``in_progress`` only when they look
    like an active call; otherwise they are treated as ``error`` so that an
    unknown terminal state can never silently look successful.
    """
    if not provider_status:
        return CallStatus.PENDING
    key = provider_status.strip().lower()
    if key in BOLNA_STATUS_MAP:
        return BOLNA_STATUS_MAP[key]
    if key in {"in-progress", "in_progress", "active", "ringing"}:
        return CallStatus.IN_PROGRESS
    return CallStatus.ERROR


# ---------------------------------------------------------------------------
# Compliance vocabulary
# ---------------------------------------------------------------------------


class ComplianceDecision:
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class CheckOutcome:
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class ReasonCode:
    """Stable, machine-readable reasons for a compliance decision."""

    # configuration / tenancy
    BUSINESS_NOT_FOUND = "business_not_found"
    BUSINESS_INACTIVE = "business_inactive"
    LEAD_NOT_FOUND = "lead_not_found"
    LEAD_PHONE_INVALID = "lead_phone_invalid"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"

    # consent
    CONSENT_MISSING = "consent_missing"
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_EXPIRED = "consent_expired"
    CONSENT_CHANNEL_MISMATCH = "consent_channel_mismatch"

    # do-not-disturb
    DND_ACTIVE = "dnd_active"
    DND_LEAD_FLAG = "dnd_lead_flag"
    DND_PROVIDER_UNAVAILABLE = "dnd_provider_unavailable"

    # calling window
    OUTSIDE_CALLING_HOURS = "outside_calling_hours"
    CALLING_DAY_BLOCKED = "calling_day_blocked"

    # duplicate / quota
    DUPLICATE_CALL_IN_FLIGHT = "duplicate_call_in_flight"
    DUPLICATE_CALL_COOLDOWN = "duplicate_call_cooldown"
    DAILY_LEAD_QUOTA_EXCEEDED = "daily_lead_quota_exceeded"
    DAILY_BUSINESS_QUOTA_EXCEEDED = "daily_business_quota_exceeded"
    CONCURRENCY_LIMIT_REACHED = "concurrency_limit_reached"

    # availability of the check itself
    CHECK_UNAVAILABLE = "check_unavailable"


#: Checks that must pass before a call may be placed. Every one of them
#: fails closed: an error inside a check blocks the call.
MANDATORY_CHECKS: List[str] = [
    "business_active",
    "lead_exists",
    "consent",
    "dnd",
    "calling_hours",
    "duplicate_call",
    "quota",
    "concurrency",
    "provider_ready",
]


# ---------------------------------------------------------------------------
# Consent / DND vocabulary
# ---------------------------------------------------------------------------


class ConsentStatus:
    GRANTED = "granted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ConsentChannel:
    VOICE = "voice"
    SMS = "sms"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class DndScope:
    BUSINESS = "business"
    GLOBAL = "global"


# ---------------------------------------------------------------------------
# Post-call / handoff vocabulary
# ---------------------------------------------------------------------------


class Disposition:
    """Top-level outcome of a conversation."""

    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    REFUSED = "refused"
    CALLBACK_REQUESTED = "callback_requested"
    NO_CONVERSATION = "no_conversation"
    UNKNOWN = "unknown"


class RefusalCategory:
    NOT_INTERESTED = "not_interested"
    ALREADY_CUSTOMER = "already_customer"
    WRONG_NUMBER = "wrong_number"
    TOO_EARLY = "too_early"
    PRICE = "price"
    COMPETITOR = "competitor"
    NO_BUDGET = "no_budget"
    CALLBACK_LATER = "callback_later"
    DO_NOT_CONTACT = "do_not_contact"
    LANGUAGE_BARRIER = "language_barrier"
    OTHER = "other"


class HandoffKind:
    WARM_TRANSFER = "warm_transfer"
    CALLBACK = "callback"


class HandoffStatus:
    REQUESTED = "requested"
    INITIATED = "initiated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_SUPPORTED = "not_supported"


class JobType:
    POSTCALL_PROCESS = "postcall.process"


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class AuditAction:
    CALL_REQUESTED = "call.requested"
    CALL_BLOCKED = "call.blocked"
    CALL_INITIATED = "call.initiated"
    CALL_PROVIDER_ERROR = "call.provider_error"
    CALL_STATUS_UPDATED = "call.status_updated"
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_REJECTED = "webhook.rejected"
    WEBHOOK_DUPLICATE = "webhook.duplicate"
    POSTCALL_COMPLETED = "postcall.completed"
    POSTCALL_FAILED = "postcall.failed"
    HANDOFF_REQUESTED = "handoff.requested"
    HANDOFF_COMPLETED = "handoff.completed"
    CONSENT_RECORDED = "consent.recorded"
    DND_RECORDED = "dnd.recorded"
    JOB_RETRIED = "job.retried"
    BUSINESS_CONFIG_UPDATED = "business.config_updated"
    CALL_CANCELED = "call.canceled"


#: Languages the voice pipeline is expected to handle. The language is a
#: hint to the provider; the pipeline itself decides how well it can
#: code-switch.
SUPPORTED_LANGUAGES = ("en", "hi", "kn")

#: Human readable names used in prompts and briefings.
LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "kn": "Kannada",
}
