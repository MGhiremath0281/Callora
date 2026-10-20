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
    """

    crm_configured: bool = False
    failed_event_lead_ids: FrozenSet[str] = field(default_factory=frozenset)


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


def is_opted_out(lead: Dict[str, Any]) -> bool:
    """Respect opt-out flags carried in ``custom_fields``."""
    custom = lead.get("custom_fields") or {}
    if not isinstance(custom, dict):
        return False
    return bool(custom.get("opt_out") or custom.get("opted_out"))


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
    # signal). Only meaningful when a CRM connection actually exists;
    # a null external_crm_id without a configured CRM is the normal
    # state, not drift. (Lead-status exclusion is the caller's job —
    # the sweep skips inactive leads before detection.)
    if ctx.crm_configured and not lead.get("external_crm_id"):
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

    return signals
