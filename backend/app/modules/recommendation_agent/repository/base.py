"""Repository layer for Recommendation Agent."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from app.modules.recommendation_agent import models


class RecommendationRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_recommendation(self, **kwargs) -> models.Recommendation:
        rec = models.Recommendation(**kwargs)
        self.db.add(rec)
        self.db.flush()
        return rec

    def get_recommendation(self, business_id: str, recommendation_id: str) -> Optional[models.Recommendation]:
        return self.db.query(models.Recommendation).filter(
            and_(models.Recommendation.id == recommendation_id,
                 models.Recommendation.business_id == business_id)
        ).first()

    def list_recommendations(self, business_id: str, status: Optional[str] = None,
                            category: Optional[str] = None,
                            limit: int = 50, offset: int = 0) -> List[models.Recommendation]:
        query = self.db.query(models.Recommendation).filter(
            models.Recommendation.business_id == business_id
        )
        if status:
            query = query.filter(models.Recommendation.status == status)
        if category:
            query = query.filter(models.Recommendation.category == category)
        return query.order_by(desc(models.Recommendation.priority_score)).offset(offset).limit(limit).all()

    def count_recommendations(self, business_id: str, status: Optional[str] = None) -> int:
        query = self.db.query(models.Recommendation).filter(
            models.Recommendation.business_id == business_id
        )
        if status:
            query = query.filter(models.Recommendation.status == status)
        return query.count()

    def update_status(self, recommendation: models.Recommendation, new_status: str,
                      actor: str = "system", reason: Optional[str] = None) -> models.Recommendation:
        from_status = recommendation.status
        recommendation.status = new_status
        recommendation.updated_at = datetime.now(timezone.utc)
        audit = models.RecommendationAuditLog(
            recommendation_id=recommendation.id,
            business_id=recommendation.business_id,
            actor=actor,
            action="status_change",
            from_status=from_status,
            to_status=new_status,
            reason=reason,
        )
        self.db.add(audit)
        self.db.flush()
        return recommendation

    def add_evidence(self, recommendation_id: str, business_id: str, evidence_items: List[Dict[str, Any]]):
        for item in evidence_items:
            ev = models.RecommendationEvidence(
                recommendation_id=recommendation_id,
                business_id=business_id,
                call_id=item.get("call_id"),
                lead_id=item.get("lead_id"),
                evidence_type=item.get("evidence_type", "call"),
                finding_supported=item.get("finding_supported", ""),
                transcript_excerpt=item.get("transcript_excerpt"),
                timestamp_in_call=item.get("timestamp_in_call"),
                evidence_data=item.get("evidence_data", {}),
                is_valid=item.get("is_valid", True),
            )
            self.db.add(ev)
        self.db.flush()

    def get_evidence(self, recommendation_id: str) -> List[models.RecommendationEvidence]:
        return self.db.query(models.RecommendationEvidence).filter(
            models.RecommendationEvidence.recommendation_id == recommendation_id,
            models.RecommendationEvidence.is_valid == True
        ).all()

    def create_script_suggestion(self, **kwargs) -> models.ScriptSuggestion:
        ssg = models.ScriptSuggestion(**kwargs)
        self.db.add(ssg)
        self.db.flush()
        return ssg

    def get_script_suggestion(self, suggestion_id: str) -> Optional[models.ScriptSuggestion]:
        return self.db.query(models.ScriptSuggestion).filter(
            models.ScriptSuggestion.id == suggestion_id
        ).first()

    def list_script_suggestions(self, business_id: str, recommendation_id: Optional[str] = None):
        query = self.db.query(models.ScriptSuggestion).filter(
            models.ScriptSuggestion.business_id == business_id
        )
        if recommendation_id:
            query = query.filter(models.ScriptSuggestion.recommendation_id == recommendation_id)
        return query.order_by(desc(models.ScriptSuggestion.created_at)).all()

    def create_recovery_recommendation(self, **kwargs) -> models.RecoveryRecommendation:
        rrec = models.RecoveryRecommendation(**kwargs)
        self.db.add(rrec)
        self.db.flush()
        return rrec

    def list_recovery_recommendations(self, business_id: str):
        return self.db.query(models.RecoveryRecommendation).filter(
            models.RecoveryRecommendation.business_id == business_id
        ).order_by(desc(models.RecoveryRecommendation.created_at)).all()

    def get_audit_history(self, recommendation_id: str):
        return self.db.query(models.RecommendationAuditLog).filter(
            models.RecommendationAuditLog.recommendation_id == recommendation_id
        ).order_by(models.RecommendationAuditLog.created_at).all()

    def get_or_create_outcome(self, recommendation_id: str, business_id: str) -> models.RecommendationOutcome:
        outcome = self.db.query(models.RecommendationOutcome).filter(
            models.RecommendationOutcome.recommendation_id == recommendation_id
        ).first()
        if not outcome:
            outcome = models.RecommendationOutcome(
                recommendation_id=recommendation_id,
                business_id=business_id,
            )
            self.db.add(outcome)
            self.db.flush()
        return outcome
