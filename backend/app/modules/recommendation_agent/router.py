"""Recommendation Agent API routes."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.modules.recommendation_agent import models
from app.modules.recommendation_agent.repository.base import RecommendationRepository
from app.modules.recommendation_agent.generation.generator import RecommendationGenerator
from app.modules.recommendation_agent.errors import RecommendationNotFoundError, ScriptSuggestionNotFoundError
from app.modules.voice import repository as voice_repo
from datetime import datetime, timezone

router = APIRouter(
    prefix="/recommendations",
    tags=["Recommendation Agent"],
)


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


@router.get("/analytics", status_code=status.HTTP_200_OK)
async def get_recommendation_analytics(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    total = repo.count_recommendations(business_id)
    new_count = repo.count_recommendations(business_id, status="new")
    p1 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P1").count()
    p2 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P2").count()
    p3 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P3").count()
    approved = repo.count_recommendations(business_id, status="approved")
    implemented = repo.count_recommendations(business_id, status="completed")
    from app.modules.recommendation_agent.analytics.aggregator import AnalyticsAggregator
    agg = AnalyticsAggregator(db)
    analytics = agg.get_business_analytics(business_id)
    return {
        "business_id": business_id,
        "total_recommendations": total,
        "new_recommendations": new_count,
        "p1_count": p1,
        "p2_count": p2,
        "p3_count": p3,
        "approved_count": approved,
        "implemented_count": implemented,
        "analyzed_calls": analytics["analyzed_calls"],
        "data_sufficiency": {"analyzed_calls": analytics["analyzed_calls"], "sufficient": analytics["analyzed_calls"] >= 20},
        "effectiveness_summary": {"evaluated": 0, "improved": 0},
    }


@router.get("/recovery", status_code=status.HTTP_200_OK)
async def get_recovery_opportunities(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    recoveries = repo.list_recovery_recommendations(business_id)
    return [{"id": r.id, "business_id": r.business_id, "lead_id": r.lead_id,
             "recovery_reason": r.recovery_reason, "suggested_next_action": r.suggested_next_action,
             "recommended_timing": r.recommended_timing, "supporting_call_ids": r.supporting_call_ids,
             "current_lead_status": r.current_lead_status, "confidence": float(r.confidence),
             "expected_outcome": r.expected_outcome, "status": r.status,
             "created_at": r.created_at.isoformat()} for r in recoveries]


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
    recs = repo.list_recommendations(business_id, status=status_filter, category=category, limit=limit, offset=offset)
    items = []
    for rec in recs:
        evidence = repo.get_evidence(rec.id)
        call_ids = [e.call_id for e in evidence if e.call_id]
        items.append(_rec_to_dict(rec, call_ids))
    total = repo.count_recommendations(business_id, status=status_filter)
    return {"items": items, "total": total, "business_id": business_id}


@router.get("/{recommendation_id}", status_code=status.HTTP_200_OK)
async def get_recommendation(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = repo.get_recommendation(business_id, recommendation_id)
    if not rec:
        raise RecommendationNotFoundError(recommendation_id)
    evidence = repo.get_evidence(rec.id)
    call_ids = [e.call_id for e in evidence if e.call_id]
    audit = repo.get_audit_history(rec.id)
    scripts = repo.list_script_suggestions(business_id, recommendation_id)
    outcome = repo.get_or_create_outcome(rec.id, business_id)
    return {
        **_rec_to_dict(rec, call_ids),
        "evidence": [{"id": e.id, "call_id": e.call_id, "evidence_type": e.evidence_type,
                      "finding_supported": e.finding_supported, "transcript_excerpt": e.transcript_excerpt,
                      "is_valid": e.is_valid} for e in evidence],
        "audit_history": [{"id": a.id, "actor": a.actor, "action": a.action,
                           "from_status": a.from_status, "to_status": a.to_status,
                           "reason": a.reason, "created_at": a.created_at.isoformat()} for a in audit],
        "script_suggestions": [{"id": s.id, "status": s.status, "before_text": s.before_text,
                                "after_text": s.after_text, "rationale": s.rationale} for s in scripts],
        "outcome": {"implemented": outcome.implemented, "evaluation_status": outcome.evaluation_status,
                    "sample_size_baseline": outcome.sample_size_baseline,
                    "sample_size_post": outcome.sample_size_post},
    }


@router.get("/{recommendation_id}/evidence", status_code=status.HTTP_200_OK)
async def get_recommendation_evidence(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = repo.get_recommendation(business_id, recommendation_id)
    if not rec:
        raise RecommendationNotFoundError(recommendation_id)
    evidence = repo.get_evidence(rec.id)
    return [{"id": e.id, "call_id": e.call_id, "lead_id": e.lead_id,
             "evidence_type": e.evidence_type, "finding_supported": e.finding_supported,
             "transcript_excerpt": e.transcript_excerpt, "timestamp_in_call": e.timestamp_in_call,
             "is_valid": e.is_valid, "validated_at": e.validated_at.isoformat() if e.validated_at else None}
            for e in evidence]


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_recommendations(
    request: Dict[str, Any] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    data = request or {}
    days = data.get("time_window_days", 30)
    force_refresh = data.get("force_refresh", False)
    generator = RecommendationGenerator(db)
    recs = generator.generate_recommendations(business_id, days=days, force_refresh=force_refresh)
    return {"generated": len(recs), "recommendations": [_rec_to_dict(r, []) for r in recs], "business_id": business_id}


@router.patch("/{recommendation_id}/status", status_code=status.HTTP_200_OK)
async def update_recommendation_status(
    recommendation_id: str,
    request: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = repo.get_recommendation(business_id, recommendation_id)
    if not rec:
        raise RecommendationNotFoundError(recommendation_id)
    new_status = request.get("status")
    reason = request.get("reason")
    actor = request.get("actor", "api")
    valid_statuses = ["new", "reviewed", "approved", "rejected", "in_progress", "completed", "superseded"]
    if new_status not in valid_statuses:
        from app.errors import InvalidParameterError
        raise InvalidParameterError(f"Invalid status. Must be one of {valid_statuses}")
    rec = repo.update_status(rec, new_status, actor=actor, reason=reason)
    db.commit()
    return {"id": rec.id, "status": rec.status, "message": "Status updated successfully"}


@router.get("/{recommendation_id}/script-suggestions", status_code=status.HTTP_200_OK)
async def get_script_suggestions(
    recommendation_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = repo.get_recommendation(business_id, recommendation_id)
    if not rec:
        raise RecommendationNotFoundError(recommendation_id)
    suggestions = repo.list_script_suggestions(business_id, recommendation_id)
    return [{"id": s.id, "status": s.status, "before_text": s.before_text, "after_text": s.after_text,
             "rationale": s.rationale, "reasoning": s.reasoning, "expected_outcome": s.expected_outcome,
             "uncertainty": s.uncertainty, "potential_downsides": s.potential_downsides,
             "supporting_call_ids": s.supporting_call_ids, "sample_size": s.sample_size,
             "created_at": s.created_at.isoformat()} for s in suggestions]


@router.post("/{recommendation_id}/script-suggestions/{suggestion_id}/approve", status_code=status.HTTP_200_OK)
async def approve_script_suggestion(
    recommendation_id: str,
    suggestion_id: str,
    request: Dict[str, Any] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    rec = repo.get_recommendation(business_id, recommendation_id)
    if not rec:
        raise RecommendationNotFoundError(recommendation_id)
    suggestion = repo.get_script_suggestion(suggestion_id)
    if not suggestion or suggestion.recommendation_id != recommendation_id:
        raise ScriptSuggestionNotFoundError(suggestion_id)
    data = request or {}
    actor = data.get("actor", "api")
    suggestion.status = models.ScriptSuggestionStatus.APPROVED
    suggestion.approved_by = actor
    suggestion.approved_at = datetime.now(timezone.utc)
    # Create script version
    from app.modules.recommendation_agent import models as rec_models
    version = rec_models.ScriptVersion(
        business_id=business_id,
        version_number=suggestion.version_number,
        script_content=suggestion.proposed_script or suggestion.after_text,
        change_summary=f"Approved from suggestion {suggestion_id}",
        source_suggestion_id=suggestion_id,
        approved_by=actor,
        approved_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(version)
    repo.update_status(rec, models.RecommendationStatus.APPROVED, actor=actor, reason="Script suggestion approved")
    db.commit()
    return {"message": "Script suggestion approved", "suggestion_id": suggestion_id, "version_id": version.id}


@router.get("/analytics", status_code=status.HTTP_200_OK)
async def get_recommendation_analytics(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    total = repo.count_recommendations(business_id)
    new_count = repo.count_recommendations(business_id, status="new")
    p1 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P1").count()
    p2 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P2").count()
    p3 = db.query(models.Recommendation).filter(models.Recommendation.business_id == business_id,
                                                   models.Recommendation.priority == "P3").count()
    approved = repo.count_recommendations(business_id, status="approved")
    implemented = repo.count_recommendations(business_id, status="completed")
    from app.modules.recommendation_agent.analytics.aggregator import AnalyticsAggregator
    agg = AnalyticsAggregator(db)
    analytics = agg.get_business_analytics(business_id)
    return {
        "business_id": business_id,
        "total_recommendations": total,
        "new_recommendations": new_count,
        "p1_count": p1,
        "p2_count": p2,
        "p3_count": p3,
        "approved_count": approved,
        "implemented_count": implemented,
        "analyzed_calls": analytics["analyzed_calls"],
        "data_sufficiency": {"analyzed_calls": analytics["analyzed_calls"], "sufficient": analytics["analyzed_calls"] >= 20},
        "effectiveness_summary": {"evaluated": 0, "improved": 0},
    }


@router.get("/recovery", status_code=status.HTTP_200_OK)
async def get_recovery_opportunities(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    repo = RecommendationRepository(db)
    recoveries = repo.list_recovery_recommendations(business_id)
    return [{"id": r.id, "business_id": r.business_id, "lead_id": r.lead_id,
             "recovery_reason": r.recovery_reason, "suggested_next_action": r.suggested_next_action,
             "recommended_timing": r.recommended_timing, "supporting_call_ids": r.supporting_call_ids,
             "current_lead_status": r.current_lead_status, "confidence": float(r.confidence),
             "expected_outcome": r.expected_outcome, "status": r.status,
             "created_at": r.created_at.isoformat()} for r in recoveries]
