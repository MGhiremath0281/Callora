"""Deterministic conversation analysis over persisted transcripts.

The analyzer is a rules engine (``engine="rules"``, ``model=None``): it
reads the transcript of a call the business already owns and extracts
objections, buying signals and leakage flags with keyword rules. It
never calls an LLM and never invents a transcript it did not read.

A transcript hash is stored with the analysis, so re-running the
analyzer on an unchanged transcript is a no-op and a changed transcript
produces a new row instead of silently overwriting history.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import InvalidParameterError, LeadNotFoundError
from app.modules.recovery_workflow.models import ConversationAnalysis
from app.modules.voice import repository as voice_repo

OBJECTION_RULES: Dict[str, List[str]] = {
    "price": ["price", "cost", "expensive", "budget", "afford", "rate", "too much"],
    "timing": ["later", "not now", "busy", "next month", "next year", "call back later"],
    "competitor": ["another builder", "other option", "already booked", "competitor"],
    "trust": ["trust", "scam", "fake", "legitimate", "verify", "documents"],
    "need": ["not interested", "don't need", "no need", "already have"],
    "authority": ["ask my husband", "ask my wife", "my father", "my boss", "decision maker"],
}

BUYING_SIGNAL_RULES: Dict[str, List[str]] = {
    "price_enquiry": ["what is the price", "how much does", "payment plan", "emi"],
    "visit_intent": ["site visit", "show me", "when can i visit", "visit the"],
    "timeline": ["when do you start", "possession", "handover", "possession date"],
    "financing": ["loan", "bank", "finance", "down payment"],
    "documentation": ["documents", "agreement", "booking amount", "token"],
}

#: A call where the agent quoted price before any qualification question
#: was answered, or never proposed a next step, is a leakage candidate.
LEAKAGE_RULES: Dict[str, Dict[str, Any]] = {
    "price_before_qualification": {
        "description": "price discussed before any qualification answer",
    },
    "no_next_step": {
        "description": "call ended without a proposed next step",
    },
    "objection_unhandled": {
        "description": "objection raised with no acknowledgement in the reply",
    },
}

NEXT_STEP_MARKERS = [
    "call back",
    "callback",
    "follow up",
    "follow-up",
    "let me send",
    "i will send",
    "schedule",
    "book",
    "site visit",
    "meeting",
    "whatsapp",
    "email you",
]

QUALIFICATION_MARKERS = [
    "budget",
    "when are you",
    "timeline",
    "requirement",
    "looking for",
    "size",
    "bedroom",
    "bhk",
    "purpose",
]

PRICE_MARKERS = ["price", "cost", "rate", "lakh", "crore", "₹", "rs.", "inr"]

ACKNOWLEDGEMENT_MARKERS = [
    "understand",
    "i see",
    "fair point",
    "good question",
    "let me explain",
    "absolutely",
    "sure",
]


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _contains(text: str, markers: List[str]) -> bool:
    return any(marker in text for marker in markers)


def transcript_hash(transcript: str) -> str:
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()


def analyse_transcript(
    session: Session,
    *,
    business_id: str,
    call_id: str,
    now: Optional[datetime] = None,
) -> ConversationAnalysis:
    """Analyse one owned call's transcript with deterministic rules."""
    call = voice_repo.get_call(session, business_id, call_id)
    if call is None:
        # Cross-tenant or unknown calls are never analysed.
        from app.modules.voice.errors import CallNotFoundError

        raise CallNotFoundError(call_id)

    transcript = (call.transcript or "").strip()
    if not transcript:
        raise InvalidParameterError(
            "the call has no transcript to analyse; nothing was fabricated"
        )

    existing = session.execute(
        select(ConversationAnalysis).where(
            ConversationAnalysis.call_id == call_id,
            ConversationAnalysis.business_id == business_id,
        )
    ).scalars().first()
    current_hash = transcript_hash(transcript)
    if existing is not None and existing.transcript_hash == current_hash:
        return existing

    lowered = transcript.lower()
    objections = sorted(
        name
        for name, markers in OBJECTION_RULES.items()
        if _contains(lowered, markers)
    )
    buying_signals = sorted(
        name
        for name, markers in BUYING_SIGNAL_RULES.items()
        if _contains(lowered, markers)
    )

    qualification_asked = _contains(lowered, QUALIFICATION_MARKERS)
    price_discussed = _contains(lowered, PRICE_MARKERS)
    next_step = _contains(lowered, NEXT_STEP_MARKERS)

    leakage_flags: List[str] = []
    if price_discussed and not qualification_asked:
        leakage_flags.append("price_before_qualification")
    if not next_step:
        leakage_flags.append("no_next_step")
    if objections and not _contains(lowered, ACKNOWLEDGEMENT_MARKERS):
        leakage_flags.append("objection_unhandled")

    sentiment = "mixed"
    positive = sum(lowered.count(word) for word in ("interested", "great", "good", "yes"))
    negative = sum(lowered.count(word) for word in ("no", "not", "later", "expensive"))
    if positive > negative * 2 and positive > 0:
        sentiment = "positive"
    elif negative > positive * 2 and negative > 0:
        sentiment = "negative"

    intent = "neutral"
    if buying_signals:
        intent = "buying"
    elif objections and not buying_signals:
        intent = "resisting"

    now = now or datetime.now(timezone.utc)
    analysis = ConversationAnalysis(
        id=_prefixed_id("ca"),
        business_id=business_id,
        call_id=call_id,
        lead_id=call.lead_id,
        transcript_hash=current_hash,
        analysis={
            "qualification_asked": qualification_asked,
            "price_discussed": price_discussed,
            "next_step_proposed": next_step,
            "rule_engine": "callora-v1",
        },
        objections=objections,
        buying_signals=buying_signals,
        leakage_flags=leakage_flags,
        sentiment=sentiment,
        intent=intent,
        engine="rules",
        model=None,
        analyzed_at=now,
    )
    if existing is not None:
        # The transcript changed: replace the stale analysis in place so
        # the unique (call_id) constraint holds and history stays honest.
        existing.transcript_hash = current_hash
        existing.analysis = analysis.analysis
        existing.objections = analysis.objections
        existing.buying_signals = analysis.buying_signals
        existing.leakage_flags = analysis.leakage_flags
        existing.sentiment = analysis.sentiment
        existing.intent = analysis.intent
        existing.analyzed_at = now
        session.flush()
        return existing

    session.add(analysis)
    session.flush()
    return analysis


def analyse_business_calls(
    session: Session,
    business_id: str,
    *,
    limit: int = 100,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Analyse the most recent transcript-bearing calls of the business."""
    calls = list(
        session.execute(
            select(voice_repo.Call)
            .where(
                voice_repo.Call.business_id == business_id,
                voice_repo.Call.transcript.isnot(None),
            )
            .order_by(voice_repo.Call.created_at.desc())
            .limit(limit)
        ).scalars().all()
    )
    analysed = skipped = 0
    for call in calls:
        if not (call.transcript or "").strip():
            skipped += 1
            continue
        analyse_transcript(session, business_id=business_id, call_id=call.id, now=now)
        analysed += 1
    return {"analysed": analysed, "skipped_no_transcript": skipped}
