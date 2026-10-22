"""Automated evaluation runner for the voice follow-up question answering engine.

Computes benchmark scores across:
- Retrieval relevance
- Factual accuracy & groundedness
- Unsupported claims prevention (anti-hallucination)
- Clarification behavior
- Escalation decisions
- Immediate opt-out handling
- Conversation continuity across multiple turns
"""

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import FollowUpAction
from app.modules.voice.knowledge.service import FollowUpService
from app.modules.voice.schemas import (
    FollowUpQuestionRequest,
    FollowUpQuestionResponse,
)
from tests.evaluation.dataset import (
    EVALUATION_DATASET,
    EvaluationQuestion,
)

logger = logging.getLogger("callora.voice.evaluation")


@dataclass
class ItemEvaluationResult:
    question_id: str
    business_id: str
    category: str
    question: str
    action_matched: bool
    retrieval_passed: bool
    factual_accuracy_passed: bool
    unsupported_claims_passed: bool
    clarification_passed: bool
    escalation_passed: bool
    opt_out_passed: bool
    continuity_passed: bool
    answer: str
    response: FollowUpQuestionResponse
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.action_matched
            and self.retrieval_passed
            and self.factual_accuracy_passed
            and self.unsupported_claims_passed
            and self.clarification_passed
            and self.escalation_passed
            and self.opt_out_passed
            and self.continuity_passed
        )


@dataclass
class EvaluationReport:
    total_questions: int = 0
    passed_questions: int = 0
    retrieval_relevance_rate: float = 0.0
    factual_accuracy_rate: float = 0.0
    unsupported_claims_rate: float = 0.0
    clarification_accuracy: float = 0.0
    escalation_accuracy: float = 0.0
    opt_out_accuracy: float = 0.0
    continuity_rate: float = 0.0
    overall_pass_rate: float = 0.0
    results: List[ItemEvaluationResult] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# Automated Evaluation Report: AI Voice Sales Follow-Up Agent",
            "",
            "## Summary Metrics",
            "",
            f"- **Total Questions Evaluated**: {self.total_questions}",
            f"- **Overall Pass Rate**: {self.overall_pass_rate:.1f}% ({self.passed_questions}/{self.total_questions})",
            f"- **Retrieval Relevance Rate**: {self.retrieval_relevance_rate:.1f}%",
            f"- **Factual Accuracy & Groundedness**: {self.factual_accuracy_rate:.1f}%",
            f"- **Unsupported Claims / Hallucination Rate**: {self.unsupported_claims_rate:.1f}% (target: 0.0%)",
            f"- **Clarification Accuracy**: {self.clarification_accuracy:.1f}%",
            f"- **Escalation Accuracy**: {self.escalation_accuracy:.1f}%",
            f"- **Opt-Out Compliance**: {self.opt_out_accuracy:.1f}%",
            f"- **Conversation Continuity**: {self.continuity_rate:.1f}%",
            "",
            "## Category Breakdown",
            "",
            "| Question ID | Category | Action Matched | Retrieval | Factually Accurate | Zero Unsupported Claims | Result |",
            "|---|---|---|---|---|---|---|",
        ]
        for res in self.results:
            status_str = "PASS" if res.passed else "FAIL"
            lines.append(
                f"| `{res.question_id}` | {res.category} | {'Yes' if res.action_matched else 'No'} | "
                f"{'Yes' if res.retrieval_passed else 'No'} | {'Yes' if res.factual_accuracy_passed else 'No'} | "
                f"{'Yes' if res.unsupported_claims_passed else 'No'} | **{status_str}** |"
            )

        return "\n".join(lines)


class QuestionEvaluator:
    """Runs the evaluation dataset against the FollowUpService."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        call_id_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._service = FollowUpService(settings=settings, session=session)
        self._call_id_map = call_id_map or {}

    def evaluate(
        self, dataset: Optional[List[EvaluationQuestion]] = None
    ) -> EvaluationReport:
        items = dataset or EVALUATION_DATASET
        results: List[ItemEvaluationResult] = []

        multi_turn_call_id = self._call_id_map.get("multi_turn")

        for item in items:
            call_id = (
                multi_turn_call_id
                if item.is_multi_turn and multi_turn_call_id
                else self._call_id_map.get(item.business_id, "call_eval_001")
            )

            req = FollowUpQuestionRequest(
                business_id=item.business_id,
                call_id=call_id,
                question=item.question,
                language="en",
                context=item.context or {},
            )

            response = self._service.answer_question(req)
            result = self._evaluate_item(item, response, call_id)
            results.append(result)

        # Aggregate metrics
        total = len(results)
        passed = sum(1 for r in results if r.passed)

        retrieval_rate = (
            sum(1 for r in results if r.retrieval_passed) / total * 100.0 if total else 0.0
        )
        accuracy_rate = (
            sum(1 for r in results if r.factual_accuracy_passed) / total * 100.0
            if total
            else 0.0
        )
        # Unsupported claims rate is % of answers that invented unauthorized claims
        hallucination_rate = (
            sum(1 for r in results if not r.unsupported_claims_passed) / total * 100.0
            if total
            else 0.0
        )

        clarify_items = [r for r in results if r.category == "ambiguous"]
        clarify_acc = (
            sum(1 for r in clarify_items if r.clarification_passed)
            / len(clarify_items)
            * 100.0
            if clarify_items
            else 100.0
        )

        escalate_items = [r for r in results if r.category in ("escalation", "unsupported", "cross_business")]
        escalate_acc = (
            sum(1 for r in escalate_items if r.escalation_passed)
            / len(escalate_items)
            * 100.0
            if escalate_items
            else 100.0
        )

        optout_items = [r for r in results if r.category == "opt_out"]
        optout_acc = (
            sum(1 for r in optout_items if r.opt_out_passed)
            / len(optout_items)
            * 100.0
            if optout_items
            else 100.0
        )

        continuity_items = [r for r in results if r.category == "multi_turn"]
        continuity_acc = (
            sum(1 for r in continuity_items if r.continuity_passed)
            / len(continuity_items)
            * 100.0
            if continuity_items
            else 100.0
        )

        return EvaluationReport(
            total_questions=total,
            passed_questions=passed,
            retrieval_relevance_rate=retrieval_rate,
            factual_accuracy_rate=accuracy_rate,
            unsupported_claims_rate=hallucination_rate,
            clarification_accuracy=clarify_acc,
            escalation_accuracy=escalate_acc,
            opt_out_accuracy=optout_acc,
            continuity_rate=continuity_acc,
            overall_pass_rate=(passed / total * 100.0) if total else 0.0,
            results=results,
        )

    def _evaluate_item(
        self,
        item: EvaluationQuestion,
        response: FollowUpQuestionResponse,
        call_id: str,
    ) -> ItemEvaluationResult:
        notes: List[str] = []

        # 1. Action check
        action_matched = response.action == item.expected_action
        if not action_matched:
            notes.append(f"Action mismatch: expected {item.expected_action}, got {response.action}")

        # 2. Retrieval check
        if item.expected_sources:
            retrieval_passed = any(
                exp in response.source_references for exp in item.expected_sources
            )
            if not retrieval_passed:
                notes.append(
                    f"Retrieval mismatch: expected one of {item.expected_sources}, got {response.source_references}"
                )
        else:
            retrieval_passed = True

        # 3. Factual accuracy check
        answer_lower = response.answer.lower()
        if item.expected_key_facts:
            facts_found = sum(
                1 for fact in item.expected_key_facts if fact.lower() in answer_lower
            )
            # Require at least one expected key fact to be explicitly present
            factual_accuracy_passed = facts_found > 0
            if not factual_accuracy_passed:
                notes.append(
                    f"Factual key facts missing: expected any of {item.expected_key_facts} in answer"
                )
        else:
            factual_accuracy_passed = True

        # 4. Anti-hallucination / forbidden claims check
        unsupported_claims_passed = True
        for forbidden in item.forbidden_claims:
            if forbidden.lower() in answer_lower:
                unsupported_claims_passed = False
                notes.append(f"Forbidden claim detected in answer: '{forbidden}'")
                break

        # 5. Clarification behavior check
        if item.expected_action == FollowUpAction.CLARIFY:
            clarification_passed = (
                response.action == FollowUpAction.CLARIFY and not response.should_handoff
            )
        else:
            clarification_passed = True

        # 6. Escalation behavior check
        if item.expected_action == FollowUpAction.ESCALATE:
            escalation_passed = response.should_handoff is True
            if item.expected_handoff_type:
                if response.handoff_type != item.expected_handoff_type:
                    escalation_passed = False
                    notes.append(
                        f"Expected handoff type '{item.expected_handoff_type}', got '{response.handoff_type}'"
                    )
        else:
            escalation_passed = True

        # 7. Opt-out check
        if item.expected_action == FollowUpAction.OPT_OUT:
            memory = repo.get_call_memory(self._session, call_id)
            opt_out_passed = (
                response.action == FollowUpAction.OPT_OUT
                and memory is not None
                and bool(memory.opt_out_detected)
            )
        else:
            opt_out_passed = True

        # 8. Continuity check
        if item.is_multi_turn:
            memory = repo.get_call_memory(self._session, call_id)
            continuity_passed = (
                memory is not None
                and memory.turns_count >= item.turn_number
                and len(memory.questions_answered or []) >= item.turn_number
            )
            if not continuity_passed:
                notes.append(
                    f"Turn {item.turn_number} continuity failed: turns_count={getattr(memory, 'turns_count', None)}"
                )
        else:
            continuity_passed = True

        return ItemEvaluationResult(
            question_id=item.id,
            business_id=item.business_id,
            category=item.category,
            question=item.question,
            action_matched=action_matched,
            retrieval_passed=retrieval_passed,
            factual_accuracy_passed=factual_accuracy_passed,
            unsupported_claims_passed=unsupported_claims_passed,
            clarification_passed=clarification_passed,
            escalation_passed=escalation_passed,
            opt_out_passed=opt_out_passed,
            continuity_passed=continuity_passed,
            answer=response.answer,
            response=response,
            notes=notes,
        )
