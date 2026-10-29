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
    # The provider reported a status Callora does not know. It is never
    # written to a call: an unknown status says nothing about whether the
    # call is over, so the call keeps its status and the reconciliation
    # sweep resolves it by polling the provider.
    UNKNOWN = "unknown"


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
#:
#: This is the set the duplicate-call guard and the concurrency cap work
#: on, so it only contains states in which a call really is pending or
#: running. A terminal status is not in it — a finished call releases the
#: guard — and neither is ``disconnected``: the line is down, Bolna is
#: only finalising the payload, and the call no longer occupies the
#: business' concurrency budget. The cooldown rule still applies to it.
IN_FLIGHT_STATUSES = frozenset(
    {
        CallStatus.PENDING,
        CallStatus.SCHEDULED,
        CallStatus.QUEUED,
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
    }
)

#: Statuses in which the provider has accepted the call but has not dialled
#: out yet. No conversation is taking place, so a call that stays in one of
#: these states past the documented timeout can be failed without ever
#: cutting a live conversation short.
PRE_DIAL_STATUSES = frozenset(
    {
        CallStatus.PENDING,
        CallStatus.SCHEDULED,
        CallStatus.QUEUED,
    }
)

#: Statuses in which a conversation is genuinely taking place. A call in
#: one of these states is never failed because time passed: only the
#: provider can end it.
ACTIVE_STATUSES = frozenset(
    {
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
    }
)

#: Statuses the reconciliation sweep is allowed to poll the provider for.
RECONCILABLE_STATUSES = frozenset(
    PRE_DIAL_STATUSES | ACTIVE_STATUSES | {CallStatus.DISCONNECTED}
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
#:
#: The list mirrors Bolna's own status enum (see the Bolna "Errors &
#: status codes" reference): ``scheduled``/``prepared`` -> ``queued`` ->
#: ``initiated`` -> ``ringing`` -> ``in-progress`` ->
#: ``call-disconnected`` -> ``completed``, plus the hard failures.
BOLNA_STATUS_MAP: Dict[str, str] = {
    "scheduled": CallStatus.SCHEDULED,
    # "Execution record created and validated but not yet handed off to
    # the dial queue" — accepted, not dialled.
    "prepared": CallStatus.QUEUED,
    "queued": CallStatus.QUEUED,
    "rescheduled": CallStatus.SCHEDULED,
    "initiated": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    # Some providers (and Bolna's older payloads) name the connected
    # state explicitly.
    "connected": CallStatus.IN_PROGRESS,
    "active": CallStatus.IN_PROGRESS,
    "call-disconnected": CallStatus.DISCONNECTED,
    "disconnected": CallStatus.DISCONNECTED,
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
    # The internal vocabulary maps onto itself, so mapping an already
    # mapped status is idempotent. That matters because the provider
    # adapters hand out the internal status and the raw provider status
    # at the same time.
    "pending": CallStatus.PENDING,
    "blocked": CallStatus.BLOCKED,
    "simulated": CallStatus.SIMULATED,
    "unknown": CallStatus.UNKNOWN,
}


def map_provider_status(provider_status: str) -> str:
    """Map a provider status onto the internal vocabulary.

    An unknown status maps to :data:`CallStatus.UNKNOWN` and nothing else.
    It used to map to ``error``, which was wrong in both directions: a
    status Callora had never seen terminated a call that was possibly
    still running, released the duplicate-call guard and queued the
    post-call analysis for a conversation that had not happened. An
    unknown status says nothing about whether the call is over, so the
    state machine leaves the call where it is and the reconciliation
    sweep resolves it by polling the provider.
    """
    if not provider_status:
        return CallStatus.UNKNOWN
    key = provider_status.strip().lower()
    if key in BOLNA_STATUS_MAP:
        return BOLNA_STATUS_MAP[key]
    return CallStatus.UNKNOWN


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
    #: Reconcile the status of calls the provider has not reported on.
    STATUS_SYNC = "status.sync"
    #: Dispatch a requested callback through the compliance gate when it
    #: becomes due. Only created when automatic callback dispatch is
    #: enabled for the deployment.
    CALLBACK_DISPATCH = "callback.dispatch"


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
    CALL_STATUS_SYNCED = "call.status_synced"
    CALL_TIMEOUT = "call.timeout"
    POSTCALL_COMPLETED = "postcall.completed"
    POSTCALL_FAILED = "postcall.failed"
    HANDOFF_REQUESTED = "handoff.requested"
    HANDOFF_COMPLETED = "handoff.completed"
    CONSENT_RECORDED = "consent.recorded"
    DND_RECORDED = "dnd.recorded"
    JOB_RETRIED = "job.retried"
    BUSINESS_CONFIG_UPDATED = "business.config_updated"
    CALL_CANCELED = "call.canceled"
    KNOWLEDGE_CREATED = "knowledge.created"
    KNOWLEDGE_UPDATED = "knowledge.updated"
    KNOWLEDGE_DELETED = "knowledge.deleted"
    FOLLOWUP_ANSWERED = "followup.answered"
    FOLLOWUP_CLARIFY = "followup.clarify"
    FOLLOWUP_ESCALATED = "followup.escalated"
    CUSTOMER_OPT_OUT = "customer.opt_out"
    # Bolna custom functions (live tool calls into Callora)
    TOOL_LEAD_READ = "tool.lead_read"
    TOOL_OUTCOME_SAVED = "tool.outcome_saved"
    TOOL_QUALIFICATION = "tool.qualification"
    TOOL_OPPORTUNITY = "tool.opportunity"
    TOOL_CALLBACK = "tool.callback"
    TOOL_CRM_SYNC = "tool.crm_sync"
    TOOL_STATUS_PRESERVED = "tool.status_preserved"
    TOOL_UNAUTHORIZED = "tool.unauthorized"
    CALLBACK_DISPATCHED = "callback.dispatched"
    OUTCOME_CONFLICT = "outcome.conflict"


class KnowledgeCategory:
    """Categories of verified business knowledge."""

    FAQ = "faq"
    PRODUCT_FEATURE = "product_feature"
    PRICING = "pricing"
    ELIGIBILITY = "eligibility"
    POLICY = "policy"
    OBJECTION_HANDLING = "objection_handling"
    ESCALATION_RULE = "escalation_rule"


ALL_KNOWLEDGE_CATEGORIES = (
    KnowledgeCategory.FAQ,
    KnowledgeCategory.PRODUCT_FEATURE,
    KnowledgeCategory.PRICING,
    KnowledgeCategory.ELIGIBILITY,
    KnowledgeCategory.POLICY,
    KnowledgeCategory.OBJECTION_HANDLING,
    KnowledgeCategory.ESCALATION_RULE,
)


class FollowUpAction:
    """Action recommendation resulting from a customer question."""

    ANSWER = "answer"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    OPT_OUT = "opt_out"


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
