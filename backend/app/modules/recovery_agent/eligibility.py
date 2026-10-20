"""Deterministic ELIGIBILITY gate for the recovery sweep.

A recovery opportunity is only admitted when *every* business rule below
passes. There is no scoring, no LLM and no heuristics here: the same
inputs always produce the same decision, so the gate is independently
testable and safe to audit.

Rules (each returns an explicit exclusion reason when it fails):

``status``            the lead is not in an excluded/recovered/ignored
                      lifecycle state (actively handled, converted,
                      closed, already recovered, dismissed…);
``consent``           the lead has not opted out / been suppressed;
``contact``           the lead has at least one usable contact channel
                      required for the proposed recovery action;
``evidence``          the lead's data actually supports a recovery
                      signal (missing or unknown information is never
                      treated as eligibility).

The gate never mutates its inputs, never inspects the database and never
calls external services, so a CRM/LLM outage cannot corrupt an
eligibility decision.

Documented assumption: existing data has no dedicated *consent* column.
The ``custom_fields`` opt-out/suppression flags (the names the lead
import already maps, e.g. ``Do Not Contact`` → ``do_not_contact``) are
the strongest available consent signal and are therefore authoritative
when set. An absent flag is treated as ``unknown`` — which is why the
contact rule still requires real, usable contact information.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from app.modules.recovery_agent.detect import (
    SIGNAL_IS_RECOVERED,
    RecoveryPolicy,
    as_utc,
    is_candidate_status,
    is_ignored_status,
    is_opted_out_with_flags,
    is_recovered_status,
)

# Exclusion reason codes (stable, machine-readable).
REASON_MISSING_IDENTIFIER = "missing_identifier"
REASON_INACTIVE_STATUS = "inactive_status"
REASON_ALREADY_RECOVERED = "already_recovered"
REASON_IGNORED_STATUS = "ignored_status"
REASON_OPTED_OUT = "opted_out"
REASON_NO_CONTACT_CHANNEL = "no_contact_channel"
REASON_NO_RECOVERY_EVIDENCE = "no_recovery_evidence"

# Sweep-level exclusion reasons (persisted in existing recovery cases, so
# the lead is never re-opened by a later sweep; not produced by the
# per-lead gate above).
REASON_EXISTING_CASE = "existing_case_in_progress"
REASON_DUPLICATE_ENTRY = "duplicate_entry"

# Signals that indicate a lead is already recovered (never an opportunity).
_RECOVERED_SIGNALS: FrozenSet[str] = frozenset({SIGNAL_IS_RECOVERED})


@dataclass
class EligibilityDecision:
    """The outcome of the deterministic gate for one lead."""

    eligible: bool
    reason: Optional[str] = None
    detail: str = ""
    contact_channels: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # convenience: ``if decision:``
        return self.eligible


def usable_contact_channels(lead: Dict[str, Any], channels: Tuple[str, ...]) -> List[str]:
    """Contact channels that are present *and* usable.

    A channel counts only when it is a non-blank string/number — a
    ``None``, empty or whitespace value is unknown information, never
    proof of reachability.
    """
    usable: List[str] = []
    for channel in channels:
        value = lead.get(channel)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        usable.append(channel)
    return usable


def _explicit_recovery_flag(lead: Dict[str, Any]) -> bool:
    """Truthiness of an explicit ``custom_fields.is_recovered`` flag."""
    custom = lead.get("custom_fields") or {}
    if not isinstance(custom, dict):
        return False
    value = custom.get("is_recovered")
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _has_recovery_evidence(
    lead: Dict[str, Any],
    signals: Optional[List[Dict[str, str]]],
    now: datetime,
    policy: RecoveryPolicy,
) -> bool:
    """Does the lead's *known* data support recovery?

    Missing or unknown data (no creation timestamp, unparseable contact
    timestamps, unconfirmed CRM status) is never treated as evidence.
    When pre-computed ``signals`` are supplied they take precedence so the
    sweep and the standalone gate always agree.
    """
    if signals is not None:
        meaningful = [s for s in signals if s.get("signal") not in _RECOVERED_SIGNALS]
        return bool(meaningful)

    created = as_utc(lead.get("created_at"))
    last_contact = as_utc(lead.get("last_contact_at"))
    now = as_utc(now) or now

    if created is not None and last_contact is None:
        # never_contacted requires a provably elapsed SLA.
        if (now - created).total_seconds() / 60 > policy.sla_minutes:
            return True

    if last_contact is not None:
        # stalled follow-up requires a provably elapsed stall window —
        # the same strict ``>`` comparison ``detect_signals`` uses, so a
        # lead is never excluded here for a stall the detector counts.
        if (now - last_contact) > timedelta(days=policy.stall_days):
            return True

    return False


def check_eligibility(
    lead: Dict[str, Any],
    now: datetime,
    policy: RecoveryPolicy,
    config: Any,
    signals: Optional[List[Dict[str, str]]] = None,
) -> EligibilityDecision:
    """Evaluate the deterministic eligibility rules for one lead.

    ``config`` is anything exposing ``contact_channels``,
    ``opt_out_flags``, ``recovered_statuses``, ``ignored_statuses`` and
    ``require_reachability`` (see :class:`policy.EligibilityConfig`).

    Returns an :class:`EligibilityDecision`; ``reason`` is ``None`` for
    eligible leads.
    """
    channels = tuple(getattr(config, "contact_channels", ("phone",)))
    opt_out_flags = frozenset(getattr(config, "opt_out_flags", frozenset()))
    recovered_statuses = frozenset(getattr(config, "recovered_statuses", frozenset()))
    ignored_statuses = frozenset(getattr(config, "ignored_statuses", frozenset()))
    require_reachability = bool(getattr(config, "require_reachability", True))

    # 1. Identifier — the sweep cannot attribute a case without one.
    lead_id = lead.get("lead_id") or lead.get("id")
    if not lead_id or not isinstance(lead_id, str):
        return EligibilityDecision(False, REASON_MISSING_IDENTIFIER, "no lead identifier")

    status = lead.get("status")

    # 2. Lifecycle — actively handled / converted / closed are excluded.
    if not is_candidate_status(status):
        return EligibilityDecision(False, REASON_INACTIVE_STATUS, f"status={status or 'unknown'}")

    # 3. Previous outcome — an already-recovered lead is not an opportunity.
    #    An explicit ``is_recovered`` flag is honoured first, then the
    #    canonical end-state status.
    if _explicit_recovery_flag(lead) or is_recovered_status(status, recovered_statuses):
        return EligibilityDecision(False, REASON_ALREADY_RECOVERED, f"status={status or 'flag'}")

    # 4. Operator-closed states — never re-opened by a sweep.
    if is_ignored_status(status, ignored_statuses):
        return EligibilityDecision(False, REASON_IGNORED_STATUS, f"status={status}")

    # 5. Consent / contact restrictions — authoritative when set.
    if is_opted_out_with_flags(lead, opt_out_flags):
        return EligibilityDecision(False, REASON_OPTED_OUT, "opt-out / suppression flag set")

    # 6. Contact validity — the proposed action needs a usable channel.
    usable = usable_contact_channels(lead, channels)
    if require_reachability and not usable:
        return EligibilityDecision(
            False,
            REASON_NO_CONTACT_CHANNEL,
            f"no usable contact channel (required: {', '.join(channels) or 'none'})",
        )

    # 7. Recovery evidence — unknown data is not proof of eligibility.
    if not _has_recovery_evidence(lead, signals, now, policy):
        return EligibilityDecision(
            False,
            REASON_NO_RECOVERY_EVIDENCE,
            "no verifiable recovery signal in the available data",
        )

    return EligibilityDecision(True, None, "", usable)


def is_eligible(
    lead: Dict[str, Any],
    now: datetime,
    policy: RecoveryPolicy,
    config: Any,
    signals: Optional[List[Dict[str, str]]] = None,
) -> bool:
    """Boolean convenience wrapper around :func:`check_eligibility`."""
    return check_eligibility(lead, now, policy, config, signals).eligible
