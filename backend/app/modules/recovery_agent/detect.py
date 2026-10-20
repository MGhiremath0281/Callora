"""Stage 1 — DETECT: deterministic opportunity signals (no LLM).

Ported from the validated notebook prototype with two production fixes:

- timestamps are normalized to tz-aware UTC (the database returns
  tz-aware datetimes; the prototype assumed naive values);
- ``crm_sync_drift`` only fires when the business actually has an
  active CRM connection — a null ``external_crm_id`` without a
  configured CRM is normal, not drift.

Lead-status exclusion: leads that are actively handled or closed are
never recovery candidates.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Union

# Signal names produced by this module.
SIGNAL_NEVER_CONTACTED = "never_contacted"
SIGNAL_STALLED_FOLLOW_UP = "stalled_follow_up"
SIGNAL_CRM_SYNC_DRIFT = "crm_sync_drift"
SIGNAL_EVENT_DELIVERY_FAILED = "event_delivery_failed"
SIGNAL_IS_RECOVERED = "is_recovered"

# System-induced signals: recovery is a tool action (re-sync / notify),
# never outreach composed by the LLM.
SYSTEM_SIGNALS = frozenset({SIGNAL_CRM_SYNC_DRIFT, SIGNAL_EVENT_DELIVERY_FAILED})

# Leads in any of these statuses are actively handled, converted, or
# closed — the recovery agent must ignore them (Module 3 AC). Matching
# is case-insensitive; an unknown/missing status is treated as
# recoverable so new lead states degrade toward detection, not silence.
EXCLUDED_STATUSES: FrozenSet[str] = frozenset(
    {
        "contacted",
        "working",
        "qualified",
        "proposal",
        "negotiation",
        "won",
        "converted",
        "closed",
        "closed won",
        "closed lost",
        "archived",
        "do not contact",
        "opted out",
    }
)


@dataclass(frozen=True)
class RecoveryPolicy:
    """Per-business recovery criteria (would be a ``recovery_policy``
    table in a later module; sourced from ``CALLORA_RECOVERY_*``)."""

    sla_minutes: int = 120
    stall_days: int = 7
    score_threshold: float = 0.40
    max_attempts: int = 3
    auto_action_cap: float = 500.0
    enforce_quiet_hours: bool = False
    quiet_hours: "tuple[int, int]" = (21, 8)

    @classmethod
    def from_settings(cls, settings) -> "RecoveryPolicy":
        return cls(
            sla_minutes=settings.recovery_sla_minutes,
            stall_days=settings.recovery_stall_days,
            score_threshold=settings.recovery_score_threshold,
            max_attempts=settings.recovery_max_attempts,
            enforce_quiet_hours=settings.recovery_enforce_quiet_hours,
            quiet_hours=(
                settings.recovery_quiet_hours_start,
                settings.recovery_quiet_hours_end,
            ),
        )


@dataclass(frozen=True)
class DetectionContext:
    """External-system state signals are evaluated against.

    ``crm_configured`` gates the drift signal; ``failed_event_lead_ids``
    carries the lead IDs whose ``lead.created`` outbox events are in a
    terminal ``failed`` state.

    ``crm_synced_lead_ids`` is the set of leads the CRM synchronization
    module has *confirmed* as pushed to the external CRM (a terminal
    ``created``/``updated`` sync record). It is the evidence required to
    classify ``crm_sync_drift``: a missing ``external_crm_id`` alone,
    while the CRM is configured, is *unknown* — not drift (data
    reliability rule). When this set is empty the sweeper keeps the
    legacy behaviour (``crm_configured`` gates drift) so no information
    is silently lost.
    """

    crm_configured: bool = False
    failed_event_lead_ids: FrozenSet[str] = field(default_factory=frozenset)
    crm_synced_lead_ids: FrozenSet[str] = field(default_factory=frozenset)


def as_utc(value: Union[None, str, datetime]) -> Optional[datetime]:
    """Normalize an ISO string or datetime to tz-aware UTC.

    Naive values are assumed to be UTC (the same convention the rest of
    the backend uses). Returns None for empty values.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_candidate_status(status: Optional[str]) -> bool:
    """True when the lead's status allows recovery outreach.

    Actively handled (contacted/qualified/...) and converted/closed
    leads are excluded from detection entirely.
    """
    normalized = (status or "New").strip().lower()
    return normalized not in EXCLUDED_STATUSES


def is_recovered_status(status: Optional[str], recovered_statuses: FrozenSet[str]) -> bool:
    """True when the canonical ``status`` already IS the recovery end-state.

    Configuration-driven (``CALLORA_RECOVERY_RECOVERED_STATUSES``); by
    default the status ``recovered``. An empty/unknown status is never
    treated as recovered.
    """
    normalized = (status or "").strip().lower()
    return bool(normalized) and normalized in recovered_statuses


def is_ignored_status(status: Optional[str], ignored_statuses: FrozenSet[str]) -> bool:
    """True when a status sits outside the detection scope.

    These are leads an operator has closed out of recovery (or that the
    ACT stage already owns). They produce neither signals nor candidates
    — the lead is ignored, so a persisted case keeps its lifecycle and is
    never re-opened by a later sweep. An unknown/empty status is never
    ignored.
    """
    normalized = (status or "").strip().lower()
    return bool(normalized) and normalized in ignored_statuses


def is_opted_out(lead: Dict[str, Any]) -> bool:
    """Respect opt-out flags carried in ``custom_fields``."""
    custom = lead.get("custom_fields") or {}
    if not isinstance(custom, dict):
        return False
    return bool(custom.get("opt_out") or custom.get("opted_out"))


def is_opted_out_with_flags(lead: Dict[str, Any], opt_out_flags: FrozenSet[str]) -> bool:
    """Opt-out check honoring business-configured flag names.

    ``opt_out``/``opted_out`` are always honoured (existing behaviour);
    additional canonical flags (``do_not_contact``, ``dnc``,
    ``unsubscribed``, ``suppressed``…) come from configuration — the same
    names the lead-import mapping already recognises. Truthy *string*
    values (``"true"``/``"yes"``/``"1"``) are treated as set; anything
    unknown is ignored rather than guessed.
    """
    custom = lead.get("custom_fields") or {}
    if not isinstance(custom, dict):
        return False
    for flag in opt_out_flags:
        if _truthy(custom.get(flag)):
            return True
    return _truthy(custom.get("opt_out")) or _truthy(custom.get("opted_out"))


_TRUE_STRINGS = frozenset({"true", "yes", "y", "1", "on"})


def _truthy(value: Any) -> bool:
    """Conservative truthiness: missing/unknown values are NOT opt-out."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    if isinstance(value, (int, float)):
        return value != 0
    return False


def detect_signals(
    lead: Dict[str, Any],
    now: datetime,
    policy: RecoveryPolicy,
    ctx: DetectionContext,
) -> List[Dict[str, str]]:
    """Watchman: deterministic signal detection. Returns [] for healthy leads."""
    signals: List[Dict[str, str]] = []

    created = as_utc(lead.get("created_at"))
    last_contact = as_utc(lead.get("last_contact_at"))
    now = as_utc(now) or datetime.now(timezone.utc)

    # S1: never contacted AND past SLA. Older payloads without a
    # creation timestamp simply skip this signal instead of crashing.
    if created is not None and last_contact is None:
        age_min = (now - created).total_seconds() / 60
        if age_min > policy.sla_minutes:
            signals.append(
                {
                    "signal": SIGNAL_NEVER_CONTACTED,
                    "severity": "high",
                    "detail": (
                        f"no contact {int(age_min)} min after creation "
                        f"(SLA {policy.sla_minutes} min)"
                    ),
                }
            )

    # S2: contacted once, then stalled.
    if last_contact is not None and (now - last_contact) > timedelta(days=policy.stall_days):
        stalled_days = (now - last_contact).days
        signals.append(
            {
                "signal": SIGNAL_STALLED_FOLLOW_UP,
                "severity": "medium",
                "detail": f"last touch {stalled_days} days ago",
            }
        )

    # S6: CRM sync drift -> sales never received the lead (Module 2
    # signal). Only meaningful when a CRM connection actually exists; a
    # null external_crm_id without a configured CRM is the normal state,
    # not drift. (Lead-status exclusion is the caller's job — the sweep
    # skips inactive leads before detection.)
    #
    # Data reliability: drift requires evidence. When the CRM sync module
    # has confirmed synced leads (``crm_synced_lead_ids``), only a lead
    # that WAS confirmed synced and has since lost its ``external_crm_id``
    # is drift; a missing id without a confirmed sync is *unknown* and
    # must not be reported as drift. When no confirmation list is
    # available the legacy ``crm_configured`` gate is used, so existing
    # behaviour (and existing tests) are preserved.
    if _crm_sync_drift_detected(lead, ctx):
        signals.append(
            {
                "signal": SIGNAL_CRM_SYNC_DRIFT,
                "severity": "medium",
                "detail": "lead not present in external CRM",
            }
        )

    # S7: system-induced drop -> lead.created event failed downstream.
    lead_id = lead.get("lead_id") or lead.get("id")
    if lead_id and lead_id in ctx.failed_event_lead_ids:
        signals.append(
            {
                "signal": SIGNAL_EVENT_DELIVERY_FAILED,
                "severity": "high",
                "detail": "lead.created outbox event exhausted retries",
            }
        )

    # S8: the lead is already recovered (explicit flag or canonical
    # end-state). Kept as a last, low-severity signal so the
    # recommendation contract stays intact — the eligibility gate then
    # excludes it (a ready-to-use signal, not a second exclusion path).
    if is_recovered(lead):
        signals.append(
            {
                "signal": SIGNAL_IS_RECOVERED,
                "severity": "low",
                "detail": "lead already recovered; not an open opportunity",
            }
        )

    return signals


def _crm_sync_drift_detected(lead: Dict[str, Any], ctx: DetectionContext) -> bool:
    """CRM drift requires a configured CRM *and* positive evidence.

    When ``crm_synced_lead_ids`` carries confirmations, drift is only
    reported for leads that were confirmed synced but no longer have an
    ``external_crm_id``. Otherwise (no confirmation list available) the
    legacy ``crm_configured`` gate applies.
    """
    if lead.get("external_crm_id"):
        return False
    if not ctx.crm_configured:
        return False
    if ctx.crm_synced_lead_ids:
        return (lead.get("lead_id") or lead.get("id")) in ctx.crm_synced_lead_ids
    return True


def is_recovered(lead: Dict[str, Any]) -> bool:
    """True when the lead is already recovered.

    Honours an explicit ``custom_fields.is_recovered`` flag first, then a
    canonical ``status`` in the configured recovered set. Any other
    (missing/unknown) value is *not* evidence of recovery.
    """
    custom = lead.get("custom_fields") or {}
    if isinstance(custom, dict) and _truthy(custom.get("is_recovered")):
        return True
    from app.modules.recovery_agent.policy import recovered_statuses

    return is_recovered_status(lead.get("status"), recovered_statuses())

