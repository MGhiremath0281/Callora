"""Recommendation Agent API routes.

Route order matters: static paths (``/analytics``, ``/recovery``,
``/generate``, ``/scripts/...``) are declared before the dynamic
``/{recommendation_id}`` route so they are never shadowed. Each route is
declared exactly once.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.errors import InvalidParameterError
from app.modules.recommendation_agent import models
from app.modules.recommendation_agent.analytics.aggregator import AnalyticsAggregator
from app.modules.recommendation_agent.errors import (
    RecommendationNotFoundError,
    ScriptSuggestionNotFoundError,
)
from app.modules.recommendation_agent.evaluation import evaluate_recommendation
from app.modules.recommendation_agent.generation.generator import RecommendationGenerator
from app.modules.recommendation_agent.repository.base import RecommendationRepository

router = APIRouter(
    prefix="/recommendations",
    tags=["Recommendation Agent"],
)

#: Allowed lifecycle transitions. Terminal states are never left.
STATUS_TRANSITIONS: Dict[str, set[str]] = {
    models.RecommendationStatus.NEW: {
        models.RecommendationStatus.REVIEWED,
        models.RecommendationStatus.APPROVED,
        models.RecommendationStatus.REJECTED,
        models.RecommendationStatus.SUPERSEDED,
    },
    models.RecommendationStatus.REVIEWED: {
        models.RecommendationStatus.APPROVED,
        models.RecommendationStatus.REJECTED,
        models.RecommendationStatus.IN_PROGRESS,
        models.RecommendationStatus.SUPERSEDED,
    },
    models.RecommendationStatus.APPROVED: {
        models.RecommendationStatus.IN_PROGRESS,
        models.RecommendationStatus.COMPLETED,
        models.RecommendationStatus.SUPERSEDED,
    },
    models.RecommendationStatus.IN_PROGRESS: {
        models.RecommendationStatus.COMPLETED,
        models.RecommendationStatus.SUPERSEDED,
    },
    models.RecommendationStatus.REJECTED: {models.RecommendationStatus.SUPERSEDED},
    models.RecommendationStatus.COMPLETED: {models.RecommendationStatus.SUPERSEDED},
    models.RecommendationStatus.SUPERSEDED: set(),
}


def business_credentials(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    business_id: str = Depends(authenticate_business),
):
    return business_id


def _rec_to_dict(rec: models.Recommendation, evidence_call_ids=None):
    evidence_call_ids = evidence_call_ids or []
    return {
        "id": rec.id,
        "business_id": rec.business_id,
        "title": rec.title,
        "finding": rec.finding,
        "rationale": rec.rationale,
        "text": rec.text,
        "category": rec.category,
        "priority": rec.priority,
        "priority_score": float(rec.priority_score),
        "confidence": float(rec.confidence),
        "impact_score": float(rec.impact_score),
        "frequency_score": float(rec.frequency_score),
        "recoverability_score": float(rec.recoverability_score),
        "urgency_score": float(rec.urgency_score),
        "evidence_strength_score": float(rec.evidence_strength_score),
        "sample_size": rec.sample_size,
        "expected_direction": rec.expected_direction,
        "suggested_next_action": rec.suggested_next_action,
        "relevant_funnel_stage": rec.relevant_funnel_stage,
        "relevant_script_section": rec.relevant_script_section,
        "status": rec.status,
        "source_type": rec.source_type,
        "evidence_call_ids": evidence_call_ids,
        "created_at": rec.created_at.isoformat(),
        "updated_at": rec.updated_at.isoformat(),
        "extra_metadata": rec.extra_metadata,
    }


def _rec_or_404(repo: RecommendationRepository, business_id: str, rec_id: str):
    rec = repo.get_recommendation(business_id, rec_id)
    if rec is None:
        raise RecommendationNotFoundError(rec_id)
    return rec


# ---------------------------------------------------------------------------
# Static routes (must be declared before /{recommendation_id})
# ---------------------------------------------------------------------------


@router.get("/analytics", status_code=status.HTTP_200_OK)
async def get_recommendation_analytics(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    analytics = AnalyticsAggregator(db).get_business_analytics(business_id)
    total = repo.count_recommendations(business_id)
    evaluated = (
        db.query(models.RecommendationOutcome)
        .filter(
            models.RecommendationOutcome.business_id == business_id,
            models.RecommendationOutcome.evaluation_status == "evaluated",
        )
        .count()
    )
    improved = 0
    for outcome in (
        db.query(models.RecommendationOutcome)
        .filter(
            models.RecommendationOutcome.business_id == business_id,
            models.RecommendationOutcome.evaluation_status == "evaluated",
        )
        .all()
    ):
        change = (outcome.observed_change or {}).get("qualification_rate") or {}
        if (change.get("absolute_change") or 0) > 0:
            improved += 1
    return {
        "business_id": business_id,
        "total_recommendations": total,
        "new_recommendations": repo.count_recommendations(
            business_id, status=models.RecommendationStatus.NEW
        ),
        "p1_count": db.query(models.Recommendation)
        .filter(
            models.Recommendation.business_id == business_id,
            models.Recommendation.priority == "P1",
        )
        .count(),
        "p2_count": db.query(models.Recommendation)
        .filter(
            models.Recommendation.business_id == business_id,
            models.Recommendation.priority == "P2",
        )
        .count(),
        "p3_count": db.query(models.Recommendation)
        .filter(
            models.Recommendation.business_id == business_id,
            models.Recommendation.priority == "P3",
        )
        .count(),
        "approved_count": repo.count_recommendations(
            business_id, status=models.RecommendationStatus.APPROVED
        ),
        "implemented_count": repo.count_recommendations(
            business_id, status=models.RecommendationStatus.COMPLETED
        ),
        "analyzed_calls": analytics["analyzed_calls"],
        "data_sufficiency": {
            "analyzed_calls": analytics["analyzed_calls"],
            "script_optimization_threshold": 20,
            "sufficient_for_script_optimization": analytics["analyzed_calls"] >= 20,
        },
        "effectiveness_summary": {
            "evaluated": evaluated,
            "improved": improved,
            "note": (
                "Improvement counts measured qualification-rate increases only; "
                "these are associations, not causal claims."
            ),
        },
    }


@router.get("/recovery", status_code=status.HTTP_200_OK)
async def get_recovery_opportunities(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    recoveries = repo.list_recovery_recommendations(business_id)
    return [
        {
            "id": r.id,
            "business_id": r.business_id,
            "lead_id": r.lead_id,
            "recovery_reason": r.recovery_reason,
            "suggested_next_action": r.suggested_next_action,
            "recommended_timing": r.recommended_timing,
            "supporting_call_ids": r.supporting_call_ids,
            "current_lead_status": r.current_lead_status,
            "confidence": float(r.confidence),
            "expected_outcome": r.expected_outcome,
            "status": r.status,
            "compliance_checked": r.compliance_checked,
            "executed": r.executed,
            "created_at": r.created_at.isoformat(),
        }
        for r in recoveries
    ]


@router.get("/scripts/versions", status_code=status.HTTP_200_OK)
async def list_script_versions(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    return [
        {
            "id": v.id,
            "version_number": v.version_number,
            "change_summary": v.change_summary,
            "source_suggestion_id": v.source_suggestion_id,
            "approved_by": v.approved_by,
            "approved_at": v.approved_at.isoformat() if v.approved_at else None,
            "is_active": v.is_active,
            "created_at": v.created_at.isoformat(),
        }
        for v in repo.list_script_versions(business_id)
    ]


@router.post(
    "/scripts/versions/{version_id}/activate", status_code=status.HTTP_200_OK
)
async def activate_script_version(
    version_id: str,
    request: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    actor = (request or {}).get("actor", "api")
    version = repo.activate_script_version(business_id, version_id, actor=actor)
    if version is None:
        raise ScriptSuggestionNotFoundError(version_id)
    db.commit()
    return {
        "id": version.id,
        "version_number": version.version_number,
        "is_active": True,
        "activated_by": actor,
        "message": "Script version activated; the previous version was deactivated.",
    }


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_recommendations(
    request: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    data = request or {}
    days = int(data.get("time_window_days", 30))
    if days < 1 or days > 365:
        raise InvalidParameterError("time_window_days must be between 1 and 365")
    generator = RecommendationGenerator(db)
    recs = generator.generate_recommendations(
        business_id, days=days, force_refresh=bool(data.get("force_refresh", False))
    )
    return {
        "generated": len(recs),
        "recommendations": [_rec_to_dict(r, []) for r in recs],
        "business_id": business_id,
    }


@router.get("", status_code=status.HTTP_200_OK)
async def list_recommendations(
    business_id: str = Depends(business_credentials),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    recs = repo.list_recommendations(
        business_id, status=status_filter, category=category, limit=limit, offset=offset
    )
    items = []
    for rec in recs:
        evidence = repo.get_evidence(rec.id)
        items.append(_rec_to_dict(rec, [e.call_id for e in evidence if e.call_id]))
    total = repo.count_recommendations(business_id, status=status_filter)
    return {"items": items, "total": total, "business_id": business_id}


# ---------------------------------------------------------------------------
# Dynamic routes
# ---------------------------------------------------------------------------


@router.get("/{recommendation_id}", status_code=status.HTTP_200_OK)
async def get_recommendation(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    evidence = repo.get_evidence(rec.id)
    audit = repo.get_audit_history(rec.id)
    scripts = repo.list_script_suggestions(business_id, recommendation_id)
    outcome = (
        db.query(models.RecommendationOutcome)
        .filter(
            models.RecommendationOutcome.recommendation_id == rec.id,
            models.RecommendationOutcome.business_id == business_id,
        )
        .first()
    )
    return {
        **_rec_to_dict(rec, [e.call_id for e in evidence if e.call_id]),
        "evidence": [
            {
                "id": e.id,
                "call_id": e.call_id,
                "evidence_type": e.evidence_type,
                "finding_supported": e.finding_supported,
                "transcript_excerpt": e.transcript_excerpt,
                "is_valid": e.is_valid,
            }
            for e in evidence
        ],
        "audit_history": [
            {
                "id": a.id,
                "actor": a.actor,
                "action": a.action,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "reason": a.reason,
                "created_at": a.created_at.isoformat(),
            }
            for a in audit
        ],
        "script_suggestions": [
            {
                "id": s.id,
                "status": s.status,
                "before_text": s.before_text,
                "after_text": s.after_text,
                "rationale": s.rationale,
            }
            for s in scripts
        ],
        "outcome": (
            {
                "implemented": outcome.implemented,
                "evaluation_status": outcome.evaluation_status,
                "sample_size_baseline": outcome.sample_size_baseline,
                "sample_size_post": outcome.sample_size_post,
            }
            if outcome
            else None
        ),
    }


@router.get("/{recommendation_id}/evidence", status_code=status.HTTP_200_OK)
async def get_recommendation_evidence(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    evidence = repo.get_evidence(rec.id)
    return [
        {
            "id": e.id,
            "call_id": e.call_id,
            "lead_id": e.lead_id,
            "evidence_type": e.evidence_type,
            "finding_supported": e.finding_supported,
            "transcript_excerpt": e.transcript_excerpt,
            "timestamp_in_call": e.timestamp_in_call,
            "is_valid": e.is_valid,
            "validated_at": e.validated_at.isoformat() if e.validated_at else None,
        }
        for e in evidence
    ]


@router.patch("/{recommendation_id}/status", status_code=status.HTTP_200_OK)
async def update_recommendation_status(
    recommendation_id: str,
    request: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    new_status = request.get("status")
    reason = request.get("reason")
    actor = request.get("actor", "api")
    if new_status not in STATUS_TRANSITIONS:
        raise InvalidParameterError(
            f"Invalid status. Must be one of {sorted(STATUS_TRANSITIONS)}"
        )
    allowed = STATUS_TRANSITIONS.get(rec.status, set())
    if new_status not in allowed:
        raise InvalidParameterError(
            f"Invalid transition {rec.status!r} -> {new_status!r}; "
            f"allowed from {rec.status!r}: {sorted(allowed) or 'none'}"
        )
    if new_status == models.RecommendationStatus.COMPLETED:
        # Completing is the implementation event: record when it happened so
        # the evaluator has a real baseline boundary.
        outcome = repo.get_or_create_outcome(rec.id, business_id)
        outcome.implemented = True
        outcome.implementation_date = datetime.now(timezone.utc)
        outcome.implemented_by = actor
    rec = repo.update_status(rec, new_status, actor=actor, reason=reason)
    db.commit()
    return {"id": rec.id, "status": rec.status, "message": "Status updated successfully"}


@router.post("/{recommendation_id}/implement", status_code=status.HTTP_200_OK)
async def mark_recommendation_implemented(
    recommendation_id: str,
    request: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    if rec.status not in {
        models.RecommendationStatus.APPROVED,
        models.RecommendationStatus.IN_PROGRESS,
    }:
        raise InvalidParameterError(
            "Only an approved or in-progress recommendation can be implemented"
        )
    data = request or {}
    actor = data.get("actor", "api")
    outcome = repo.get_or_create_outcome(rec.id, business_id)
    outcome.implemented = True
    outcome.implementation_date = datetime.now(timezone.utc)
    outcome.implemented_by = actor
    outcome.script_version_id = data.get("script_version_id")
    outcome.notes = data.get("notes")
    if rec.status == models.RecommendationStatus.APPROVED:
        repo.update_status(
            rec,
            models.RecommendationStatus.IN_PROGRESS,
            actor=actor,
            reason="implementation started",
        )
    db.commit()
    return {
        "id": rec.id,
        "implemented": True,
        "implementation_date": outcome.implementation_date.isoformat(),
        "evaluation_status": outcome.evaluation_status,
    }


@router.post("/{recommendation_id}/evaluate", status_code=status.HTTP_200_OK)
async def evaluate_recommendation_effectiveness(
    recommendation_id: str,
    request: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    data = request or {}
    baseline_days = int(data.get("baseline_days", 30))
    post_days = int(data.get("post_days", 30))
    if not (1 <= baseline_days <= 365 and 1 <= post_days <= 365):
        raise InvalidParameterError("baseline_days and post_days must be 1..365")
    outcome = evaluate_recommendation(
        db, rec, baseline_days=baseline_days, post_days=post_days
    )
    db.commit()
    return {
        "recommendation_id": rec.id,
        "evaluation_status": outcome.evaluation_status,
        "baseline_metrics": outcome.baseline_metrics,
        "post_metrics": outcome.post_metrics,
        "observed_change": outcome.observed_change,
        "sample_size_baseline": outcome.sample_size_baseline,
        "sample_size_post": outcome.sample_size_post,
        "is_causal_claim": False,
        "notes": outcome.notes,
    }


@router.get("/{recommendation_id}/script-suggestions", status_code=status.HTTP_200_OK)
async def get_script_suggestions(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    suggestions = repo.list_script_suggestions(business_id, recommendation_id)
    return [
        {
            "id": s.id,
            "status": s.status,
            "before_text": s.before_text,
            "after_text": s.after_text,
            "rationale": s.rationale,
            "reasoning": s.reasoning,
            "expected_outcome": s.expected_outcome,
            "uncertainty": s.uncertainty,
            "potential_downsides": s.potential_downsides,
            "supporting_call_ids": s.supporting_call_ids,
            "sample_size": s.sample_size,
            "created_at": s.created_at.isoformat(),
        }
        for s in suggestions
    ]


@router.post(
    "/{recommendation_id}/script-suggestions/{suggestion_id}/approve",
    status_code=status.HTTP_200_OK,
)
async def approve_script_suggestion(
    recommendation_id: str,
    suggestion_id: str,
    request: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = _rec_or_404(repo, business_id, recommendation_id)
    suggestion = repo.get_script_suggestion(suggestion_id)
    if (
        suggestion is None
        or suggestion.recommendation_id != recommendation_id
        or suggestion.business_id != business_id
    ):
        raise ScriptSuggestionNotFoundError(suggestion_id)
    if suggestion.status != models.ScriptSuggestionStatus.PROPOSED:
        raise InvalidParameterError(
            f"Only a proposed suggestion can be approved (current: {suggestion.status})"
        )
    data = request or {}
    actor = data.get("actor", "api")

    suggestion.status = models.ScriptSuggestionStatus.APPROVED
    suggestion.approved_by = actor
    suggestion.approved_at = datetime.now(timezone.utc)

    version = repo.create_script_version(
        business_id=business_id,
        version_number=repo.next_script_version_number(business_id),
        script_content=suggestion.proposed_script or suggestion.after_text,
        change_summary=f"Approved from suggestion {suggestion_id}",
        source_suggestion_id=suggestion_id,
        approved_by=actor,
        approved_at=datetime.now(timezone.utc),
        # Approval does not activate: activation is a separate, explicit
        # action so a reviewed script cannot go live by accident.
        is_active=False,
    )
    repo.update_status(
        rec,
        models.RecommendationStatus.APPROVED,
        actor=actor,
        reason="Script suggestion approved",
    )
    db.commit()
    return {
        "message": "Script suggestion approved; version created inactive",
        "suggestion_id": suggestion_id,
        "version_id": version.id,
        "version_number": version.version_number,
        "is_active": False,
        "next_step": f"/api/v1/recommendations/scripts/versions/{version.id}/activate",
    }
