"""Stage 2 — ASSESS: recoverability scoring (pure math, no LLM).

    R = 0.35·VALUE + 0.25·RECENCY + 0.20·REACHABILITY + 0.15·PRIOR
        + 0.05·DRIFT − PENALTY

Identical formula to the validated notebook prototype, hardened for
production payloads: ``deal_value`` may be null or a Decimal, and all
timestamps pass through ``detect.as_utc``.
"""

from typing import Any, Dict, List

from app.modules.recovery_agent.detect import (
    SIGNAL_CRM_SYNC_DRIFT,
    SIGNAL_EVENT_DELIVERY_FAILED,
)

SEVERITY_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}


def _value_component(lead: Dict[str, Any]) -> float:
    """Deal value normalized to 0..1 against a Rs.10M reference."""
    raw = lead.get("deal_value")
    if raw in (None, ""):
        return 0.0
    try:
        return min(abs(float(raw)) / 10_000_000, 1.0)
    except (TypeError, ValueError):
        return 0.0


def recoverability(
    lead: Dict[str, Any],
    signals: List[Dict[str, str]],
    attempts_used: int,
) -> float:
    """Deterministic 0..1 recoverability score for one lead."""
    value = _value_component(lead)
    recency = max(SEVERITY_WEIGHT[s["severity"]] for s in signals) if signals else 0.0

    reach = 1.0
    if not lead.get("phone"):
        reach -= 0.6
    if not lead.get("language"):
        reach -= 0.4
    custom = lead.get("custom_fields") or {}
    if isinstance(custom, dict) and (custom.get("opt_out") or custom.get("opted_out")):
        reach = 0.0  # hard stop

    prior = max(0.2, 1.0 - 0.25 * attempts_used)
    drift = (
        0.05
        if any(s["signal"] in (SIGNAL_CRM_SYNC_DRIFT, SIGNAL_EVENT_DELIVERY_FAILED) for s in signals)
        else 0.0
    )
    penalty = 0.10 * attempts_used

    score = 0.35 * value + 0.25 * recency + 0.20 * reach + 0.15 * prior + drift - penalty
    return round(min(max(score, 0.0), 1.0), 3)
