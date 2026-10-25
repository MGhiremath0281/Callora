"""Tests for the live voice follow-up question API and AI guardrails.

Covers:
- POST /api/v1/voice/answer-question endpoint validation and business scoping
- Grounded answer generation and source references
- AI guardrails: ambiguity clarification, missing info fallback, opt-out enforcement
- Human handoff and callback integration
- Conversation memory tracking and qualification continuation
- Cross-business data leakage prevention
- GET /api/v1/voice/bolna/tool-definition
"""

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    CallStatus,
    FollowUpAction,
    HandoffKind,
    HandoffStatus,
    KnowledgeCategory,
)
from app.models import Lead
from app.modules.voice.models import BusinessKnowledgeItem, Call
from tests.conftest import (
    BIZ_1,
    BIZ_2,
    KEY_1,
    KEY_2,
    VOICE_LEAD_ID,
    VOICE_PHONE,
    auth_headers,
)

ANSWER_URL = "/api/v1/voice/answer-question"


@pytest.fixture()
def setup_call(db, voice_business, voice_lead):
    """Create an active call and seed knowledge for BIZ_1."""
    # Seed knowledge
    k1 = BusinessKnowledgeItem(
        business_id=BIZ_1,
        category=KnowledgeCategory.PRICING,
        title="Professional Plan",
        content="The Professional plan costs ₹12,000 per month for up to 20 users with automated CRM sync.",
        tags=["pricing", "professional"],
        source_reference="Pricing Guide 2026",
        is_active=True,
        priority=10,
    )
    k2 = BusinessKnowledgeItem(
        business_id=BIZ_1,
        category=KnowledgeCategory.POLICY,
        title="Refund Policy",
        content="Callora offers a 14-day full money-back guarantee with zero cancellation fees.",
        tags=["refund", "policy"],
        source_reference="Refund Policy v2.0",
        is_active=True,
        priority=8,
    )
    db.add_all([k1, k2])

    # Configure business with qualification questions and warm transfer
    voice_business.qualification_questions = [
        {"key": "team_size", "question": "How many sales agents are on your team?"},
        {"key": "budget", "question": "What is your target monthly software budget?"},
    ]
    voice_business.warm_transfer_enabled = True
    voice_business.warm_transfer_number = "+919876543210"

    # Create active call
    call = Call(
        id="call_test_001",
        business_id=BIZ_1,
        lead_id=voice_lead.id,
        to_number=VOICE_PHONE,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    db.commit()

    return {"call": call, "business": voice_business, "lead": voice_lead}


@pytest.fixture()
def biz2_call(db):
    """Create an active call for BIZ_2."""
    biz2 = repo.get_or_create_business(db, BIZ_2)
    lead2 = Lead(
        id="lead_biz2_001",
        business_id=BIZ_2,
        name="Lead Two",
        phone="+919111111111",
        source="test",
    )
    db.add(lead2)
    call2 = Call(
        id="call_biz2_001",
        business_id=BIZ_2,
        lead_id=lead2.id,
        to_number="+919111111111",
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call2)
    db.commit()
    return {"call": call2, "business": biz2, "lead": lead2}


class TestFollowUpQuestionAPI:
    def test_answer_grounded_question(self, voice_client, setup_call):
        call = setup_call["call"]
        payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "How much is the Professional plan?",
            "language": "en",
        }
        res = voice_client.post(ANSWER_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        body = res.json()
        assert body["action"] == FollowUpAction.ANSWER
        assert "12,000" in body["answer"]
        assert "Pricing Guide 2026" in body["source_references"]
        assert body["should_handoff"] is False
        assert body["confidence"] > 0.5
        # Natural qualification continuation attached
        assert "team" in body["answer"] or body["qualification_continuation"] is not None

    def test_ambiguous_question_triggers_clarification(self, voice_client, setup_call):
        call = setup_call["call"]
        payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "Can you do better on the deal?",
            "language": "en",
        }
        res = voice_client.post(ANSWER_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        body = res.json()
        assert body["action"] == FollowUpAction.CLARIFY
        assert "clarify" in body["answer"].lower() or "specify" in body["answer"].lower()
        assert body["should_handoff"] is False

    def test_missing_knowledge_triggers_safe_fallback_and_escalation(
        self, voice_client, setup_call
    ):
        call = setup_call["call"]
        payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "Do you support on-premise mainframe installation with quantum cryptography?",
            "language": "en",
        }
        res = voice_client.post(ANSWER_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        body = res.json()
        assert body["action"] == FollowUpAction.ESCALATE
        assert body["should_handoff"] is True
        assert body["handoff_recommended"] is True
        assert "don't have that specific information" in body["answer"].lower()
        assert body["confidence"] == 0.0

    def test_customer_opt_out_immediately_enforced(self, voice_client, setup_call, db):
        call = setup_call["call"]
        payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "Stop calling me and remove my number from your list immediately.",
            "language": "en",
        }
        res = voice_client.post(ANSWER_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        body = res.json()
        assert body["action"] == FollowUpAction.OPT_OUT
        assert "removed your number" in body["answer"].lower()
        assert body["should_handoff"] is False

        # Verify PostgreSQL database state updated
        lead_profile = repo.get_lead_profile(db, BIZ_1, call.lead_id)
        assert lead_profile is not None
        assert lead_profile.do_not_call is True

        dnd = repo.find_dnd_entry(db, BIZ_1, call.to_number)
        assert dnd is not None
        assert dnd.active is True

    def test_explicit_human_transfer_request(self, voice_client, setup_call):
        call = setup_call["call"]
        payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "I want to speak with a human agent right now.",
            "language": "en",
        }
        res = voice_client.post(ANSWER_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        body = res.json()
        assert body["action"] == FollowUpAction.ESCALATE
        assert body["should_handoff"] is True
        assert body["handoff_type"] == HandoffKind.WARM_TRANSFER
        assert "transfer" in body["answer"].lower() or "connect" in body["answer"].lower()

    def test_conversation_memory_continuity(self, voice_client, setup_call, db):
        call = setup_call["call"]

        # Turn 1: customer asks pricing and supplies team size
        turn1_payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "What is the price of your Professional plan?",
            "context": {"qualification_fields": {"team_size": "15"}},
        }
        res1 = voice_client.post(ANSWER_URL, json=turn1_payload, headers=auth_headers(BIZ_1))
        assert res1.status_code == 200
        state1 = res1.json()["memory_state"]
        assert state1["turns_count"] == 1
        assert state1["qualification_fields"]["team_size"] == "15"
        assert "team_size" not in state1["pending_qualification_keys"]
        assert "budget" in state1["pending_qualification_keys"]

        # Turn 2: customer asks follow-up policy question
        turn2_payload = {
            "business_id": BIZ_1,
            "call_id": call.id,
            "question": "What is your refund guarantee?",
        }
        res2 = voice_client.post(ANSWER_URL, json=turn2_payload, headers=auth_headers(BIZ_1))
        assert res2.status_code == 200
        state2 = res2.json()["memory_state"]
        assert state2["turns_count"] == 2
        assert state2["questions_answered_count"] == 2
        # Team size qualification answer preserved from Turn 1
        assert state2["qualification_fields"]["team_size"] == "15"

    def test_cross_business_data_leakage_prevented(self, voice_client, setup_call, biz2_call):
        call_biz1 = setup_call["call"]
        call_biz2 = biz2_call["call"]

        # BIZ_1 key attempting to query BIZ_2 call is blocked with 403
        payload = {
            "business_id": BIZ_2,
            "call_id": call_biz2.id,
            "question": "What are your prices?",
        }
        res_mismatch = voice_client.post(
            ANSWER_URL, json=payload, headers=auth_headers(BIZ_1)
        )
        assert res_mismatch.status_code == 403

        # BIZ_1 key passing BIZ_1 business_id with BIZ_2 call_id is blocked
        payload_sneak = {
            "business_id": BIZ_1,
            "call_id": call_biz2.id,
            "question": "What are your prices?",
        }
        res_sneak = voice_client.post(
            ANSWER_URL, json=payload_sneak, headers=auth_headers(BIZ_1)
        )
        assert res_sneak.status_code in (403, 404)

    def test_bolna_tool_definition_endpoint(self, voice_client):
        res = voice_client.get("/api/v1/voice/bolna/tool-definition")
        assert res.status_code == 200
        data = res.json()["tool"]
        assert data["type"] == "function"
        assert data["function"]["name"] == "answer_customer_question"
        assert "api_config" in data
        assert data["api_config"]["method"] == "POST"
        assert "/api/v1/voice/answer-question" in data["api_config"]["url"]
