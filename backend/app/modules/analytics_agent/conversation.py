"""Conversation Analytics for Callora Outbound Voice Agent.

Extracts customer intent, objections, sentiment, and grouped refusal reasons
from stored call records, post-call outcomes, memory, and transcripts.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead
from app.modules.voice.models import Call, CallOutcome, Handoff, VoiceCallMemory
from app.modules.analytics_agent.schemas import (
    CallInsightItem,
    ConversationAnalyticsResponse,
    ObjectionItem,
    RefusalReasonItem,
    SentimentBreakdown,
)


POSITIVE_LEXICON = frozenset([
    "interested", "sounds good", "send details", "schedule", "great", "yes",
    "visit", "looking to buy", "callback", "perfect", "good", "happy", "sure",
    "tell me more", "share proposal", "whatsapp me", "proceed"
])

NEGATIVE_LEXICON = frozenset([
    "not interested", "don't call", "stop calling", "remove my number", "dnd",
    "expensive", "too high", "waste", "cheaper", "never", "hate", "scam",
    "no", "bad", "refuse", "cancel", "unhappy", "terrible", "irritated"
])

OBJECTION_PATTERNS = {
    "Price / Budget Constraint": [
        r"\b(expensive|too high|costly|budget|afford|discount|cheaper|can't pay|price is high)\b",
        r"\b(price|pricing|cost)\b",
    ],
    "Timing / Busy Currently": [
        r"\b(not now|busy|call later|next month|next year|bad time|driving|meeting)\b",
        r"\b(timing|later)\b",
    ],
    "Competitor Preference": [
        r"\b(already using|competitor|other tool|alternative|another company|signed with)\b",
    ],
    "Authority / Decision Maker Missing": [
        r"\b(talk to (my )?(boss|manager|partner|husband|wife|father)|need approval)\b",
    ],
    "Location / Feature Mismatch": [
        r"\b(location|far|not in (my )?area|missing feature|different city)\b",
    ],
    "No Current Need / Low Priority": [
        r"\b(no need|no requirement|not looking|already bought|not planning)\b",
    ],
}


class ConversationAnalyzer:
    """Analyzes customer conversation records."""

    @classmethod
    def extract_intent(
        cls,
        call: Call,
        outcome: Optional[CallOutcome] = None,
        memory: Optional[VoiceCallMemory] = None,
    ) -> str:
        """Derive the primary customer intent during the call."""
        if memory and memory.customer_intent:
            return memory.customer_intent

        if outcome:
            if outcome.refusal_category in ("do_not_contact", "opt_out"):
                return "opt_out"
            if outcome.refusal_category:
                return "raise_objection"
            if outcome.qualified:
                return "qualification_ready"
            if outcome.handoff_required:
                return "request_human_transfer"
            if outcome.disposition == "callback_requested":
                return "request_callback"

        text = f"{call.summary or ''} {call.transcript or ''}".lower()
        if any(w in text for w in ["dnd", "stop calling", "remove"]):
            return "opt_out"
        if any(w in text for w in ["price", "cost", "how much", "rate"]):
            return "inquire_pricing"
        if any(w in text for w in ["callback", "call me back", "later"]):
            return "request_callback"
        if any(w in text for w in ["human", "agent", "transfer", "manager"]):
            return "request_human_transfer"
        if any(w in text for w in ["flat", "property", "bhk", "feature", "inventory"]):
            return "inquire_business_knowledge"

        return "general_inquiry"

    @classmethod
    def extract_objections(
        cls,
        call: Call,
        outcome: Optional[CallOutcome] = None,
    ) -> List[str]:
        """Detect objections raised during the conversation."""
        found: List[str] = []
        text = f"{call.summary or ''} {call.transcript or ''} {outcome.refusal_reason if outcome else ''}".lower()

        if outcome and outcome.refusal_category:
            cat = outcome.refusal_category.lower()
            if cat in ("price", "pricing", "budget"):
                found.append("Price / Budget Constraint")
            elif cat in ("timing", "callback_later", "busy"):
                found.append("Timing / Busy Currently")
            elif cat in ("competitor", "alternative"):
                found.append("Competitor Preference")
            elif cat in ("authority", "approval"):
                found.append("Authority / Decision Maker Missing")
            elif cat in ("not_interested", "no_need"):
                found.append("No Current Need / Low Priority")

        for label, patterns in OBJECTION_PATTERNS.items():
            if label not in found:
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        found.append(label)
                        break

        return found

    @classmethod
    def analyze_sentiment(
        cls,
        call: Call,
        outcome: Optional[CallOutcome] = None,
    ) -> Tuple[str, float]:
        """Classify sentiment into (positive, neutral, negative) and a normalized score."""
        # Baseline score
        score = 0.50

        if outcome:
            if outcome.qualified:
                score += 0.35
            elif outcome.disposition == "qualified":
                score += 0.30
            elif outcome.disposition == "callback_requested":
                score += 0.15
            elif outcome.refusal_category in ("do_not_contact", "opt_out"):
                score -= 0.40
            elif outcome.refusal_category:
                score -= 0.25

            if outcome.qualification_score is not None:
                # normalize qualification score (0-100 to -0.2 to +0.2)
                qs = float(outcome.qualification_score)
                score += (qs - 50.0) / 250.0

        text = f"{call.summary or ''} {call.transcript or ''}".lower()
        pos_hits = sum(1 for w in POSITIVE_LEXICON if w in text)
        neg_hits = sum(1 for w in NEGATIVE_LEXICON if w in text)

        score += (pos_hits * 0.08) - (neg_hits * 0.12)
        score = max(0.0, min(1.0, round(score, 2)))

        if score >= 0.62:
            label = "positive"
        elif score <= 0.38:
            label = "negative"
        else:
            label = "neutral"

        return label, score

    @classmethod
    def group_refusal_reasons(
        cls,
        outcomes: List[CallOutcome],
    ) -> List[RefusalReasonItem]:
        """Aggregate and group refusal reasons from call outcomes."""
        buckets: Dict[str, Dict[str, Any]] = {
            "Pricing & Budget": {"count": 0, "samples": []},
            "Timing & Postponement": {"count": 0, "samples": []},
            "Competitor / Alternate Solution": {"count": 0, "samples": []},
            "Not Interested / No Need": {"count": 0, "samples": []},
            "Contact Preference / DND": {"count": 0, "samples": []},
            "Eligibility & Technical Fit": {"count": 0, "samples": []},
            "Other / General Refusal": {"count": 0, "samples": []},
        }

        total_refusals = 0
        for out in outcomes:
            cat = (out.refusal_category or "").lower()
            reason = (out.refusal_reason or "").strip()
            if not cat and not reason and out.disposition != "refused":
                continue

            total_refusals += 1
            sample = reason or cat or "Customer declined without specific reason"
            target_text = f"{cat} {reason}".lower()

            if any(k in target_text for k in ["price", "pricing", "cost", "budget", "expensive", "afford", "cheap"]):
                target = "Pricing & Budget"
            elif any(k in target_text for k in ["time", "timing", "later", "busy", "postpone", "driving", "meeting"]):
                target = "Timing & Postponement"
            elif any(k in target_text for k in ["competitor", "alternative", "other company"]):
                target = "Competitor / Alternate Solution"
            elif any(k in target_text for k in ["not_interested", "no_need", "disinterest", "not looking"]):
                target = "Not Interested / No Need"
            elif any(k in target_text for k in ["dnd", "do_not_contact", "opt_out", "stop calling", "remove"]):
                target = "Contact Preference / DND"
            elif any(k in target_text for k in ["eligibility", "unsupported", "criteria", "fit"]):
                target = "Eligibility & Technical Fit"
            else:
                target = "Other / General Refusal"

            buckets[target]["count"] += 1
            if len(buckets[target]["samples"]) < 3 and sample not in buckets[target]["samples"]:
                buckets[target]["samples"].append(sample)

        results: List[RefusalReasonItem] = []
        for cat_name, data in buckets.items():
            cnt = data["count"]
            if cnt > 0:
                pct = round((cnt / total_refusals) * 100.0, 1) if total_refusals > 0 else 0.0
                results.append(
                    RefusalReasonItem(
                        refusal_category=cat_name,
                        count=cnt,
                        percentage=pct,
                        sample_reasons=data["samples"],
                    )
                )

        results.sort(key=lambda r: r.count, reverse=True)
        return results

    @classmethod
    def get_conversation_analytics(
        cls,
        session: Session,
        business_id: str,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
        sentiment_filter: Optional[str] = None,
    ) -> ConversationAnalyticsResponse:
        """Compute full conversation analytics for one business."""
        # Query calls
        calls_query = select(Call).where(Call.business_id == business_id)
        if status_filter:
            calls_query = calls_query.where(Call.status == status_filter)
        calls_query = calls_query.order_by(Call.created_at.desc())

        all_calls = session.execute(calls_query).scalars().all()
        total_calls_count = len(all_calls)

        # Map call_id -> CallOutcome
        call_ids = [c.id for c in all_calls]
        outcomes_by_call_id: Dict[str, CallOutcome] = {}
        if call_ids:
            outcomes = session.execute(
                select(CallOutcome).where(CallOutcome.call_id.in_(call_ids))
            ).scalars().all()
            outcomes_by_call_id = {o.call_id: o for o in outcomes}

        # Map call_id -> VoiceCallMemory
        memories_by_call_id: Dict[str, VoiceCallMemory] = {}
        if call_ids:
            memories = session.execute(
                select(VoiceCallMemory).where(VoiceCallMemory.call_id.in_(call_ids))
            ).scalars().all()
            memories_by_call_id = {m.call_id: m for m in memories}

        # Map call_id -> Handoff
        handoffs_by_call_id: Dict[str, Handoff] = {}
        if call_ids:
            handoffs = session.execute(
                select(Handoff).where(Handoff.call_id.in_(call_ids))
            ).scalars().all()
            handoffs_by_call_id = {h.call_id: h for h in handoffs}

        # Map lead_id -> Lead Name
        lead_ids = list({c.lead_id for c in all_calls if c.lead_id})
        lead_names: Dict[str, str] = {}
        if lead_ids:
            leads = session.execute(
                select(Lead.id, Lead.name).where(Lead.id.in_(lead_ids))
            ).all()
            lead_names = {lid: name for lid, name in leads}

        sent_counts = {"positive": 0, "neutral": 0, "negative": 0}
        intent_counts: Dict[str, int] = {}
        objection_counts: Dict[str, int] = {}
        insights: List[CallInsightItem] = []

        for call in all_calls:
            outcome = outcomes_by_call_id.get(call.id)
            memory = memories_by_call_id.get(call.id)
            handoff = handoffs_by_call_id.get(call.id)

            intent = cls.extract_intent(call, outcome, memory)
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

            sentiment_label, sentiment_score = cls.analyze_sentiment(call, outcome)
            sent_counts[sentiment_label] += 1

            call_objections = cls.extract_objections(call, outcome)
            for obj in call_objections:
                objection_counts[obj] = objection_counts.get(obj, 0) + 1

            if sentiment_filter and sentiment_label != sentiment_filter:
                continue

            follow_ups: List[str] = []
            if outcome and outcome.follow_up_actions:
                for act in outcome.follow_up_actions:
                    if isinstance(act, dict):
                        follow_ups.append(act.get("action") or act.get("type") or str(act))
                    else:
                        follow_ups.append(str(act))

            insights.append(
                CallInsightItem(
                    call_id=call.id,
                    lead_id=call.lead_id,
                    lead_name=lead_names.get(call.lead_id) or "Unknown Lead",
                    duration_seconds=call.duration_seconds or 0,
                    cost=float(call.cost or 0.0),
                    status=call.status,
                    disposition=outcome.disposition if outcome else call.status,
                    qualified=outcome.qualified if outcome else None,
                    customer_intent=intent,
                    sentiment=sentiment_label,
                    sentiment_score=sentiment_score,
                    objections=call_objections,
                    refusal_category=outcome.refusal_category if outcome else None,
                    refusal_reason=outcome.refusal_reason if outcome else None,
                    follow_up_actions=follow_ups,
                    handoff_status=handoff.status if handoff else None,
                    created_at=call.created_at.isoformat() if call.created_at else None,
                )
            )

        total_analyzed = len(all_calls)
        pos_pct = round((sent_counts["positive"] / total_analyzed) * 100.0, 1) if total_analyzed > 0 else 0.0
        neu_pct = round((sent_counts["neutral"] / total_analyzed) * 100.0, 1) if total_analyzed > 0 else 0.0
        neg_pct = round((sent_counts["negative"] / total_analyzed) * 100.0, 1) if total_analyzed > 0 else 0.0

        sentiment_breakdown = SentimentBreakdown(
            positive=sent_counts["positive"],
            neutral=sent_counts["neutral"],
            negative=sent_counts["negative"],
            positive_pct=pos_pct,
            neutral_pct=neu_pct,
            negative_pct=neg_pct,
        )

        objection_items: List[ObjectionItem] = []
        for obj_name, count in objection_counts.items():
            freq = round((count / total_analyzed) * 100.0, 1) if total_analyzed > 0 else 0.0
            objection_items.append(
                ObjectionItem(objection=obj_name, count=count, frequency_pct=freq)
            )
        objection_items.sort(key=lambda x: x.count, reverse=True)

        grouped_refusals = cls.group_refusal_reasons(list(outcomes_by_call_id.values()))

        # Pagination for insights
        paginated_insights = insights[offset : offset + limit]

        return ConversationAnalyticsResponse(
            business_id=business_id,
            total_conversations=total_analyzed,
            sentiment_breakdown=sentiment_breakdown,
            intent_distribution=intent_counts,
            common_objections=objection_items,
            grouped_refusal_reasons=grouped_refusals,
            call_insights=paginated_insights,
            total=len(insights),
            limit=limit,
            offset=offset,
        )
