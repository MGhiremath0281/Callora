"""Guardrails — hard blocks enforced **outside** the LLM.

The Strategist may only choose *within* this envelope. These checks run
again at execution time in the ACT stage (defense in depth).

Ported from the notebook prototype. One deliberate change: quiet hours
are only enforced when ``recovery_enforce_quiet_hours`` is enabled —
Module 3 produces *recommendations*, and a sweep running at 22:00 must
not archive every candidate. The ACT stage enables the check before any
message is actually sent.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from app.modules.recovery_agent.detect import (
    SYSTEM_SIGNALS,
    RecoveryPolicy,
    as_utc,
    is_opted_out,
)


def hard_block(
    lead: Dict[str, Any],
    now: datetime,
    policy: RecoveryPolicy,
    attempts_used: int,
) -> Optional[str]:
    """Returns a blocking reason, or None when outreach is allowed."""
    if is_opted_out(lead):
        return "lead opted out of outreach"
    if attempts_used >= policy.max_attempts:
        return f"max attempts reached ({policy.max_attempts})"
    now = as_utc(now) or datetime.now().astimezone()
    if policy.enforce_quiet_hours:
        quiet_from, quiet_to = policy.quiet_hours
        if now.hour >= quiet_from or now.hour < quiet_to:
            return "quiet hours"
    if not lead.get("phone"):
        return "no reachable channel"
    return None


def route(
    lead: Dict[str, Any],
    signals: list,
    now: datetime,
    policy: RecoveryPolicy,
    attempts_used: int,
) -> str:
    """Decide WHO acts: 'block' | 'auto' | 'llm' | 'hitl'.

    auto : system-induced signals only (re-sync CRM / notify owner) —
           no outreach, so no LLM composition is needed.
    llm  : anything choosing or composing outreach (judgment + language).
    hitl : automated attempts already happened -> human reviews the case.
    """
    if hard_block(lead, now, policy, attempts_used):
        return "block"
    if attempts_used >= 1:
        return "hitl"
    if all(s["signal"] in SYSTEM_SIGNALS for s in signals):
        return "auto"
    return "llm"
