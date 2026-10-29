"""Outcomes, costs, verified revenue and funnel-leakage analytics.

The rules that keep the numbers honest:

* **Verified revenue only.** ``revenue_records`` and the
  ``revenue_amount`` of an outcome count toward ROI only when
  ``verified=True`` and a verifier is named. Everything else is reported
  separately as *unverified pipeline* and never summed into ROI.
* **Real costs.** Cost rows are only created from a provider-reported
  number (call cost) or a manual record. Nothing is estimated.
* **Minimum samples.** Rates below
  ``CALLORA_ANALYTICS_MIN_SAMPLE_PER_WINDOW`` are reported as
  ``insufficient_data`` instead of a percentage. Leakage percentages
  additionally require ``CALLORA_ANALYTICS_MIN_CALLS_FOR_LEAKAGE``
  analysed calls.
* **Tenant isolation.** Every query is business-scoped.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import InvalidParameterError, LeadNotFoundError
from app.modules.recovery_workflow.models import (
    ConversationAnalysis,
    CostRecord,
    OutcomeRecord,
    RevenueRecord,
)
from app.modules.voice import repository as voice_repo

#: Known outcome types. Unknown types are allowed but flagged.
KNOWN_OUTCOME_TYPES = {
    "recovered",
    "reengaged",
    "booked",
    "qualified",
    "lost",
    "unreachable",
    "opted_out",
}


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record_outcome(
    session: Session,
    *,
    business_id: str,
    lead_id: str,
    outcome_type: str,
    verified: bool = False,
    verified_by: Optional[str] = None,
    verification_source: Optional[str] = None,
    revenue_amount: Optional[float] = None,
    currency: str = "INR",
    case_id: Optional[str] = None,
    call_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    notes: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    recorded_by: str = "api",
) -> OutcomeRecord:
    """Record one recovery outcome.

    ``verified`` requires a named verifier and a source: an anonymous
    "verified" claim is rejected instead of silently downgraded.
    """
    if voice_repo.get_lead(session, business_id, lead_id) is None:
        raise LeadNotFoundError(lead_id)
    if verified and not (verified_by and verification_source):
        raise InvalidParameterError(
            "a verified outcome requires both verified_by and verification_source"
        )
    if revenue_amount is not None and revenue_amount < 0:
        raise InvalidParameterError("revenue_amount must not be negative")

    outcome = OutcomeRecord(
        id=_prefixed_id("outc"),
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        call_id=call_id,
        workflow_run_id=workflow_run_id,
        outcome_type=outcome_type,
        verified=bool(verified),
        verified_by=verified_by if verified else None,
        verification_source=verification_source if verified else None,
        verified_at=_utcnow() if verified else None,
        revenue_amount=revenue_amount,
        currency=currency,
        notes=notes,
        evidence=evidence or {},
        recorded_by=recorded_by,
    )
    session.add(outcome)
    voice_repo.add_audit_log(
        session,
        action="outcome.recorded",
        business_id=business_id,
        actor=recorded_by,
        entity_type="outcome_record",
        entity_id=outcome.id,
        decision="verified" if verified else "unverified",
        reason=f"outcome {outcome_type} recorded",
        metadata={"lead_id": lead_id, "revenue_amount": str(revenue_amount)},
    )
    session.flush()
    return outcome


def record_cost(
    session: Session,
    *,
    business_id: str,
    cost_type: str,
    amount: float,
    currency: str = "INR",
    source: str = "manual",
    lead_id: Optional[str] = None,
    call_id: Optional[str] = None,
    case_id: Optional[str] = None,
    incurred_at: Optional[datetime] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> CostRecord:
    """Record one real incurred cost (never an estimate)."""
    if amount < 0:
        raise InvalidParameterError("amount must not be negative")
    record = CostRecord(
        id=_prefixed_id("cost"),
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        call_id=call_id,
        cost_type=cost_type,
        amount=amount,
        currency=currency,
        source=source,
        incurred_at=incurred_at or _utcnow(),
        detail=detail or {},
    )
    session.add(record)
    session.flush()
    return record


def record_revenue(
    session: Session,
    *,
    business_id: str,
    amount: float,
    verified: bool = False,
    verified_by: Optional[str] = None,
    currency: str = "INR",
    case_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    outcome_id: Optional[str] = None,
    source: str = "manual",
    detail: Optional[Dict[str, Any]] = None,
) -> RevenueRecord:
    """Record one revenue row.

    Unverified rows are stored as *pipeline*; only verified rows with a
    named verifier count toward ROI.
    """
    if amount < 0:
        raise InvalidParameterError("amount must not be negative")
    if verified and not verified_by:
        raise InvalidParameterError("verified revenue requires a named verifier")
    record = RevenueRecord(
        id=_prefixed_id("rev"),
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        outcome_id=outcome_id,
        amount=amount,
        currency=currency,
        verified=bool(verified) and bool(verified_by),
        source=source,
        verified_at=_utcnow() if (verified and verified_by) else None,
        detail=detail or {},
    )
    session.add(record)
    session.flush()
    return record


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _rate(qualified: int, total: int, minimum: int) -> Optional[float]:
    """A rate below the minimum sample is ``None`` — never a fake percent."""
    if total < minimum:
        return None
    return round(qualified / total * 100, 2)


def funnel_summary(
    session: Session,
    settings: Settings,
    business_id: str,
    *,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Call → connected → qualified funnel with sample-size honesty."""
    now = now or _utcnow()
    start = now - timedelta(days=days)
    calls = list(
        session.execute(
            select(voice_repo.Call).where(
                voice_repo.Call.business_id == business_id,
                voice_repo.Call.created_at >= start,
            )
        ).scalars().all()
    )
    total_calls = len(calls)
    connected = sum(
        1
        for c in calls
        if c.connected_at is not None or c.duration_seconds
    )
    completed = sum(1 for c in calls if c.status == "completed")
    blocked = sum(1 for c in calls if c.status == "blocked")

    outcomes = list(
        session.execute(
            select(voice_repo.CallOutcome).where(
                voice_repo.CallOutcome.business_id == business_id,
                voice_repo.CallOutcome.created_at >= start,
            )
        ).scalars().all()
    )
    qualified = sum(1 for o in outcomes if o.qualified is True)
    refused = sum(1 for o in outcomes if o.refusal_category)
    minimum = settings.analytics_min_sample_per_window

    connect_rate = _rate(connected, total_calls, minimum)
    qualification_rate = _rate(qualified, len(outcomes), minimum)
    return {
        "business_id": business_id,
        "window_days": days,
        "calls": total_calls,
        "connected": connected,
        "completed": completed,
        "blocked": blocked,
        "outcomes": len(outcomes),
        "qualified": qualified,
        "refused": refused,
        "connect_rate_percent": connect_rate,
        "qualification_rate_percent": qualification_rate,
        "min_sample_per_window": minimum,
        "insufficient_data": connect_rate is None or qualification_rate is None,
        "note": (
            "Rates below the configured minimum sample are omitted "
            "instead of being computed from noise."
        ),
    }


def leakage_report(
    session: Session,
    settings: Settings,
    business_id: str,
    *,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Opportunity-leakage flags from persisted conversation analyses.

    Leakage is only reported when enough calls were actually analysed;
    below the threshold the report says ``insufficient_data`` rather
    than inventing a percentage.
    """
    now = now or _utcnow()
    start = now - timedelta(days=days)
    analyses = list(
        session.execute(
            select(ConversationAnalysis).where(
                ConversationAnalysis.business_id == business_id,
                ConversationAnalysis.analyzed_at >= start,
            )
        ).scalars().all()
    )
    analysed = len(analyses)
    flagged = [a for a in analyses if a.leakage_flags]
    flag_counts: Dict[str, int] = {}
    for analysis in flagged:
        for flag in analysis.leakage_flags:
            name = flag if isinstance(flag, str) else str((flag or {}).get("flag", "unknown"))
            flag_counts[name] = flag_counts.get(name, 0) + 1

    threshold = settings.analytics_min_calls_for_leakage
    sufficient = analysed >= threshold
    return {
        "business_id": business_id,
        "window_days": days,
        "analysed_calls": analysed,
        "flagged_calls": len(flagged),
        "leakage_percent": (
            round(len(flagged) / analysed * 100, 2) if sufficient and analysed else None
        ),
        "flags": sorted(
            ({"flag": name, "count": count} for name, count in flag_counts.items()),
            key=lambda item: (-item["count"], item["flag"]),
        ),
        "min_calls_for_leakage": threshold,
        "insufficient_data": not sufficient,
        "note": (
            f"At least {threshold} analysed calls are required before a "
            "leakage percentage is reported."
        ),
    }


def roi_summary(
    session: Session,
    business_id: str,
    *,
    days: int = 90,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """ROI from verified revenue and real costs only."""
    now = now or _utcnow()
    start = now - timedelta(days=days)

    costs = list(
        session.execute(
            select(CostRecord).where(
                CostRecord.business_id == business_id,
                CostRecord.incurred_at >= start,
            )
        ).scalars().all()
    )
    revenues = list(
        session.execute(
            select(RevenueRecord).where(
                RevenueRecord.business_id == business_id,
                RevenueRecord.created_at >= start,
            )
        ).scalars().all()
    )
    verified = [r for r in revenues if r.verified]
    unverified = [r for r in revenues if not r.verified]

    total_costs = round(sum(float(c.amount) for c in costs), 2)
    verified_revenue = round(sum(float(r.amount) for r in verified), 2)
    pipeline = round(sum(float(r.amount) for r in unverified), 2)
    roi = (
        round((verified_revenue - total_costs) / total_costs, 4)
        if total_costs > 0
        else None
    )
    return {
        "business_id": business_id,
        "window_days": days,
        "total_costs": total_costs,
        "verified_revenue": verified_revenue,
        "unverified_pipeline": pipeline,
        "roi": roi,
        "cost_rows": len(costs),
        "revenue_rows": len(revenues),
        "verified_revenue_rows": len(verified),
        "note": (
            "ROI uses verified revenue and recorded costs only. "
            "Unverified pipeline is reported separately and never counted."
        ),
    }


def record_call_costs_from_calls(
    session: Session,
    business_id: str,
    *,
    days: int = 30,
    now: Optional[datetime] = None,
) -> int:
    """Persist provider-reported call costs that have no cost row yet.

    Only calls with a real provider-reported ``cost`` number produce a
    row; a simulated or blocked call never generates a cost.
    """
    now = now or _utcnow()
    start = now - timedelta(days=days)
    calls = list(
        session.execute(
            select(voice_repo.Call).where(
                voice_repo.Call.business_id == business_id,
                voice_repo.Call.created_at >= start,
                voice_repo.Call.cost.isnot(None),
            )
        ).scalars().all()
    )
    existing = {
        row[0]
        for row in session.execute(
            select(CostRecord.call_id).where(
                CostRecord.business_id == business_id,
                CostRecord.call_id.isnot(None),
            )
        ).all()
    }
    created = 0
    for call in calls:
        if call.id in existing or call.cost is None:
            continue
        record_cost(
            session,
            business_id=business_id,
            cost_type="voice",
            amount=float(call.cost),
            currency=call.currency or "INR",
            source=f"provider:{call.provider}",
            lead_id=call.lead_id,
            call_id=call.id,
            incurred_at=call.ended_at or call.created_at,
            detail={"provider": call.provider, "status": call.status},
        )
        created += 1
    return created
