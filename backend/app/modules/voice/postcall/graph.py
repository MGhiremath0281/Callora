"""The post-call AI workflow (LangGraph).

The workflow is a bounded, linear graph:

    load_context -> extract_qualification -> classify_refusal -> summarize
                 -> (build_handoff) -> persist

Every node is deterministic in shape and has a bounded amount of work: one
LLM call at most. The graph never loops, so a single job can only ever
spend a handful of model calls.

Failure behaviour:

* a *transient* model failure (timeout, malformed answer) propagates and
  the background job is retried with backoff;
* a *missing* model (no API key configured) degrades to a deterministic
  extraction from the provider payload, so a completed call still produces
  a usable outcome instead of nothing.
"""

import logging
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    Disposition,
    HandoffKind,
    HandoffStatus,
    RefusalCategory,
)
from app.modules.voice.errors import LLMUnavailableError
from app.modules.voice.models import CallOutcome, Handoff
from app.modules.voice.postcall.llm import LLMClient, NullLLMClient
from app.modules.voice.postcall.prompts import (
    HANDOFF_SYSTEM,
    HANDOFF_USER,
    QUALIFICATION_SYSTEM,
    QUALIFICATION_USER,
    REFUSAL_SYSTEM,
    REFUSAL_USER,
    SUMMARY_SYSTEM,
    SUMMARY_USER,
    format_answers,
    format_questions,
    truncate_transcript,
)

logger = logging.getLogger("callora.voice.postcall.graph")


class PostCallState(TypedDict, total=False):
    """The state carried through the graph."""

    call_id: str
    business_id: str
    lead_id: str
    lead_name: str
    lead_phone: str
    business_name: str
    business_instructions: str
    language: str
    questions: List[Dict[str, Any]]
    transcript: str
    provider_extracted: Dict[str, Any]
    provider_summary: str
    no_conversation: bool

    # outputs
    qualification_answers: Dict[str, Any]
    qualified: Optional[bool]
    qualification_score: Optional[float]
    qualification_notes: str
    missing_questions: List[str]
    is_refusal: bool
    refusal_category: Optional[str]
    refusal_reason: Optional[str]
    callback_requested: bool
    summary: str
    disposition: str
    follow_ups: List[str]
    language_detected: Optional[str]
    key_details: Dict[str, Any]
    needs_human: bool
    handoff_briefing: Optional[str]
    handoff_recommended_action: Optional[str]
    handoff_priority: Optional[str]
    engine: str
    model: str
    raw: Dict[str, Any]


# ---------------------------------------------------------------------------
# Structured answers
# ---------------------------------------------------------------------------


class QualificationAnswer(BaseModel):
    answers: Dict[str, Any] = Field(default_factory=dict)
    qualified: bool = False
    score: float = 0.0
    notes: str = ""
    missing_questions: List[str] = Field(default_factory=list)


class RefusalAnswer(BaseModel):
    is_refusal: bool = False
    category: str = RefusalCategory.OTHER
    reason: str = ""
    callback_requested: bool = False


class SummaryAnswer(BaseModel):
    summary: str = ""
    outcome: str = Disposition.UNKNOWN
    follow_ups: List[str] = Field(default_factory=list)
    language: str = ""
    key_details: Dict[str, Any] = Field(default_factory=dict)
    needs_human: bool = False


class HandoffAnswer(BaseModel):
    briefing: str = ""
    recommended_action: str = ""
    priority: str = "medium"


_VALID_REFUSAL_CATEGORIES = {
    RefusalCategory.NOT_INTERESTED,
    RefusalCategory.ALREADY_CUSTOMER,
    RefusalCategory.WRONG_NUMBER,
    RefusalCategory.TOO_EARLY,
    RefusalCategory.PRICE,
    RefusalCategory.COMPETITOR,
    RefusalCategory.NO_BUDGET,
    RefusalCategory.CALLBACK_LATER,
    RefusalCategory.DO_NOT_CONTACT,
    RefusalCategory.LANGUAGE_BARRIER,
    RefusalCategory.OTHER,
}

_VALID_OUTCOMES = {
    Disposition.QUALIFIED,
    Disposition.NOT_QUALIFIED,
    Disposition.REFUSED,
    Disposition.CALLBACK_REQUESTED,
    Disposition.NO_CONVERSATION,
    Disposition.UNKNOWN,
}


class PostCallWorkflow:
    """Builds and runs the post-call graph."""

    def __init__(self, settings: Settings, llm: Optional[LLMClient] = None) -> None:
        self._settings = settings
        self._llm = llm or NullLLMClient()

    # -- graph -------------------------------------------------------------

    def build(self, session: Session):
        """Compile the graph. ``session`` is captured by the DB nodes."""
        graph = StateGraph(PostCallState)
        graph.add_node("load_context", self._node_load_context(session))
        graph.add_node("extract_qualification", self._node_extract_qualification)
        graph.add_node("classify_refusal", self._node_classify_refusal)
        graph.add_node("summarize", self._node_summarize)
        graph.add_node("build_handoff", self._node_build_handoff)
        graph.add_node("persist", self._node_persist(session))

        graph.add_edge(START, "load_context")
        graph.add_conditional_edges(
            "load_context",
            self._route_after_context,
            {"qualify": "extract_qualification", "summarize": "summarize"},
        )
        graph.add_edge("extract_qualification", "classify_refusal")
        graph.add_edge("classify_refusal", "summarize")
        graph.add_conditional_edges(
            "summarize",
            self._route_after_summary,
            {"handoff": "build_handoff", "persist": "persist"},
        )
        graph.add_edge("build_handoff", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    def run(self, session: Session, state: PostCallState) -> PostCallState:
        """Execute the workflow for one call."""
        graph = self.build(session)
        return graph.invoke(state)

    # -- routing -----------------------------------------------------------

    @staticmethod
    def _route_after_context(state: PostCallState) -> str:
        # A call without a transcript still needs an outcome, but it must not
        # reach the model. It goes straight to the summary node, which knows
        # how to describe a call that never happened.
        if state.get("no_conversation"):
            return "summarize"
        return "qualify"

    @staticmethod
    def _route_after_summary(state: PostCallState) -> str:
        if state.get("needs_human") or state.get("callback_requested"):
            return "handoff"
        return "persist"

    # -- nodes -------------------------------------------------------------

    def _node_load_context(self, session: Session):
        def node(state: PostCallState) -> Dict[str, Any]:
            call = repo.get_call_any_business(session, state["call_id"])
            if call is None:
                raise LookupError(f"call '{state['call_id']}' no longer exists")
            business = repo.get_business(session, call.business_id)
            lead = repo.get_lead(session, call.business_id, call.lead_id)
            payload = dict(call.provider_payload or {})
            transcript = call.transcript or payload.get("transcript") or ""
            extracted = payload.get("extracted_data") or {}
            if not isinstance(extracted, dict):
                extracted = {}
            provider_summary = payload.get("summary") or ""
            if not provider_summary and isinstance(extracted, dict):
                call_summary = extracted.get("Call Summary")
                if isinstance(call_summary, dict):
                    provider_summary = str(call_summary.get("subjective") or "")
                elif isinstance(call_summary, str):
                    provider_summary = call_summary
            questions = list(business.qualification_questions or []) if business else []
            max_chars = self._settings.voice_transcript_max_chars
            return {
                "business_id": call.business_id,
                "lead_id": call.lead_id,
                "lead_name": (lead.name if lead is not None else "") or "",
                "lead_phone": (lead.phone if lead is not None else "") or "",
                "business_name": (business.name if business is not None else "") or "",
                "business_instructions": (
                    business.agent_instructions if business is not None else ""
                )
                or "",
                "language": call.language or (business.default_language if business else "en"),
                "questions": questions,
                "transcript": truncate_transcript(transcript or "", max_chars),
                "provider_extracted": extracted,
                "provider_summary": provider_summary or "",
                "no_conversation": not (transcript or "").strip(),
                "engine": "langgraph",
                "model": self._llm.model,
                "raw": {},
            }

        return node

    def _node_extract_qualification(self, state: PostCallState) -> Dict[str, Any]:
        if not self._llm.is_available:
            fallback = self._fallback_qualification(state)
            return fallback
        system = QUALIFICATION_SYSTEM
        user = QUALIFICATION_USER.format(
            business_name=state.get("business_name", ""),
            language=state.get("language", ""),
            business_instructions=state.get("business_instructions", ""),
            questions=format_questions(state.get("questions") or []),
            transcript=state.get("transcript", ""),
        )
        payload = self._ask(system, user)
        answer = QualificationAnswer.model_validate(payload)
        return {
            "qualification_answers": answer.answers,
            "qualified": answer.qualified,
            "qualification_score": max(0.0, min(1.0, float(answer.score or 0.0))),
            "qualification_notes": answer.notes,
            "missing_questions": answer.missing_questions,
            "raw": {**state.get("raw", {}), "qualification": payload},
        }

    def _node_classify_refusal(self, state: PostCallState) -> Dict[str, Any]:
        if not self._llm.is_available:
            return self._fallback_refusal(state)
        system = REFUSAL_SYSTEM
        user = REFUSAL_USER.format(
            language=state.get("language", ""),
            transcript=state.get("transcript", ""),
        )
        payload = self._ask(system, user)
        answer = RefusalAnswer.model_validate(payload)
        category = answer.category if answer.category in _VALID_REFUSAL_CATEGORIES else (
            RefusalCategory.OTHER
        )
        return {
            "is_refusal": answer.is_refusal,
            "refusal_category": category if answer.is_refusal else None,
            "refusal_reason": answer.reason if answer.is_refusal else None,
            "callback_requested": answer.callback_requested,
            "raw": {**state.get("raw", {}), "refusal": payload},
        }

    def _node_summarize(self, state: PostCallState) -> Dict[str, Any]:
        if state.get("no_conversation"):
            return {
                "summary": "No conversation took place on this call.",
                "disposition": Disposition.NO_CONVERSATION,
                "follow_ups": [],
                "language_detected": state.get("language"),
                "key_details": {},
                "needs_human": False,
                "raw": {**state.get("raw", {}), "summary": {"skipped": "no_conversation"}},
            }
        if not self._llm.is_available:
            return self._fallback_summary(state)
        system = SUMMARY_SYSTEM
        user = SUMMARY_USER.format(
            business_name=state.get("business_name", ""),
            language=state.get("language", ""),
            business_instructions=state.get("business_instructions", ""),
            transcript=state.get("transcript", ""),
        )
        payload = self._ask(system, user)
        answer = SummaryAnswer.model_validate(payload)
        outcome = answer.outcome if answer.outcome in _VALID_OUTCOMES else Disposition.UNKNOWN
        if state.get("is_refusal") and outcome not in (
            Disposition.REFUSED,
            Disposition.CALLBACK_REQUESTED,
        ):
            outcome = Disposition.REFUSED
        if state.get("callback_requested"):
            outcome = Disposition.CALLBACK_REQUESTED
        return {
            "summary": answer.summary,
            "disposition": outcome,
            "follow_ups": answer.follow_ups,
            "language_detected": (answer.language or state.get("language") or "").lower()[:8] or None,
            "key_details": answer.key_details,
            "needs_human": bool(answer.needs_human) or bool(state.get("callback_requested")),
            "raw": {**state.get("raw", {}), "summary": payload},
        }

    def _node_build_handoff(self, state: PostCallState) -> Dict[str, Any]:
        if not self._llm.is_available:
            briefing = self._fallback_briefing(state)
            return {
                "handoff_briefing": briefing,
                "handoff_recommended_action": self._fallback_action(state),
                "handoff_priority": "medium",
                "raw": {**state.get("raw", {}), "handoff": {"skipped": "no_llm"}},
            }
        system = HANDOFF_SYSTEM
        user = HANDOFF_USER.format(
            business_name=state.get("business_name", ""),
            lead_name=state.get("lead_name", ""),
            lead_phone=state.get("lead_phone", ""),
            language=state.get("language", ""),
            summary=state.get("summary", ""),
            qualification=format_answers(state.get("qualification_answers")),
            refusal=state.get("refusal_reason") or "none",
        )
        payload = self._ask(system, user)
        answer = HandoffAnswer.model_validate(payload)
        return {
            "handoff_briefing": answer.briefing,
            "handoff_recommended_action": answer.recommended_action,
            "handoff_priority": answer.priority,
            "raw": {**state.get("raw", {}), "handoff": payload},
        }

    def _node_persist(self, session: Session):
        def node(state: PostCallState) -> Dict[str, Any]:
            call = repo.get_call_any_business(session, state["call_id"])
            if call is None:
                raise LookupError(f"call '{state['call_id']}' no longer exists")
            outcome = CallOutcome(
                business_id=state.get("business_id") or call.business_id,
                call_id=call.id,
                lead_id=state.get("lead_id") or call.lead_id,
                summary=state.get("summary", ""),
                disposition=state.get("disposition", Disposition.UNKNOWN),
                qualified=state.get("qualified"),
                qualification_score=state.get("qualification_score"),
                qualification_answers=state.get("qualification_answers") or {},
                refusal_category=state.get("refusal_category"),
                refusal_reason=state.get("refusal_reason"),
                follow_up_actions=state.get("follow_ups") or [],
                handoff_required=bool(state.get("needs_human")),
                handoff_briefing=state.get("handoff_briefing"),
                language_detected=state.get("language_detected"),
                model=state.get("model"),
                engine=state.get("engine", "langgraph"),
                raw_response=state.get("raw") or {},
            )
            repo.upsert_outcome(session, outcome)
            if call.summary is None and outcome.summary:
                repo.update_call(session, call, {"summary": outcome.summary})

            if outcome.handoff_required:
                self._create_handoff(session, state, call)
            return {"persisted": True}

        return node

    # -- helpers -----------------------------------------------------------

    def _ask(self, system: str, user: str) -> Dict[str, Any]:
        """Call the LLM and return the parsed JSON object.

        Only a *missing* model degrades gracefully. A model that answers
        with something unusable raises, and the background job retries.
        """
        try:
            return self._llm.complete_json(system, user)
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise RuntimeError(f"the post-call LLM call failed: {exc}") from exc

    def _fallback_qualification(self, state: PostCallState) -> Dict[str, Any]:
        """Deterministic extraction from the provider's own extracted data."""
        extracted = state.get("provider_extracted") or {}
        answers: Dict[str, Any] = {}
        for key, value in extracted.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                answers[key] = value
        questions = state.get("questions") or []
        for item in questions:
            if isinstance(item, dict):
                key = item.get("key") or item.get("id")
                if key and key not in answers:
                    answers[key] = None
        return {
            "qualification_answers": answers,
            "qualified": None,
            "qualification_score": None,
            "qualification_notes": "extracted from the provider payload (no LLM configured)",
            "missing_questions": [],
            "engine": "provider-fallback",
            "raw": {**state.get("raw", {}), "qualification": {"source": "provider"}},
        }

    def _fallback_refusal(self, state: PostCallState) -> Dict[str, Any]:
        extracted = state.get("provider_extracted") or {}
        category = None
        reason = None
        is_refusal = False
        callback = False
        for key, value in extracted.items():
            lowered = str(key).lower()
            if lowered in ("refused", "is_refused") and value:
                is_refusal = True
            if lowered in ("refusal_category", "refusal_reason") and value:
                is_refusal = True
                if lowered == "refusal_category":
                    category = str(value)
                else:
                    reason = str(value)
            if lowered in ("callback_requested", "callback_user") and value:
                callback = True
        if category and category not in _VALID_REFUSAL_CATEGORIES:
            category = RefusalCategory.OTHER
        return {
            "is_refusal": is_refusal,
            "refusal_category": category,
            "refusal_reason": reason,
            "callback_requested": callback,
            "engine": "provider-fallback",
            "raw": {**state.get("raw", {}), "refusal": {"source": "provider"}},
        }

    def _fallback_summary(self, state: PostCallState) -> Dict[str, Any]:
        provider_summary = state.get("provider_summary") or ""
        if not provider_summary:
            provider_summary = (
                "The call was recorded but no summary could be produced "
                "(no LLM is configured for this deployment)."
            )
        return {
            "summary": provider_summary,
            "disposition": state.get("disposition", Disposition.UNKNOWN),
            "follow_ups": [],
            "language_detected": state.get("language"),
            "key_details": {},
            "needs_human": bool(state.get("callback_requested")),
            "engine": "provider-fallback",
            "raw": {**state.get("raw", {}), "summary": {"source": "provider"}},
        }

    def _fallback_briefing(self, state: PostCallState) -> str:
        parts = [
            f"{state.get('lead_name') or 'The lead'} ({state.get('lead_phone') or 'no phone'})",
            f"Business: {state.get('business_name') or 'n/a'}",
            f"Summary: {state.get('summary') or 'n/a'}",
        ]
        if state.get("refusal_reason"):
            parts.append(f"Objection: {state['refusal_reason']}")
        if state.get("qualification_answers"):
            parts.append(f"Qualification: {format_answers(state['qualification_answers'])}")
        return " | ".join(parts)

    def _fallback_action(self, state: PostCallState) -> str:
        if state.get("callback_requested"):
            return "Call the lead back at the requested time."
        if state.get("is_refusal"):
            return "Do not call again in this campaign; log the objection."
        return "Review the call summary and follow up as appropriate."

    def _create_handoff(self, session: Session, state: PostCallState, call) -> None:
        """Create (or refresh) the handoff request for a call."""
        business = repo.get_business(session, call.business_id)
        kind = HandoffKind.WARM_TRANSFER if (
            business is not None and business.warm_transfer_enabled
        ) else HandoffKind.CALLBACK
        status = (
            HandoffStatus.INITIATED
            if kind == HandoffKind.WARM_TRANSFER
            else HandoffStatus.REQUESTED
        )
        existing = session.execute(
            select(Handoff).where(Handoff.call_id == call.id).limit(1)
        ).scalars().first()
        if existing is not None:
            repo.update_handoff(
                session,
                existing,
                {
                    "status": status,
                    "briefing": state.get("handoff_briefing"),
                    "reason": state.get("refusal_reason") or state.get("summary"),
                    "context": self._handoff_context(state),
                },
            )
            return
        repo.create_handoff(
            session,
            Handoff(
                business_id=call.business_id,
                call_id=call.id,
                lead_id=call.lead_id,
                lead_name=state.get("lead_name"),
                kind=kind,
                status=status,
                reason=state.get("refusal_reason") or state.get("summary"),
                reason_code="callback_requested" if state.get("callback_requested") else None,
                requested_by="ai",
                target_number=(
                    business.warm_transfer_number
                    if business is not None and kind == HandoffKind.WARM_TRANSFER
                    else None
                ),
                briefing=state.get("handoff_briefing"),
                context=self._handoff_context(state),
            ),
        )

    @staticmethod
    def _handoff_context(state: PostCallState) -> Dict[str, Any]:
        return {
            "summary": state.get("summary", ""),
            "qualification_answers": state.get("qualification_answers") or {},
            "refusal_category": state.get("refusal_category"),
            "refusal_reason": state.get("refusal_reason"),
            "follow_ups": state.get("follow_ups") or [],
            "language": state.get("language_detected") or state.get("language"),
            "recommended_action": state.get("handoff_recommended_action"),
            "priority": state.get("handoff_priority"),
        }
