"""The pre-call compliance gate.

Every outbound call passes through :class:`ComplianceGate` before a
provider is contacted. The gate evaluates a fixed set of mandatory checks
and returns a decision that is persisted (``compliance_decisions``) and
audited (``audit_logs``).

Two rules shape the implementation:

**Fail closed.** A check that cannot be completed (database error, an
unreachable DND provider, an unreadable business configuration) blocks the
call. An unavailable mandatory check must never default to approval.

**PostgreSQL is the source of truth.** Redis may cache DND lookups, but a
cached answer is never the sole basis for a decision: the registry is
always queried.

**One development-only exception.** The two daily quotas (per lead and
per business) can be skipped by a deployment that explicitly asks for it
(``CALLORA_DISABLE_CALL_QUOTAS`` in a development environment). No other
check can be skipped, and the override is refused outside a development
environment — see ``Settings.call_quotas_disabled``.
"""

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.models import Lead
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    CheckOutcome,
    ComplianceDecision,
    IN_FLIGHT_STATUSES,
    ReasonCode,
)
from app.modules.voice.models import Business, VoiceLeadProfile
from app.modules.voice.phone import is_valid_e164, normalize_phone
from app.modules.voice.providers.base import VoiceProvider

logger = get_logger("callora.voice.compliance")
audit_logger = logging.getLogger("callora.voice.compliance")

#: Version tag stored on every compliance decision.
POLICY_VERSION = "v1"


def _describe_duration(seconds: int) -> str:
    """Human readable duration for a message about a time window.

    The setting is expressed in seconds, so a short window reads the way
    it was configured ("60 seconds"); a window of whole minutes reads
    better in minutes ("60 minutes").
    """
    if seconds >= 120 and seconds % 60 == 0:
        return f"{seconds // 60} minutes"
    return f"{seconds} second" if seconds == 1 else f"{seconds} seconds"


@dataclass
class CheckResult:
    """The result of a single compliance check."""

    name: str
    outcome: str
    mandatory: bool = True
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome == CheckOutcome.PASSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "mandatory": self.mandatory,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "details": self.details,
        }

    def to_blocked_reason(self) -> Dict[str, Any]:
        """Compact representation used in the API error details."""
        return {
            "check": self.name,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "mandatory": self.mandatory,
        }


@dataclass
class ComplianceContext:
    """Everything a check may need to make a decision."""

    business: Business
    lead: Optional[Lead]
    provider: VoiceProvider
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    profile: Optional[VoiceLeadProfile] = None
    phone: Optional[str] = None
    #: True when the call will **not** be placed with a provider (a dry run,
    #: or a deployment where outbound calling is switched off). The provider
    #: is never contacted in that case, so ``provider_ready`` is reported as
    #: skipped instead of blocking the call.
    simulated: bool = False


@dataclass
class ComplianceEvaluation:
    """The outcome of one gate evaluation."""

    decision: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == ComplianceDecision.ALLOWED

    @property
    def blocked_reasons(self) -> List[Dict[str, Any]]:
        return [c.to_blocked_reason() for c in self.checks if not c.passed and c.mandatory]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "checks": [c.to_dict() for c in self.checks],
            "blocked_reasons": self.blocked_reasons,
        }


class DndLookup(abc.ABC):
    """Optional external do-not-disturb source.

    A configured implementation is treated as *mandatory*: if it cannot be
    reached the call is blocked. When no implementation is configured the
    check is skipped (the local registry remains authoritative).
    """

    @abc.abstractmethod
    def is_on_dnd(self, phone: str) -> bool:
        """Whether the number is registered on an external DND list."""


class ComplianceGate:
    """Evaluates every mandatory pre-call rule."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        dnd_lookup: Optional[DndLookup] = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._dnd_lookup = dnd_lookup

    # -- entry point -------------------------------------------------------

    def evaluate(self, ctx: ComplianceContext) -> ComplianceEvaluation:
        """Run every check and return the decision.

        A check that raises is recorded as an error and blocks the call;
        the remaining checks still run so the operator sees the full
        picture.
        """
        checks: List[CheckResult] = []
        for name, check in self._checks():
            try:
                result = check(ctx)
            except Exception as exc:  # fail closed
                audit_logger.exception("compliance_check_failed", extra={"check": name})
                result = CheckResult(
                    name=name,
                    outcome=CheckOutcome.ERROR,
                    mandatory=True,
                    reason_code=ReasonCode.CHECK_UNAVAILABLE,
                    reason=f"the '{name}' check could not be completed: {exc}",
                    details={"error": str(exc)},
                )
            checks.append(result)

        blocked = [c for c in checks if c.mandatory and not c.passed]
        decision = ComplianceDecision.BLOCKED if blocked else ComplianceDecision.ALLOWED
        evaluation = ComplianceEvaluation(decision=decision, checks=checks)
        if not evaluation.allowed:
            logger.info(
                "compliance_blocked",
                extra={
                    "business_id": ctx.business.id,
                    "reasons": ",".join(r.get("reason_code") or "unknown" for r in evaluation.blocked_reasons),
                },
            )
        return evaluation

    def _checks(self) -> List[tuple]:
        return [
            ("business_active", self._check_business_active),
            ("lead_exists", self._check_lead_exists),
            ("consent", self._check_consent),
            ("dnd", self._check_dnd),
            ("calling_hours", self._check_calling_hours),
            ("duplicate_call", self._check_duplicate_call),
            ("quota", self._check_quota),
            ("concurrency", self._check_concurrency),
            ("provider_ready", self._check_provider_ready),
        ]

    # -- checks ------------------------------------------------------------

    def _check_business_active(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        if business is None:
            return CheckResult(
                name="business_active",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.BUSINESS_NOT_FOUND,
                reason="the business configuration does not exist",
            )
        if not business.is_active:
            return CheckResult(
                name="business_active",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.BUSINESS_INACTIVE,
                reason="the business is deactivated",
            )
        return CheckResult(
            name="business_active",
            outcome=CheckOutcome.PASSED,
            reason="the business is active",
            details={"business_id": business.id},
        )

    def _check_lead_exists(self, ctx: ComplianceContext) -> CheckResult:
        lead = ctx.lead
        if lead is None:
            return CheckResult(
                name="lead_exists",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.LEAD_NOT_FOUND,
                reason="the lead does not exist for this business",
            )
        phone = ctx.phone or normalize_phone(lead.phone)
        if not is_valid_e164(phone):
            return CheckResult(
                name="lead_exists",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.LEAD_PHONE_INVALID,
                reason="the lead has no usable phone number",
                details={"phone": lead.phone},
            )
        return CheckResult(
            name="lead_exists",
            outcome=CheckOutcome.PASSED,
            reason="the lead exists and has a callable number",
            details={"lead_id": lead.id, "phone": phone},
        )

    def _check_consent(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        lead = ctx.lead
        if not business.require_consent:
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.SKIPPED,
                mandatory=False,
                reason="the business does not require recorded consent",
            )
        if lead is None:
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONSENT_MISSING,
                reason="no lead to check consent for",
            )
        record = repo.get_consent(self._session, business.id, lead.id, "voice")
        if record is None:
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONSENT_MISSING,
                reason="no voice consent record exists for this lead",
            )
        if record.status == "revoked":
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONSENT_REVOKED,
                reason="voice consent was revoked",
                details={"revoked_at": _iso(record.revoked_at)},
            )
        if record.status == "expired" or (
            record.expires_at is not None and record.expires_at <= ctx.now
        ):
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONSENT_EXPIRED,
                reason="voice consent has expired",
                details={"expires_at": _iso(record.expires_at)},
            )
        if record.status != "granted":
            return CheckResult(
                name="consent",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONSENT_MISSING,
                reason=f"voice consent is in state '{record.status}'",
            )
        return CheckResult(
            name="consent",
            outcome=CheckOutcome.PASSED,
            reason="voice consent is on record",
            details={
                "source": record.source,
                "granted_at": _iso(record.granted_at),
                "expires_at": _iso(record.expires_at),
            },
        )

    def _check_dnd(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        lead = ctx.lead
        if lead is None:
            return CheckResult(
                name="dnd",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.LEAD_NOT_FOUND,
                reason="no lead to check for do-not-disturb",
            )
        if not business.dnd_enabled:
            return CheckResult(
                name="dnd",
                outcome=CheckOutcome.SKIPPED,
                mandatory=False,
                reason="DND screening is disabled for this business",
            )
        phone = ctx.phone or normalize_phone(lead.phone)
        profile = ctx.profile or repo.get_lead_profile(
            self._session, business.id, lead.id
        )
        if profile is not None and profile.do_not_call:
            return CheckResult(
                name="dnd",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.DND_LEAD_FLAG,
                reason="the lead is flagged do-not-call",
                details={"phone": phone},
            )
        entry = repo.find_dnd_entry(self._session, business.id, phone)
        if entry is not None:
            return CheckResult(
                name="dnd",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.DND_ACTIVE,
                reason="the number is registered on a do-not-disturb list",
                details={
                    "phone": phone,
                    "scope": entry.scope,
                    "source": entry.source,
                    "reason": entry.reason,
                },
            )
        if self._dnd_lookup is not None:
            # A configured external source is mandatory: if it cannot be
            # reached the check raises and the caller fails closed.
            if self._dnd_lookup.is_on_dnd(phone):
                return CheckResult(
                    name="dnd",
                    outcome=CheckOutcome.FAILED,
                    reason_code=ReasonCode.DND_ACTIVE,
                    reason="the number is registered on an external do-not-disturb list",
                    details={"phone": phone, "source": "external"},
                )
            return CheckResult(
                name="dnd",
                outcome=CheckOutcome.PASSED,
                reason="the number passed the local and external DND checks",
                details={"phone": phone, "external": True},
            )
        return CheckResult(
            name="dnd",
            outcome=CheckOutcome.PASSED,
            reason="the number is not on any do-not-disturb list",
            details={"phone": phone, "external": False},
        )

    def _check_calling_hours(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        tz = repo.resolve_timezone(business.timezone)
        local_now = ctx.now.astimezone(tz)
        start = business.calling_window_start
        end = business.calling_window_end
        days = list(business.calling_days or [])
        weekday = local_now.weekday()  # 0 = Monday
        if days and weekday not in days:
            return CheckResult(
                name="calling_hours",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CALLING_DAY_BLOCKED,
                reason="calls are not permitted on this weekday",
                details={
                    "weekday": weekday,
                    "allowed_days": days,
                    "timezone": business.timezone,
                },
            )
        if start == end:
            # A zero-width window means "no restriction".
            return CheckResult(
                name="calling_hours",
                outcome=CheckOutcome.PASSED,
                reason="the business does not restrict calling hours",
                details={"timezone": business.timezone},
            )
        current_hour = local_now.hour + local_now.minute / 60.0
        if start < end:
            inside = start <= current_hour < end
        else:
            # Window wraps past midnight (e.g. 22:00 -> 06:00).
            inside = current_hour >= start or current_hour < end
        if not inside:
            return CheckResult(
                name="calling_hours",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.OUTSIDE_CALLING_HOURS,
                reason="the current time is outside the permitted calling window",
                details={
                    "local_time": local_now.isoformat(),
                    "window_start": start,
                    "window_end": end,
                    "timezone": business.timezone,
                },
            )
        return CheckResult(
            name="calling_hours",
            outcome=CheckOutcome.PASSED,
            reason="the current time is inside the permitted calling window",
            details={
                "local_time": local_now.isoformat(),
                "window_start": start,
                "window_end": end,
                "timezone": business.timezone,
            },
        )

    def _check_duplicate_call(self, ctx: ComplianceContext) -> CheckResult:
        """Two separate rules protect a lead from a duplicate call.

        **The in-flight guard.** A call the provider still reports as
        queued, ringing or active blocks a second call to the same lead
        until it reaches a terminal state. This rule is mandatory and
        completely independent of the cooldown below: it is never weakened
        by a shorter (or disabled) cooldown, and it is evaluated first.

        **The cooldown.** After the previous call ended, the lead is
        protected for a configurable gap (``CALLORA_VOICE_DUPLICATE_CALL_
        COOLDOWN_SECONDS``, 60 minutes by default). The gap is measured in
        seconds so a local development deployment can shorten it without a
        code change.
        """
        business = ctx.business
        lead = ctx.lead
        if lead is None:
            return CheckResult(
                name="duplicate_call",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.LEAD_NOT_FOUND,
                reason="no lead to check for duplicate calls",
            )
        in_flight = (
            self._session.execute(
                select(repo.Call)
                .where(
                    repo.Call.business_id == business.id,
                    repo.Call.lead_id == lead.id,
                    repo.Call.status.in_(list(IN_FLIGHT_STATUSES)),
                )
                .order_by(repo.Call.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if in_flight is not None:
            # The previous call is still queued, ringing or active: the
            # provider has not confirmed a terminal state, so a second call
            # is refused no matter how short the cooldown is.
            return CheckResult(
                name="duplicate_call",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.DUPLICATE_CALL_IN_FLIGHT,
                reason="another call to this lead is already in progress",
                details={"call_id": in_flight.id, "status": in_flight.status},
            )
        cooldown_seconds = self._settings.duplicate_call_cooldown_seconds
        if cooldown_seconds > 0:
            since = ctx.now - timedelta(seconds=cooldown_seconds)
            recent = repo.find_recent_call_for_lead(
                self._session, business.id, lead.id, since
            )
            if recent is not None:
                return CheckResult(
                    name="duplicate_call",
                    outcome=CheckOutcome.FAILED,
                    reason_code=ReasonCode.DUPLICATE_CALL_COOLDOWN,
                    reason=(
                        "this lead was called less than "
                        f"{_describe_duration(cooldown_seconds)} ago"
                    ),
                    details={
                        "call_id": recent.id,
                        "status": recent.status,
                        "cooldown_seconds": cooldown_seconds,
                        "cooldown_minutes": cooldown_seconds // 60,
                    },
                )
        return CheckResult(
            name="duplicate_call",
            outcome=CheckOutcome.PASSED,
            reason="no conflicting call exists for this lead",
            details={
                "cooldown_seconds": cooldown_seconds,
                "cooldown_minutes": cooldown_seconds // 60,
            },
        )

    def _check_quota(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        lead = ctx.lead
        tz_name = business.timezone
        per_lead = business.max_calls_per_lead_per_day
        per_business = business.max_calls_per_business_per_day
        if self._settings.call_quotas_disabled:
            # Development-only: the two daily quotas are not evaluated.
            # Nothing else is relaxed — every other check of the gate still
            # runs and still blocks. The decision records that the quotas
            # were skipped, so the bypass stays auditable.
            return CheckResult(
                name="quota",
                outcome=CheckOutcome.SKIPPED,
                mandatory=False,
                reason=(
                    "the daily call quotas are disabled for this development "
                    "environment"
                ),
                details={
                    "disabled": True,
                    "environment": self._settings.environment,
                    "per_lead": per_lead,
                    "per_business": per_business,
                },
            )
        if lead is not None and per_lead:
            used = repo.count_attempts_today(
                self._session, business.id, tz_name, lead_id=lead.id
            )
            if used >= per_lead:
                return CheckResult(
                    name="quota",
                    outcome=CheckOutcome.FAILED,
                    reason_code=ReasonCode.DAILY_LEAD_QUOTA_EXCEEDED,
                    reason=(
                        f"the daily call limit for this lead ({per_lead}) is reached"
                    ),
                    details={"used": used, "limit": per_lead, "scope": "lead"},
                )
        if per_business:
            used_business = repo.count_attempts_today(
                self._session, business.id, tz_name
            )
            if used_business >= per_business:
                return CheckResult(
                    name="quota",
                    outcome=CheckOutcome.FAILED,
                    reason_code=ReasonCode.DAILY_BUSINESS_QUOTA_EXCEEDED,
                    reason=(
                        "the daily call limit for this business "
                        f"({per_business}) is reached"
                    ),
                    details={
                        "used": used_business,
                        "limit": per_business,
                        "scope": "business",
                    },
                )
        return CheckResult(
            name="quota",
            outcome=CheckOutcome.PASSED,
            reason="the daily call quota is not exhausted",
            details={"per_lead": per_lead, "per_business": per_business},
        )

    def _check_concurrency(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        in_flight = repo.count_in_flight_calls(self._session, business.id)
        limit = business.max_concurrent_calls
        if in_flight >= limit:
            return CheckResult(
                name="concurrency",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.CONCURRENCY_LIMIT_REACHED,
                reason=f"the business already has {in_flight} calls in flight",
                details={"in_flight": in_flight, "limit": limit},
            )
        return CheckResult(
            name="concurrency",
            outcome=CheckOutcome.PASSED,
            reason="the concurrent call limit is not reached",
            details={"in_flight": in_flight, "limit": limit},
        )

    def _check_provider_ready(self, ctx: ComplianceContext) -> CheckResult:
        business = ctx.business
        if ctx.simulated:
            # The provider is not contacted for a simulated call, so missing
            # credentials are not a compliance problem — they would only
            # stop the provider from being reached. The check is reported
            # as skipped so the decision still shows why it did not apply.
            return CheckResult(
                name="provider_ready",
                outcome=CheckOutcome.SKIPPED,
                mandatory=False,
                reason="the call will not be placed with the provider",
                details={"provider": ctx.provider.name, "simulated": True},
            )
        agent_id = business.bolna_agent_id or self._settings.bolna_default_agent_id
        if not agent_id:
            return CheckResult(
                name="provider_ready",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.PROVIDER_NOT_CONFIGURED,
                reason="no voice agent is configured for this business",
            )
        if not ctx.provider.is_configured():
            return CheckResult(
                name="provider_ready",
                outcome=CheckOutcome.FAILED,
                reason_code=ReasonCode.PROVIDER_NOT_CONFIGURED,
                reason="the voice provider credentials are not configured",
                details={"provider": ctx.provider.name},
            )
        return CheckResult(
            name="provider_ready",
            outcome=CheckOutcome.PASSED,
            reason="the voice provider is ready",
            details={"provider": ctx.provider.name, "agent_id": agent_id},
        )


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def evaluate_call(
    settings: Settings,
    session: Session,
    business: Business,
    lead: Optional[Lead],
    provider: VoiceProvider,
    now: Optional[datetime] = None,
    dnd_lookup: Optional[DndLookup] = None,
    simulated: bool = False,
) -> ComplianceEvaluation:
    """Convenience wrapper around :class:`ComplianceGate`.

    ``simulated`` says whether the call would actually reach the provider.
    It defaults to ``False`` — the strict interpretation, in which
    ``provider_ready`` is mandatory — so a caller has to opt in to the
    relaxed behaviour explicitly. The orchestrator is the only production
    caller and it passes the real value.
    """
    gate = ComplianceGate(settings, session, dnd_lookup=dnd_lookup)
    profile = (
        repo.get_lead_profile(session, business.id, lead.id) if lead is not None else None
    )
    ctx = ComplianceContext(
        business=business,
        lead=lead,
        provider=provider,
        now=now or datetime.now(timezone.utc),
        profile=profile,
        phone=normalize_phone(lead.phone) if lead is not None else None,
        simulated=simulated,
    )
    return gate.evaluate(ctx)


#: The checks enforced by the gate, in evaluation order.
CHECK_NAMES: List[str] = [
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
