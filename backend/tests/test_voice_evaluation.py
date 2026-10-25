"""Automated evaluation test suite covering 45 realistic customer questions.

Evaluates:
- Retrieval relevance
- Factual accuracy & groundedness
- Unsupported claims prevention (zero hallucination)
- Clarification behavior on ambiguous queries
- Escalation decisions for complex/missing inquiries
- Opt-out compliance
- Multi-turn conversation continuity
"""

import pytest

from app.models import Lead
from app.modules.voice import repository as repo
from app.modules.voice.constants import CallStatus
from app.modules.voice.models import BusinessKnowledgeItem, Call, prefixed_id
from tests.conftest import BIZ_1, BIZ_2, VOICE_PHONE
from tests.evaluation.dataset import (
    EVALUATION_DATASET,
    get_business_seed_knowledge,
)
from tests.evaluation.evaluator import QuestionEvaluator


@pytest.fixture()
def eval_environment(db, voice_business, settings):
    """Seed both businesses, active calls, and full knowledge base items."""
    # Seed knowledge for BIZ_1
    for k in get_business_seed_knowledge(BIZ_1):
        item = BusinessKnowledgeItem(
            id=prefixed_id("kb"),
            business_id=BIZ_1,
            category=k["category"],
            title=k["title"],
            content=k["content"],
            tags=k["tags"],
            source_reference=k["source_reference"],
            priority=k.get("priority", 0),
            is_active=True,
        )
        db.add(item)

    # Configure BIZ_1
    voice_business.qualification_questions = [
        {"key": "team_size", "question": "How many members are in your sales team?"},
        {"key": "budget", "question": "What is your target monthly budget?"},
    ]
    voice_business.warm_transfer_enabled = True
    voice_business.warm_transfer_number = "+919876543210"

    # Seed BIZ_2
    biz2 = repo.get_or_create_business(db, BIZ_2)
    biz2.name = "Acme Solar Installations"
    biz2.warm_transfer_enabled = False  # Acme uses callbacks only

    for k in get_business_seed_knowledge(BIZ_2):
        item = BusinessKnowledgeItem(
            id=prefixed_id("kb"),
            business_id=BIZ_2,
            category=k["category"],
            title=k["title"],
            content=k["content"],
            tags=k["tags"],
            source_reference=k["source_reference"],
            priority=k.get("priority", 0),
            is_active=True,
        )
        db.add(item)

    # Seed calls
    lead1 = Lead(
        id="lead_eval_001",
        business_id=BIZ_1,
        name="Eval Lead One",
        phone=VOICE_PHONE,
        source="test",
    )
    lead2 = Lead(
        id="lead_eval_002",
        business_id=BIZ_2,
        name="Eval Lead Two",
        phone="+919888888888",
        source="test",
    )
    db.add_all([lead1, lead2])

    call_biz1 = Call(
        id="call_eval_biz1",
        business_id=BIZ_1,
        lead_id=lead1.id,
        to_number=lead1.phone,
        status=CallStatus.IN_PROGRESS,
    )
    call_biz2 = Call(
        id="call_eval_biz2",
        business_id=BIZ_2,
        lead_id=lead2.id,
        to_number=lead2.phone,
        status=CallStatus.IN_PROGRESS,
    )
    call_multi = Call(
        id="call_eval_multi",
        business_id=BIZ_1,
        lead_id=lead1.id,
        to_number=lead1.phone,
        status=CallStatus.IN_PROGRESS,
    )
    db.add_all([call_biz1, call_biz2, call_multi])
    db.commit()

    return {
        "call_map": {
            BIZ_1: call_biz1.id,
            BIZ_2: call_biz2.id,
            "multi_turn": call_multi.id,
        }
    }


def test_automated_evaluation_benchmark(eval_environment, db, settings):
    """Run all 45 evaluation questions and assert strict quality thresholds."""
    evaluator = QuestionEvaluator(
        settings=settings,
        session=db,
        call_id_map=eval_environment["call_map"],
    )

    report = evaluator.evaluate(EVALUATION_DATASET)

    # Print markdown summary report to stdout for inspection
    print("\n" + report.to_markdown())

    for r in report.results:
        if not r.passed:
            safe_ans = r.answer.encode("ascii", "replace").decode("ascii")
            print(f"FAILED {r.question_id} ({r.category}): {r.notes}")
            print(f"   Answer: {safe_ans}")
            print(f"   Sources: {r.response.source_references}")

    # Assert evaluation criteria
    assert report.total_questions >= 30, "Benchmark must cover at least 30 questions"
    assert (
        report.retrieval_relevance_rate >= 90.0
    ), f"Retrieval relevance {report.retrieval_relevance_rate}% fell below 90%"
    assert (
        report.factual_accuracy_rate >= 90.0
    ), f"Factual accuracy {report.factual_accuracy_rate}% fell below 90%"
    assert (
        report.unsupported_claims_rate == 0.0
    ), f"Unsupported claims detected: {report.unsupported_claims_rate}%"
    assert (
        report.clarification_accuracy == 100.0
    ), f"Clarification accuracy was {report.clarification_accuracy}%"
    assert (
        report.escalation_accuracy == 100.0
    ), f"Escalation accuracy was {report.escalation_accuracy}%"
    assert (
        report.opt_out_accuracy == 100.0
    ), f"Opt-out accuracy was {report.opt_out_accuracy}%"
    assert (
        report.continuity_rate == 100.0
    ), f"Conversation continuity was {report.continuity_rate}%"
    assert (
        report.overall_pass_rate >= 90.0
    ), f"Overall pass rate {report.overall_pass_rate}% fell below 90%"
