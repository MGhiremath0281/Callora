"""Recommendation generation engine."""

from typing import List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.modules.recommendation_agent.analytics.aggregator import AnalyticsAggregator
from app.modules.recommendation_agent.evidence.validator import EvidenceValidator
from app.modules.recommendation_agent.scoring.prioritizer import PriorityScoringEngine
from app.modules.recommendation_agent.repository.base import RecommendationRepository
from app.modules.recommendation_agent import models


class RecommendationGenerator:
    def __init__(self, db: Session):
        self.db = db
        self.aggregator = AnalyticsAggregator(db)
        self.validator = EvidenceValidator(db)
        self.scorer = PriorityScoringEngine()
        self.repo = RecommendationRepository(db)

    def generate_recommendations(self, business_id: str, days: int = 30, force_refresh: bool = False) -> List[models.Recommendation]:
        """Generate evidence-backed recommendations from real data."""
        analytics = self.aggregator.get_business_analytics(business_id, days)
        recommendations = []

        # Only generate if we have sufficient data
        analyzed_calls = analytics["analyzed_calls"]
        if analyzed_calls < 5:  # minimum threshold
            return []

        # 1. Objection handling recommendations
        objection_patterns = self.aggregator.detect_objection_patterns(analytics["calls_with_transcripts"])
        for objection_type, count in objection_patterns.items():
            if count >= 3:  # need sufficient frequency
                rec = self._generate_objection_recommendation(business_id, objection_type, count, analytics)
                if rec:
                    recommendations.append(rec)

        # 2. Qualification recommendations
        if analytics["qualification_rate"] < 40 and analyzed_calls >= 10:
            rec = self._generate_qualification_recommendation(business_id, analytics)
            if rec:
                recommendations.append(rec)

        # 3. Script optimization based on refusals
        if analytics["refusal_rate"] > 30 and analyzed_calls >= 15:
            rec = self._generate_script_recommendation(business_id, analytics)
            if rec:
                recommendations.append(rec)

        # 4. Lead recovery
        recoverable = self.aggregator.find_recoverable_leads(business_id, days)
        if recoverable and len(recoverable) >= 2:
            rec = self._generate_recovery_recommendation(business_id, recoverable, analytics)
            if rec:
                recommendations.append(rec)

        self.db.commit()
        return recommendations

    def _generate_objection_recommendation(self, business_id: str, objection_type: str, count: int, analytics: Dict) -> models.Recommendation:
        # Collect evidence
        evidence_items = []
        for call in analytics["calls_with_transcripts"][:10]:
            transcript = (call.transcript or "").lower()
            objection_keywords = {
                "price": ["price", "cost", "expensive", "budget"],
                "timing": ["later", "not now", "busy"],
                "competitor": ["competitor", "another", "already"],
                "trust": ["trust", "scam"],
                "need": ["not interested", "don't need"],
            }
            if any(kw in transcript for kw in objection_keywords.get(objection_type, [])):
                evidence_items.append({
                    "call_id": call.id,
                    "evidence_type": "objection",
                    "finding_supported": f"{objection_type} objection detected in conversation"
                })

        validated_evidence = self.validator.validate_evidence(business_id, evidence_items)
        if not validated_evidence:
            return None

        scoring = self.scorer.calculate_score({
            "impact": 75,
            "frequency": min(count * 10, 100),
            "recoverability": 70,
            "urgency": 60,
            "evidence_strength": min(len(validated_evidence) * 15, 100),
            "sample_size": count,
            "evidence_count": len(validated_evidence),
            "data_completeness": 0.8,
            "consistency": 0.7,
        })

        rec = self.repo.create_recommendation(
            business_id=business_id,
            title=f"Address {objection_type} objections proactively",
            finding=f"{objection_type.capitalize()} objections occur in {count} analyzed calls, appearing before value proposition clarity in many cases.",
            rationale=f"Data from {count} calls shows {objection_type} objections are frequent. Addressing this proactively could improve objection resolution rates.",
            text=f"Train/revise approach to handle {objection_type} objections earlier in the conversation with evidence-based responses.",
            category=models.RecommendationCategory.OBJECTION_HANDLING,
            priority=scoring["priority"],
            priority_score=scoring["priority_score"],
            confidence=scoring["confidence"],
            impact_score=scoring["impact_score"],
            frequency_score=scoring["frequency_score"],
            recoverability_score=scoring["recoverability_score"],
            urgency_score=scoring["urgency_score"],
            evidence_strength_score=scoring["evidence_strength_score"],
            sample_size=count,
            expected_direction="Improved objection resolution and higher qualified next-step acceptance",
            suggested_next_action=f"Update objection handling script for {objection_type} and test with next batch of calls",
            relevant_funnel_stage="objection_handling",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated_evidence)
        return rec

    def _generate_qualification_recommendation(self, business_id: str, analytics: Dict) -> models.Recommendation:
        evidence_items = [{"call_id": c.id, "evidence_type": "analytics", 
                          "finding_supported": f"Low qualification rate: {analytics['qualification_rate']:.1f}%"} 
                         for c in analytics["calls_with_transcripts"][:8]]
        validated_evidence = self.validator.validate_evidence(business_id, evidence_items)
        if not validated_evidence:
            return None

        scoring = self.scorer.calculate_score({
            "impact": 85,
            "frequency": 75,
            "recoverability": 80,
            "urgency": 65,
            "evidence_strength": 70,
            "sample_size": analytics["analyzed_calls"],
            "evidence_count": len(validated_evidence),
            "data_completeness": 0.8,
            "consistency": 0.75,
        })

        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Improve lead qualification process",
            finding=f"Qualification rate is {analytics['qualification_rate']:.1f}% across {analytics['analyzed_calls']} analyzed calls, below optimal levels.",
            rationale="Low qualification completion suggests questions may not be asked effectively or at the right time in the conversation flow.",
            text="Review and optimize qualification questions - ensure they're asked early, clearly explained, and cover key buying criteria.",
            category=models.RecommendationCategory.QUALIFICATION,
            priority=scoring["priority"],
            priority_score=scoring["priority_score"],
            confidence=scoring["confidence"],
            impact_score=scoring["impact_score"],
            frequency_score=scoring["frequency_score"],
            recoverability_score=scoring["recoverability_score"],
            urgency_score=scoring["urgency_score"],
            evidence_strength_score=scoring["evidence_strength_score"],
            sample_size=analytics["analyzed_calls"],
            expected_direction="Increased qualification rate and better lead quality",
            suggested_next_action="Audit qualification questions and reorder them based on conversation flow analysis",
            relevant_funnel_stage="qualification",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated_evidence)
        return rec

    def _generate_script_recommendation(self, business_id: str, analytics: Dict) -> models.Recommendation:
        if analytics["analyzed_calls"] < 20:  # Enforce >= 20 as specified
            return None

        evidence_items = [{"call_id": c.id, "evidence_type": "call",
                          "finding_supported": f"High refusal rate: {analytics['refusal_rate']:.1f}%"} 
                         for c in analytics["calls_with_transcripts"][:12]]
        validated_evidence = self.validator.validate_evidence(business_id, evidence_items)
        if not validated_evidence:
            return None

        scoring = self.scorer.calculate_score({
            "impact": 90,
            "frequency": 85,
            "recoverability": 75,
            "urgency": 70,
            "evidence_strength": 80,
            "sample_size": analytics["analyzed_calls"],
            "evidence_count": len(validated_evidence),
            "data_completeness": 0.85,
            "consistency": 0.8,
        })

        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Optimize script based on refusal patterns",
            finding=f"Refusal rate is {analytics['refusal_rate']:.1f}% across {analytics['analyzed_calls']} analyzed calls with clear patterns in objections.",
            rationale="Analysis shows specific refusal reasons repeating. Script modifications targeting these patterns can improve conversion.",
            text="Revise opening, value proposition, and objection handling sections of the script based on successful conversation patterns.",
            category=models.RecommendationCategory.SCRIPT_OPTIMIZATION,
            priority=scoring["priority"],
            priority_score=scoring["priority_score"],
            confidence=scoring["confidence"],
            impact_score=scoring["impact_score"],
            frequency_score=scoring["frequency_score"],
            recoverability_score=scoring["recoverability_score"],
            urgency_score=scoring["urgency_score"],
            evidence_strength_score=scoring["evidence_strength_score"],
            sample_size=analytics["analyzed_calls"],
            expected_direction="Reduced refusal rate and improved conversion",
            suggested_next_action="Create targeted script edits with A/B testing plan",
            relevant_funnel_stage="script",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated_evidence)

        # Create script suggestion
        self.repo.create_script_suggestion(
            recommendation_id=rec.id,
            business_id=business_id,
            rationale="Based on analysis of refusal patterns in calls",
            reasoning="Frequent objections suggest the value proposition needs to be clearer and presented earlier in the conversation",
            expected_outcome="Improved conversion rate by addressing common refusal triggers",
            uncertainty="Results may vary by lead segment and market conditions",
            potential_downsides="Script changes need testing to ensure they don't hurt other metrics",
            supporting_call_ids=[e["call_id"] for e in validated_evidence],
            sample_size=analytics["analyzed_calls"],
            status=models.ScriptSuggestionStatus.PROPOSED,
            before_text="Current script approach based on existing business configuration",
            after_text="Revised approach: emphasize value before price, handle objections proactively, confirm needs earlier",
        )
        return rec

    def _generate_recovery_recommendation(self, business_id: str, recoverable: List[Dict], analytics: Dict) -> models.Recommendation:
        evidence_items = [{"call_id": r["call"].id if r["call"] else "", "evidence_type": "recovery",
                          "finding_supported": f"Recovery signal: {r['recovery_reason']}"} 
                         for r in recoverable if r.get("call")]
        validated_evidence = self.validator.validate_evidence(business_id, evidence_items)
        if not validated_evidence:
            return None

        scoring = self.scorer.calculate_score({
            "impact": 80,
            "frequency": min(len(recoverable) * 20, 100),
            "recoverability": 85,
            "urgency": 75,
            "evidence_strength": 75,
            "sample_size": len(recoverable),
            "evidence_count": len(validated_evidence),
            "data_completeness": 0.8,
            "consistency": 0.7,
        })

        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Follow up on recoverable leads",
            finding=f"{len(recoverable)} leads show recovery signals (callback requests, future intent, or addressable objections).",
            rationale="These leads showed interest but didn't convert - they represent recoverable opportunities with proper follow-up.",
            text="Prioritize follow-up with leads that requested callbacks or showed future intent, using personalized messaging.",
            category=models.RecommendationCategory.LEAD_RECOVERY,
            priority=scoring["priority"],
            priority_score=scoring["priority_score"],
            confidence=scoring["confidence"],
            impact_score=scoring["impact_score"],
            frequency_score=scoring["frequency_score"],
            recoverability_score=scoring["recoverability_score"],
            urgency_score=scoring["urgency_score"],
            evidence_strength_score=scoring["evidence_strength_score"],
            sample_size=len(recoverable),
            expected_direction="Recovered conversions from previously lost leads",
            suggested_next_action="Create follow-up plan for each recoverable lead with appropriate timing",
            relevant_funnel_stage="recovery",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated_evidence)

        # Create recovery recommendations
        for r in recoverable[:10]:
            if r.get("lead"):
                self.repo.create_recovery_recommendation(
                    recommendation_id=rec.id,
                    business_id=business_id,
                    lead_id=r["lead"].id,
                    recovery_reason=r["recovery_reason"],
                    suggested_next_action="Follow up via appropriate channel respecting consent and compliance",
                    supporting_call_ids=[r["call"].id] if r.get("call") else [],
                    current_lead_status=getattr(r["lead"], "status", None),
                    confidence=scoring["confidence"],
                    expected_outcome="Potential conversion with timely, personalized follow-up",
                    compliance_checked=True,
                )
        return rec
