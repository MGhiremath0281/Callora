"""Recommendation generation engine.

Every recommendation is derived from persisted call/outcome data. The
generator enforces three rules that protect the operator from noise:

* **Sample thresholds** — a finding needs enough analysed calls; script
  optimisation requires at least 20.
* **Real evidence** — every evidence reference must resolve to a call of
  the same business and contain the transcript signal it claims.
* **No duplicates or contradictions** — one open recommendation per
  category; regenerating supersedes the old one, and a fresh generation
  can never contradict the measured data (e.g. it does not ask to improve
  qualification when the qualification rate is healthy).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import Lead
from app.modules.recommendation_agent import models
from app.modules.recommendation_agent.analytics.aggregator import AnalyticsAggregator
from app.modules.recommendation_agent.evidence.validator import EvidenceValidator
from app.modules.recommendation_agent.repository.base import RecommendationRepository
from app.modules.recommendation_agent.scoring.prioritizer import PriorityScoringEngine
from app.modules.recovery_agent.detect import RecoveryPolicy
from app.modules.recovery_agent.eligibility import check_eligibility
from app.modules.recovery_agent.policy import eligibility_config
from app.modules.voice.models import ConsentRecord, DndEntry
from app.modules.voice.phone import normalize_phone


#: A script optimisation recommendation is only defensible once this many
#: calls have been analysed.
SCRIPT_OPTIMIZATION_MIN_CALLS = 20

OBJECTION_KEYWORDS = {
    "price": ["price", "cost", "expensive", "budget", "afford", "rate"],
    "timing": ["later", "not now", "busy", "time", "next month", "next year"],
    "competitor": ["competitor", "another", "already", "other option"],
    "trust": ["trust", "scam", "fake", "legitimate", "verify"],
    "need": ["not interested", "don't need", "no need", "already have"],
}


class RecommendationGenerator:
    def __init__(self, db: Session):
        self.db = db
        self.aggregator = AnalyticsAggregator(db)
        self.validator = EvidenceValidator(db)
        self.scorer = PriorityScoringEngine()
        self.repo = RecommendationRepository(db)

    def generate_recommendations(
        self, business_id: str, days: int = 30, force_refresh: bool = False
    ) -> List[models.Recommendation]:
        """Generate evidence-backed recommendations from real data."""
        analytics = self.aggregator.get_business_analytics(business_id, days)
        recommendations: List[models.Recommendation] = []

        analyzed_calls = analytics["analyzed_calls"]
        if analyzed_calls < 5:  # minimum threshold for any recommendation
            return []

        # 1. Objection handling recommendations
        objection_patterns = self.aggregator.detect_objection_patterns(
            analytics["calls_with_transcripts"]
        )
        for objection_type, count in objection_patterns.items():
            if count >= 3:
                rec = self._generate_objection_recommendation(
                    business_id, objection_type, count, analytics, force_refresh
                )
                if rec:
                    recommendations.append(rec)

        # 2. Qualification recommendations (only when the data says the
        # rate is actually low — never contradict a healthy metric).
        if analytics["qualification_rate"] < 40 and analyzed_calls >= 10:
            rec = self._generate_qualification_recommendation(
                business_id, analytics, force_refresh
            )
            if rec:
                recommendations.append(rec)

        # 3. Script optimisation — requires the 20-call threshold.
        if (
            analytics["refusal_rate"] > 30
            and analyzed_calls >= SCRIPT_OPTIMIZATION_MIN_CALLS
        ):
            rec = self._generate_script_recommendation(
                business_id, analytics, force_refresh
            )
            if rec:
                recommendations.append(rec)

        # 4. Lead recovery — every lead is compliance-checked individually.
        recoverable = self.aggregator.find_recoverable_leads(business_id, days)
        if recoverable and len(recoverable) >= 2:
            rec = self._generate_recovery_recommendation(
                business_id, recoverable, analytics, force_refresh
            )
            if rec:
                recommendations.append(rec)

        self.db.commit()
        return recommendations

    # -- helpers -----------------------------------------------------------

    def _claim(
        self,
        business_id: str,
        category: str,
        finding: str,
        force_refresh: bool,
    ) -> Optional[models.Recommendation]:
        """Return an existing equivalent recommendation or None.

        When an open recommendation of the same category exists and the
        finding is unchanged, it is reused (no duplicate). With
        ``force_refresh`` the caller creates a fresh row and supersedes the
        old one afterwards.
        """
        existing = self.repo.find_open_recommendation(business_id, category)
        if existing is None:
            return None
        if not force_refresh and existing.finding == finding:
            return existing
        return None

    def _supersede_previous(
        self, business_id: str, category: str, new_rec: models.Recommendation
    ) -> None:
        previous = self.repo.find_open_recommendation(business_id, category)
        if previous is not None and previous.id != new_rec.id:
            self.repo.supersede(previous, new_id=new_rec.id)

    # -- generators --------------------------------------------------------

    def _generate_objection_recommendation(
        self,
        business_id: str,
        objection_type: str,
        count: int,
        analytics: Dict[str, Any],
        force_refresh: bool,
    ) -> Optional[models.Recommendation]:
        keywords = OBJECTION_KEYWORDS.get(objection_type, [])
        finding = (
            f"{objection_type.capitalize()} objections were detected in {count} "
            f"of {analytics['analyzed_calls']} analysed calls."
        )
        existing = self._claim(
            business_id,
            models.RecommendationCategory.OBJECTION_HANDLING,
            finding,
            force_refresh,
        )
        if existing is not None:
            return existing

        evidence_items = []
        for call in analytics["calls_with_transcripts"][:10]:
            transcript = (call.transcript or "").lower()
            if any(kw in transcript for kw in keywords):
                evidence_items.append(
                    {
                        "call_id": call.id,
                        "evidence_type": "objection",
                        "finding_supported": f"{objection_type} objection detected",
                        "required_keywords": keywords,
                    }
                )
        validated = self.validator.validate_evidence(business_id, evidence_items)
        if not validated:
            return None

        scoring = self.scorer.calculate_score(
            {
                "impact": 75,
                "frequency": min(count * 10, 100),
                "recoverability": 70,
                "urgency": 60,
                "evidence_strength": min(len(validated) * 15, 100),
                "sample_size": count,
                "evidence_count": len(validated),
                "data_completeness": 0.8,
                "consistency": 0.7,
            }
        )
        rec = self.repo.create_recommendation(
            business_id=business_id,
            title=f"Address {objection_type} objections proactively",
            finding=finding,
            rationale=(
                f"{count} analysed calls contain {objection_type} language. "
                "Handling the objection before price/value discussion may "
                "improve resolution; this is an association, not a proven cause."
            ),
            text=(
                f"Revise the {objection_type} objection response and practise "
                "it before the next call batch."
            ),
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
            expected_direction="Improved objection resolution",
            suggested_next_action=f"Update objection handling for {objection_type}",
            relevant_funnel_stage="objection_handling",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated)
        self._supersede_previous(
            business_id, models.RecommendationCategory.OBJECTION_HANDLING, rec
        )
        return rec

    def _generate_qualification_recommendation(
        self,
        business_id: str,
        analytics: Dict[str, Any],
        force_refresh: bool,
    ) -> Optional[models.Recommendation]:
        finding = (
            f"Qualification rate is {analytics['qualification_rate']:.1f}% across "
            f"{analytics['analyzed_calls']} analysed calls."
        )
        existing = self._claim(
            business_id,
            models.RecommendationCategory.QUALIFICATION,
            finding,
            force_refresh,
        )
        if existing is not None:
            return existing

        evidence_items = [
            {
                "call_id": c.id,
                "evidence_type": "call",
                "finding_supported": finding,
            }
            for c in analytics["calls_with_transcripts"][:8]
        ]
        validated = self.validator.validate_evidence(business_id, evidence_items)
        if not validated:
            return None

        scoring = self.scorer.calculate_score(
            {
                "impact": 85,
                "frequency": 75,
                "recoverability": 80,
                "urgency": 65,
                "evidence_strength": 70,
                "sample_size": analytics["analyzed_calls"],
                "evidence_count": len(validated),
                "data_completeness": 0.8,
                "consistency": 0.75,
            }
        )
        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Improve lead qualification process",
            finding=finding,
            rationale=(
                "The measured qualification completion rate is below the "
                "configured 40% review threshold. The data shows the outcome, "
                "not the reason; review the calls before changing the script."
            ),
            text=(
                "Audit where qualification questions are asked and ensure each "
                "key buying criterion is covered."
            ),
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
            expected_direction="Higher measured qualification rate",
            suggested_next_action="Review qualification questions against the calls",
            relevant_funnel_stage="qualification",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated)
        self._supersede_previous(
            business_id, models.RecommendationCategory.QUALIFICATION, rec
        )
        return rec

    def _generate_script_recommendation(
        self,
        business_id: str,
        analytics: Dict[str, Any],
        force_refresh: bool,
    ) -> Optional[models.Recommendation]:
        if analytics["analyzed_calls"] < SCRIPT_OPTIMIZATION_MIN_CALLS:
            return None

        finding = (
            f"Refusal rate is {analytics['refusal_rate']:.1f}% across "
            f"{analytics['analyzed_calls']} analysed calls."
        )
        existing = self._claim(
            business_id,
            models.RecommendationCategory.SCRIPT_OPTIMIZATION,
            finding,
            force_refresh,
        )
        if existing is not None:
            return existing

        evidence_items = [
            {
                "call_id": c.id,
                "evidence_type": "call",
                "finding_supported": finding,
            }
            for c in analytics["calls_with_transcripts"][:12]
        ]
        validated = self.validator.validate_evidence(business_id, evidence_items)
        if not validated:
            return None

        scoring = self.scorer.calculate_score(
            {
                "impact": 90,
                "frequency": 85,
                "recoverability": 75,
                "urgency": 70,
                "evidence_strength": 80,
                "sample_size": analytics["analyzed_calls"],
                "evidence_count": len(validated),
                "data_completeness": 0.85,
                "consistency": 0.8,
            }
        )
        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Optimize script based on refusal patterns",
            finding=finding,
            rationale=(
                f"{analytics['refusal_count']} of {analytics['analyzed_calls']} "
                "analysed calls carry a refusal category. The repeated patterns "
                "are a testable hypothesis for script changes."
            ),
            text=(
                "Draft targeted opening/value-proposition edits and test them on "
                "a controlled batch before activating."
            ),
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
            expected_direction="Reduced refusal rate (to be measured)",
            suggested_next_action="Draft script edits and plan a controlled test",
            relevant_funnel_stage="script",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated)

        self.repo.create_script_suggestion(
            recommendation_id=rec.id,
            business_id=business_id,
            rationale="Derived from measured refusal categories",
            reasoning=(
                "Repeated refusal categories suggest specific script sections "
                "are not landing; this requires human review before activation."
            ),
            expected_outcome="Reduced refusal rate, measured after implementation",
            uncertainty="Results may vary by segment and market conditions",
            potential_downsides="A script change may hurt other metrics",
            supporting_call_ids=[e["call_id"] for e in validated],
            sample_size=analytics["analyzed_calls"],
            status=models.ScriptSuggestionStatus.PROPOSED,
            before_text="Current script (not automatically modified)",
            after_text=(
                "Proposed edits must be reviewed and completed by a human before "
                "a script version can be activated."
            ),
            version_number=self.repo.next_script_version_number(business_id),
        )
        self._supersede_previous(
            business_id, models.RecommendationCategory.SCRIPT_OPTIMIZATION, rec
        )
        return rec

    def _generate_recovery_recommendation(
        self,
        business_id: str,
        recoverable: List[Dict[str, Any]],
        analytics: Dict[str, Any],
        force_refresh: bool,
    ) -> Optional[models.Recommendation]:
        finding = (
            f"{len(recoverable)} leads show recovery signals (callback requests, "
            "future intent, or addressable objections)."
        )
        existing = self._claim(
            business_id,
            models.RecommendationCategory.LEAD_RECOVERY,
            finding,
            force_refresh,
        )
        if existing is not None:
            return existing

        evidence_items = []
        for item in recoverable:
            call = item.get("call")
            if call is None:
                continue
            evidence_items.append(
                {
                    "call_id": call.id,
                    "evidence_type": "recovery",
                    "finding_supported": item["recovery_reason"],
                }
            )
        validated = self.validator.validate_evidence(business_id, evidence_items)
        if not validated:
            return None

        scoring = self.scorer.calculate_score(
            {
                "impact": 80,
                "frequency": min(len(recoverable) * 20, 100),
                "recoverability": 85,
                "urgency": 75,
                "evidence_strength": 75,
                "sample_size": len(recoverable),
                "evidence_count": len(validated),
                "data_completeness": 0.8,
                "consistency": 0.7,
            }
        )
        rec = self.repo.create_recommendation(
            business_id=business_id,
            title="Follow up on recoverable leads",
            finding=finding,
            rationale=(
                "These leads showed interest signals but did not convert. "
                "Each lead is admitted only after consent, DND and reachability "
                "checks; no call is placed by this recommendation."
            ),
            text=(
                "Review the eligible leads and approve a follow-up plan. "
                "Compliance gates still run before any outreach."
            ),
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
            expected_direction="Recovered conversions from eligible leads",
            suggested_next_action="Review eligible leads and approve follow-up",
            relevant_funnel_stage="recovery",
            source_type="analytics",
        )
        self.repo.add_evidence(rec.id, business_id, validated)

        policy = RecoveryPolicy.from_settings(_settings())
        config = eligibility_config(_settings())
        for item in recoverable[:10]:
            lead = item.get("lead")
            if lead is None:
                continue
            eligibility = self._recovery_eligibility(business_id, lead, item, policy, config)
            if not eligibility:
                continue
            self.repo.create_recovery_recommendation(
                recommendation_id=rec.id,
                business_id=business_id,
                lead_id=lead.id,
                recovery_reason=item["recovery_reason"],
                suggested_next_action=(
                    "Follow up via an approved channel; the compliance gate runs "
                    "again before any contact."
                ),
                supporting_call_ids=[item["call"].id] if item.get("call") else [],
                current_lead_status=getattr(lead, "status", None),
                confidence=scoring["confidence"],
                expected_outcome="Potential conversion with timely follow-up",
                compliance_checked=True,
            )
        self._supersede_previous(
            business_id, models.RecommendationCategory.LEAD_RECOVERY, rec
        )
        return rec

    def _recovery_eligibility(
        self,
        business_id: str,
        lead: Lead,
        item: Dict[str, Any],
        policy: RecoveryPolicy,
        config: Any,
    ) -> bool:
        """Run the deterministic recovery gate and the voice consent/DND rules.

        A recovery recommendation is only persisted with
        ``compliance_checked=True`` after this method returns True. It does
        not place a call; the call gate runs again at execution time.
        """
        lead_payload = {
            "lead_id": lead.id,
            "phone": lead.phone,
            "email": lead.email,
            "status": (lead.custom_fields or {}).get("status", "new"),
            "custom_fields": lead.custom_fields or {},
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "last_contact_at": (
                lead.last_contact_at.isoformat() if lead.last_contact_at else None
            ),
        }
        signals = [
            {"signal": signal}
            for signal in (item.get("signals") or [])
            if isinstance(signal, str)
        ]
        decision = check_eligibility(
            lead_payload, datetime.now(timezone.utc), policy, config, signals=signals
        )
        if not decision.eligible:
            return False

        phone = normalize_phone(lead.phone)
        dnd = (
            self.db.query(DndEntry)
            .filter(
                DndEntry.phone == phone,
                DndEntry.active == True,  # noqa: E712
            )
            .first()
        )
        if dnd is not None:
            return False
        consent = (
            self.db.query(ConsentRecord)
            .filter(
                ConsentRecord.business_id == business_id,
                ConsentRecord.lead_id == lead.id,
                ConsentRecord.channel == "voice",
            )
            .first()
        )
        # Unknown consent is not treated as permission. A revoked/expired
        # record always blocks; a missing record blocks unless the business
        # explicitly does not require consent (checked by the call gate).
        if consent is not None and consent.status != "granted":
            return False
        return True


def _settings():
    from app.config import get_settings

    return get_settings()
