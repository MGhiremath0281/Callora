"""Analytics aggregation for recommendation generation."""

from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, and_

from app.modules.voice import models as voice_models
from app.modules.voice import repository as voice_repo
from app.modules.voice.models import Call, CallOutcome
from app.models import Lead


class AnalyticsAggregator:
    """Aggregates real data from voice calls and analytics."""

    def __init__(self, db: Session):
        self.db = db

    def get_business_analytics(self, business_id: str, days: int = 30) -> Dict[str, Any]:
        """Get comprehensive analytics for a business."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Call statistics
        total_calls = self.db.query(func.count(Call.id)).filter(
            Call.business_id == business_id,
            Call.created_at >= cutoff
        ).scalar() or 0

        analyzed_calls = self.db.query(func.count(Call.id)).filter(
            Call.business_id == business_id,
            Call.created_at >= cutoff,
            Call.status.in_(["completed", "answered"]),
            Call.transcript.isnot(None)
        ).scalar() or 0

        completed_calls = self.db.query(func.count(Call.id)).filter(
            Call.business_id == business_id,
            Call.created_at >= cutoff,
            Call.status == "completed"
        ).scalar() or 0

        connected_calls = self.db.query(func.count(Call.id)).filter(
            Call.business_id == business_id,
            Call.created_at >= cutoff,
            Call.connected_at.isnot(None)
        ).scalar() or 0

        # Outcomes
        outcomes = self.db.query(CallOutcome).filter(
            CallOutcome.business_id == business_id,
            CallOutcome.created_at >= cutoff
        ).all()

        qualified_count = sum(1 for o in outcomes if o.qualified)
        refusal_count = sum(1 for o in outcomes if o.refusal_category)
        handoff_count = sum(1 for o in outcomes if o.handoff_required)

        # Refusal reasons
        refusal_reasons = {}
        for o in outcomes:
            if o.refusal_category:
                refusal_reasons[o.refusal_category] = refusal_reasons.get(o.refusal_category, 0) + 1

        # Dispositions
        dispositions = {}
        for o in outcomes:
            disp = o.disposition or "unknown"
            dispositions[disp] = dispositions.get(disp, 0) + 1

        # Get calls with transcripts for pattern analysis
        calls_with_transcripts = self.db.query(Call).filter(
            Call.business_id == business_id,
            Call.created_at >= cutoff,
            Call.transcript.isnot(None),
            Call.transcript != ""
        ).all()

        return {
            "business_id": business_id,
            "period_days": days,
            "total_calls": total_calls,
            "analyzed_calls": analyzed_calls,
            "completed_calls": completed_calls,
            "connected_calls": connected_calls,
            "qualified_count": qualified_count,
            "refusal_count": refusal_count,
            "handoff_count": handoff_count,
            "qualification_rate": (qualified_count / analyzed_calls * 100) if analyzed_calls > 0 else 0,
            "refusal_rate": (refusal_count / analyzed_calls * 100) if analyzed_calls > 0 else 0,
            "refusal_reasons": refusal_reasons,
            "dispositions": dispositions,
            "calls_with_transcripts": calls_with_transcripts,
            "outcomes": outcomes,
        }

    def detect_objection_patterns(self, calls: List[Call]) -> Dict[str, int]:
        """Detect objection patterns from transcripts."""
        objection_keywords = {
            "price": ["price", "cost", "expensive", "budget", "afford", "rate"],
            "timing": ["later", "not now", "busy", "time", "next month", "next year"],
            "competitor": ["competitor", "another", "already", "other option"],
            "trust": ["trust", "scam", "fake", "legitimate", "verify"],
            "need": ["not interested", "don't need", "no need", "already have"],
        }

        patterns = {k: 0 for k in objection_keywords}
        for call in calls:
            transcript = (call.transcript or "").lower()
            for category, keywords in objection_keywords.items():
                if any(kw in transcript for kw in keywords):
                    patterns[category] += 1
        return patterns

    def find_recoverable_leads(self, business_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Find leads with recovery potential based on signals."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Look for calls with callback requests, future intent, etc.
        outcomes = self.db.query(CallOutcome).filter(
            CallOutcome.business_id == business_id,
            CallOutcome.created_at >= cutoff,
            CallOutcome.handoff_required == False,
        ).all()

        recoverable = []
        for outcome in outcomes:
            # Check for recovery signals
            signals = []
            summary = (outcome.summary or "").lower()
            if "callback" in summary or "call back" in summary:
                signals.append("callback_requested")
            if "later" in summary or "future" in summary or "next" in summary:
                signals.append("future_intent")
            if outcome.refusal_category and outcome.refusal_category not in ["not_interested_permanent"]:
                signals.append("addressable_objection")
            if outcome.qualification_answers and not outcome.qualified:
                signals.append("incomplete_qualification")

            if signals:
                call = self.db.query(Call).filter(Call.id == outcome.call_id).first()
                lead = self.db.query(Lead).filter(Lead.id == outcome.lead_id).first() if outcome.lead_id else None
                recoverable.append({
                    "outcome": outcome,
                    "call": call,
                    "lead": lead,
                    "signals": signals,
                    "recovery_reason": "; ".join(signals)
                })
        return recoverable
