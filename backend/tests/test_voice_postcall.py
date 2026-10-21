"""Tests for the post-call AI workflow (LangGraph).

The workflow turns a transcript into structured intelligence: the
qualification answers, the refusal category, a summary, the follow-up
actions and — when a human has to take over — a briefing. These tests
also pin down the degradation path: without a model the workflow must
still produce an outcome from the provider payload instead of failing.
"""

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    CallStatus,
    Disposition,
    HandoffKind,
    HandoffStatus,
    RefusalCategory,
)
from app.modules.voice.models import Call, CallOutcome, Handoff
from app.modules.voice.postcall.graph import PostCallWorkflow
from app.modules.voice.postcall.llm import NullLLMClient
from tests.conftest import VOICE_BIZ, VOICE_LEAD_ID, VOICE_PHONE
from tests.fakes import FailingLLM, ScriptedLLM

TRANSCRIPT = (
    "Agent: Hello, this is Test Realty. May I speak to Rahul?\n"
    "Rahul: Yes. I am looking for a 2BHK in Whitefield.\n"
    "Agent: What is your budget?\n"
    "Rahul: Around 50 lakhs. I can visit this weekend.\n"
)

#: Markers that identify the four prompts of the graph.
M_QUALIFICATION = "extract structured facts"
M_REFUSAL = "classify why a conversation ended"
M_SUMMARY = "writes short, factual call notes"
M_HANDOFF = "briefs a human sales representative"


@pytest.fixture()
def call(db, voice_business, voice_lead):
    """A finished call with a transcript."""
    row = Call(
        business_id=VOICE_BIZ,
        lead_id=VOICE_LEAD_ID,
        provider="bolna",
        provider_call_id="exec_1",
        status=CallStatus.COMPLETED,
        to_number=VOICE_PHONE,
        language="hi",
        transcript=TRANSCRIPT,
        provider_payload={
            "transcript": TRANSCRIPT,
            "extracted_data": {"budget": "5000000", "timeline": "this weekend"},
        },
    )
    db.add(row)
    db.commit()
    return row


def _script(**overrides):
    """A scripted model with sensible defaults."""
    script = {
        M_QUALIFICATION: {
            "answers": {"budget": "5000000", "timeline": "this weekend"},
            "qualified": True,
            "score": 0.8,
            "notes": "The lead has a budget and a timeline.",
            "missing_questions": [],
        },
        M_REFUSAL: {
            "is_refusal": False,
            "category": RefusalCategory.OTHER,
            "reason": "",
            "callback_requested": False,
        },
        M_SUMMARY: {
            "summary": "The lead is looking for a 2BHK in Whitefield.",
            "outcome": Disposition.QUALIFIED,
            "follow_ups": ["Send the brochure"],
            "language": "hi",
            "key_details": {"budget": "5000000"},
            "needs_human": False,
        },
        M_HANDOFF: {
            "briefing": "Rahul wants a 2BHK in Whitefield, budget 50 lakhs.",
            "recommended_action": "Call back on Monday",
            "priority": "high",
        },
    }
    script.update(overrides)
    return ScriptedLLM(script)


def _run(db, settings, llm, call):
    workflow = PostCallWorkflow(settings, llm)
    return workflow.run(db, {"call_id": call.id})


# ---------------------------------------------------------------------------
# The full workflow
# ---------------------------------------------------------------------------


class TestWorkflow:
    def test_a_qualified_call_produces_an_outcome(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        state = _run(db, voice_settings, _script(), call)
        assert state["disposition"] == Disposition.QUALIFIED
        assert state["qualified"] is True
        assert state["qualification_score"] == 0.8
        assert state["qualification_answers"]["budget"] == "5000000"
        assert state["summary"]
        assert state["follow_ups"] == ["Send the brochure"]

    def test_the_outcome_is_persisted(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        _run(db, voice_settings, _script(), call)
        outcome = repo.get_outcome(db, call.id)
        assert outcome is not None
        assert outcome.disposition == Disposition.QUALIFIED
        assert outcome.qualified is True
        assert float(outcome.qualification_score) == 0.8
        assert outcome.qualification_answers["timeline"] == "this weekend"
        assert outcome.engine == "langgraph"
        assert outcome.model == "scripted-model"

    def test_the_summary_is_written_back_to_the_call(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        _run(db, voice_settings, _script(), call)
        db.refresh(call)
        assert call.summary == "The lead is looking for a 2BHK in Whitefield."

    def test_running_twice_updates_the_same_outcome(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        _run(db, voice_settings, _script(), call)
        first_id = repo.get_outcome(db, call.id).id
        _run(db, voice_settings, _script(), call)
        assert db.query(CallOutcome).count() == 1
        assert repo.get_outcome(db, call.id).id == first_id

    def test_the_workflow_uses_the_business_questions(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script()
        _run(db, voice_settings, llm, call)
        qualification_prompt = next(
            call for call in llm.calls if M_QUALIFICATION in call["system"]
        )
        assert "budget" in qualification_prompt["user"]
        assert "When are you buying?" in qualification_prompt["user"]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_a_refusal_is_classified(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script(
            **{
                M_REFUSAL: {
                    "is_refusal": True,
                    "category": RefusalCategory.PRICE,
                    "reason": "The price is too high for me.",
                    "callback_requested": False,
                },
                M_SUMMARY: {
                    "summary": "The lead did not want to talk about the price.",
                    "outcome": Disposition.QUALIFIED,
                    "follow_ups": [],
                    "language": "hi",
                    "key_details": {},
                    "needs_human": False,
                },
            }
        )
        state = _run(db, voice_settings, llm, call)
        assert state["is_refusal"] is True
        assert state["refusal_category"] == RefusalCategory.PRICE
        # A refusal always wins over the summary's own outcome.
        assert state["disposition"] == Disposition.REFUSED

    def test_the_refusal_is_persisted(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script(
            **{
                M_REFUSAL: {
                    "is_refusal": True,
                    "category": RefusalCategory.DO_NOT_CONTACT,
                    "reason": "Please never call again.",
                    "callback_requested": False,
                }
            }
        )
        _run(db, voice_settings, llm, call)
        outcome = repo.get_outcome(db, call.id)
        assert outcome.refusal_category == RefusalCategory.DO_NOT_CONTACT
        assert "never call again" in outcome.refusal_reason

    def test_an_unknown_refusal_category_falls_back_to_other(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script(
            **{
                M_REFUSAL: {
                    "is_refusal": True,
                    "category": "because-i-said-so",
                    "reason": "no reason",
                    "callback_requested": False,
                }
            }
        )
        state = _run(db, voice_settings, llm, call)
        assert state["refusal_category"] == RefusalCategory.OTHER

    def test_a_callback_request_creates_a_handoff(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script(
            **{
                M_REFUSAL: {
                    "is_refusal": True,
                    "category": RefusalCategory.CALLBACK_LATER,
                    "reason": "Call me after the festival.",
                    "callback_requested": True,
                }
            }
        )
        state = _run(db, voice_settings, llm, call)
        assert state["disposition"] == Disposition.CALLBACK_REQUESTED
        handoff = db.query(Handoff).one()
        assert handoff.kind == HandoffKind.CALLBACK
        assert handoff.status == HandoffStatus.REQUESTED
        assert handoff.lead_id == VOICE_LEAD_ID
        assert handoff.briefing

    def test_a_human_is_asked_to_take_over(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        llm = _script(
            **{
                M_SUMMARY: {
                    "summary": "The lead wants a site visit next week.",
                    "outcome": Disposition.QUALIFIED,
                    "follow_ups": [],
                    "language": "hi",
                    "key_details": {},
                    "needs_human": True,
                }
            }
        )
        state = _run(db, voice_settings, llm, call)
        assert state["needs_human"] is True
        handoff = db.query(Handoff).one()
        assert handoff.briefing == (
            "Rahul wants a 2BHK in Whitefield, budget 50 lakhs."
        )
        assert handoff.context["recommended_action"] == "Call back on Monday"


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_without_a_model_the_provider_payload_is_used(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        """No LLM configured: the workflow still produces an outcome."""
        state = _run(db, voice_settings, NullLLMClient(), call)
        assert state["engine"] == "provider-fallback"
        assert state["qualification_answers"]["budget"] == "5000000"
        outcome = repo.get_outcome(db, call.id)
        assert outcome is not None
        assert outcome.engine == "provider-fallback"

    def test_without_a_model_the_provider_summary_is_used(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        call.provider_payload = {
            "transcript": TRANSCRIPT,
            "summary": "The provider summarised the call.",
        }
        db.commit()
        state = _run(db, voice_settings, NullLLMClient(), call)
        assert state["summary"] == "The provider summarised the call."

    def test_a_call_without_a_transcript_has_no_conversation(
        self, voice_settings, db, voice_business, voice_lead
    ):
        silent = Call(
            business_id=VOICE_BIZ,
            lead_id=VOICE_LEAD_ID,
            provider="bolna",
            status=CallStatus.NO_ANSWER,
            to_number=VOICE_PHONE,
        )
        db.add(silent)
        db.commit()
        llm = _script()
        state = _run(db, voice_settings, llm, call=silent)
        assert state["disposition"] == Disposition.NO_CONVERSATION
        assert llm.calls == []
        assert repo.get_outcome(db, silent.id).disposition == Disposition.NO_CONVERSATION

    def test_a_broken_model_raises_so_the_job_can_be_retried(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        with pytest.raises(RuntimeError):
            _run(db, voice_settings, FailingLLM(), call)

    def test_a_broken_model_leaves_no_partial_outcome(
        self, voice_settings, db, voice_business, voice_lead, call
    ):
        with pytest.raises(RuntimeError):
            _run(db, voice_settings, FailingLLM(), call)
        assert repo.get_outcome(db, call.id) is None
