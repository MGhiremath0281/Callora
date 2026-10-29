"""Honest, data-backed evaluation of an implemented recommendation.

The evaluator never fabricates an improvement. It compares persisted call
outcomes in a baseline window with a post-implementation window, reports
the measured difference, and explicitly marks the result as insufficient
when either window does not contain enough observed calls. The result is
never presented as a causal claim.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.recommendation_agent import models
from app.modules.voice.models import Call, CallOutcome


#: Minimum number of observed calls in *each* window before a comparison
#: is reported as evaluated. Below this the result stays
#: ``insufficient_data`` instead of implying a trend from noise.
MIN_SAMPLE_PER_WINDOW = 5


def _metric_block(outcomes: list[CallOutcome], calls: int) -> Dict[str, Any]:
    total = len(outcomes)
    qualified = sum(1 for o in outcomes if o.qualified is True)
    refused = sum(1 for o in outcomes if o.refusal_category)
    handoff = sum(1 for o in outcomes if o.handoff_required)
    return {
        "observed_outcomes": total,
        "calls": calls,
        "qualified": qualified,
        "refused": refused,
        "handoff_required": handoff,
        "qualification_rate": round(qualified / total * 100, 2) if total else None,
        "refusal_rate": round(refused / total * 100, 2) if total else None,
        "handoff_rate": round(handoff / total * 100, 2) if total else None,
    }


def _outcomes_between(
    db: Session, business_id: str, start: datetime, end: datetime
) -> tuple[list[CallOutcome], int]:
    outcomes = list(
        db.execute(
            select(CallOutcome).where(
                CallOutcome.business_id == business_id,
                CallOutcome.created_at >= start,
                CallOutcome.created_at < end,
            )
        ).scalars()
    )
    calls = (
        db.execute(
            select(Call.id).where(
                Call.business_id == business_id,
                Call.created_at >= start,
                Call.created_at < end,
            )
        ).all()
    )
    return outcomes, len(calls)


def evaluate_recommendation(
    db: Session,
    recommendation: models.Recommendation,
    *,
    baseline_days: int = 30,
    post_days: int = 30,
    now: Optional[datetime] = None,
) -> models.RecommendationOutcome:
    """Evaluate one recommendation using persisted outcomes only."""
    now = now or datetime.now(timezone.utc)
    outcome = db.execute(
        select(models.RecommendationOutcome).where(
            models.RecommendationOutcome.recommendation_id == recommendation.id,
            models.RecommendationOutcome.business_id == recommendation.business_id,
        )
    ).scalar_one_or_none()
    if outcome is None:
        outcome = models.RecommendationOutcome(
            recommendation_id=recommendation.id,
            business_id=recommendation.business_id,
        )
        db.add(outcome)

    if not outcome.implemented or outcome.implementation_date is None:
        outcome.evaluation_status = "not_implemented"
        outcome.notes = (
            "Evaluation requires the recommendation to be marked implemented "
            "with an implementation date."
        )
        db.flush()
        return outcome

    implemented_at = outcome.implementation_date
    if implemented_at.tzinfo is None:
        implemented_at = implemented_at.replace(tzinfo=timezone.utc)
    baseline_start = implemented_at - timedelta(days=baseline_days)
    post_end = min(now, implemented_at + timedelta(days=post_days))

    baseline_outcomes, baseline_calls = _outcomes_between(
        db, recommendation.business_id, baseline_start, implemented_at
    )
    post_outcomes, post_calls = _outcomes_between(
        db, recommendation.business_id, implemented_at, post_end
    )

    baseline = _metric_block(baseline_outcomes, baseline_calls)
    post = _metric_block(post_outcomes, post_calls)

    outcome.baseline_period_start = baseline_start
    outcome.baseline_period_end = implemented_at
    outcome.post_period_start = implemented_at
    outcome.post_period_end = post_end
    outcome.baseline_metrics = baseline
    outcome.post_metrics = post
    outcome.sample_size_baseline = baseline["observed_outcomes"]
    outcome.sample_size_post = post["observed_outcomes"]
    outcome.is_causal_claim = False

    changes: Dict[str, Any] = {}
    for key in ("qualification_rate", "refusal_rate", "handoff_rate"):
        before, after = baseline.get(key), post.get(key)
        if before is not None and after is not None:
            changes[key] = {
                "baseline": before,
                "post": after,
                "absolute_change": round(after - before, 2),
                "direction": (
                    "unchanged"
                    if after == before
                    else ("increase" if after > before else "decrease")
                ),
            }
    outcome.observed_change = changes

    if (
        baseline["observed_outcomes"] < MIN_SAMPLE_PER_WINDOW
        or post["observed_outcomes"] < MIN_SAMPLE_PER_WINDOW
    ):
        outcome.evaluation_status = "insufficient_data"
        outcome.notes = (
            "Not enough observed call outcomes in the baseline and/or post "
            f"window (minimum {MIN_SAMPLE_PER_WINDOW} each). No effectiveness "
            "claim is made."
        )
    else:
        outcome.evaluation_status = "evaluated"
        outcome.notes = (
            "Observed association only; this is not a causal claim. Other "
            "changes in the business may explain the difference."
        )
    db.flush()
    return outcome
