"""Rule-based lead qualification for the voice agent.

This is a *deterministic* engine: it evaluates the answers the customer
actually gave during the conversation against the business's configured
qualification questions. It never calls a model, never invents an answer
and never upgrades an unknown to a guess — an unanswered required question
makes the result ``insufficient_data`` with the unknown keys listed.

Where the rules come from
-------------------------

The business-specific configuration lives on the voice business record
(``Business.qualification_questions``), which the business already
maintains for the conversation script. Each entry may carry an optional
``rules`` object:

    {"key": "budget", "question": "...", "type": "number",
     "required": true,
     "rules": {"min_value": 2500000, "matches_any": ["3 bhk", "villa"]}}

Supported rule keys (all optional):

* ``min_value`` / ``max_value`` — numeric bounds for numeric answers;
* ``matches_any`` — case-insensitive substring match for text answers;
* ``allowed_values`` — exact match (case-insensitive) for choice answers.

Everything else (the score weighting) is deterministic: a required
question counts twice as much as an optional one, and the score is the
weighted share of *validly answered* questions.
"""

import logging
import numbers
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("callora.voice.tools.qualification")

STATUS_QUALIFIED = "qualified"
STATUS_NOT_QUALIFIED = "not_qualified"
STATUS_INSUFFICIENT = "insufficient_data"

ENGINE = "rules"


@dataclass
class QualificationResult:
    """The structured result the tool returns to the voice agent."""

    status: str
    qualified: Optional[bool]
    score: float
    reasons: List[str] = field(default_factory=list)
    answers: Dict[str, Any] = field(default_factory=dict)
    unknown_keys: List[str] = field(default_factory=list)
    engine: str = ENGINE
    evaluated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "qualified": self.qualified,
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "answers": self.answers,
            "unknown_keys": self.unknown_keys,
            "engine": self.engine,
            "evaluated_at": (self.evaluated_at or datetime.now(timezone.utc)).isoformat(),
        }


def _is_unknown(value: Any) -> bool:
    """Whether the value carries no real answer.

    ``None``, an empty string and the literal strings a model uses for "no
    answer" are unknown — anything else (including a number or a sentence)
    is an answer the customer gave.
    """
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return True
        return text in {"unknown", "not provided", "not_provided", "na", "n/a", "none"}
    return False


def _parse_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Number):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _check_rules(key: str, value: Any, rules: Dict[str, Any]) -> List[str]:
    """Evaluate the optional per-question rules; returns failure reasons."""
    reasons: List[str] = []
    if not isinstance(rules, dict):
        return reasons
    min_value = rules.get("min_value")
    max_value = rules.get("max_value")
    if min_value is not None or max_value is not None:
        number = _parse_number(value)
        if number is None:
            reasons.append(f"answer '{key}' is not a usable number")
        else:
            if min_value is not None and number < float(min_value):
                reasons.append(
                    f"answer '{key}' ({value}) is below the required minimum {min_value}"
                )
            if max_value is not None and number > float(max_value):
                reasons.append(
                    f"answer '{key}' ({value}) is above the accepted maximum {max_value}"
                )
    matches_any = rules.get("matches_any")
    if matches_any:
        text = str(value).strip().lower()
        if not any(str(option).lower() in text for option in matches_any):
            reasons.append(
                f"answer '{key}' does not match any accepted option of {matches_any}"
            )
    allowed = rules.get("allowed_values")
    if allowed:
        text = str(value).strip().lower()
        if text not in [str(option).lower() for option in allowed]:
            reasons.append(
                f"answer '{key}' is not one of the accepted values {allowed}"
            )
    return reasons


def evaluate_qualification(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
    min_score: float = 0.0,
) -> QualificationResult:
    """Evaluate the given answers against the business's question config.

    ``answers`` must contain only what the customer actually said. Keys
    that were never answered are preserved as unknown; they are listed in
    ``unknown_keys`` and never given a fabricated value.
    """
    now = datetime.now(timezone.utc)
    reasons: List[str] = []
    unknown_keys: List[str] = []
    clean_answers: Dict[str, Any] = {}
    total_weight = 0.0
    earned_weight = 0.0
    rule_failures = 0
    required_unknown = False

    for question in questions or []:
        if not isinstance(question, dict):
            continue
        key = str(question.get("key") or "").strip()
        if not key:
            continue
        required = bool(question.get("required"))
        rules = question.get("rules") or {}
        weight = 2.0 if required else 1.0
        total_weight += weight

        value = answers.get(key)
        if _is_unknown(value):
            unknown_keys.append(key)
            clean_answers[key] = None
            if required:
                required_unknown = True
                reasons.append(f"required answer '{key}' was not provided")
            continue

        clean_answers[key] = value
        failures = _check_rules(key, value, rules if isinstance(rules, dict) else {})
        if failures:
            rule_failures += 1
            reasons.extend(failures)
            continue
        earned_weight += weight

    if total_weight == 0:
        # A business without configured questions has no qualification
        # rules: Callora refuses to invent a verdict.
        return QualificationResult(
            status=STATUS_INSUFFICIENT,
            qualified=None,
            score=0.0,
            reasons=[
                "the business has no qualification questions configured, "
                "so no rule-based qualification is possible"
            ],
            answers=clean_answers,
            unknown_keys=unknown_keys,
            evaluated_at=now,
        )

    score = earned_weight / total_weight

    if required_unknown:
        status = STATUS_INSUFFICIENT
        qualified = None
        if not any("was not provided" in r for r in reasons):
            reasons.append("at least one required answer was not provided")
    elif rule_failures:
        status = STATUS_NOT_QUALIFIED
        qualified = False
        reasons.append(f"{rule_failures} answer(s) failed the configured rules")
    elif score < min_score:
        status = STATUS_NOT_QUALIFIED
        qualified = False
        reasons.append(
            f"qualification score {score:.2f} is below the required {min_score:.2f}"
        )
    else:
        status = STATUS_QUALIFIED
        qualified = True
        reasons.append("all required answers were provided and passed the configured rules")

    return QualificationResult(
        status=status,
        qualified=qualified,
        score=score,
        reasons=reasons,
        answers=clean_answers,
        unknown_keys=unknown_keys,
        evaluated_at=now,
    )


__all__ = [
    "ENGINE",
    "QualificationResult",
    "STATUS_INSUFFICIENT",
    "STATUS_NOT_QUALIFIED",
    "STATUS_QUALIFIED",
    "evaluate_qualification",
]
