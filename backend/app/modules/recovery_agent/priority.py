"""Module 5 — Priority Scoring: deterministic ranking of eligible cases.

Consumes the recovery cases that Modules 3/4 already admitted (a case
row only exists for a lead that passed the eligibility gate) and ranks
the *open* (``recommended``) ones by expected business value of acting
now. Eligibility is never re-evaluated here and a high priority score
can never resurrect an excluded lead — there is simply no case row to
score.

Every number is deterministic and computed from stored data only:

    priority = 100 × Σ(weight_f × ratio_f) / Σ(weight_f)      (0..100)

    business value   (30)  deal_value vs. a configurable reference
    urgency          (25)  verified SLA / stall breach magnitude
    buying intent    (20)  recorded prior contact + configured
                           intent flags — never assumed from nothing
    recovery pot.    (15)  severity of the verified recovery signals
    contactability   (10)  usable phone / email / language fields

Assumptions behind the default weights (all configurable via
``CALLORA_PRIORITY_*``): deal size is the strongest single reason to
call one lead before another; how long the SLA has been breached drives
time-sensitivity; recorded engagement (prior contact, explicit intent
flags) predicts conversion; stronger detected signals mean a more
actionable opportunity; a reachable lead is worth more than an
unreachable one. Missing data always yields zero points for that factor
— it is never silently treated as positive evidence — and the score
breakdown records exactly that.

No LLM is involved: scoring is pure math over stored fields so equal
inputs always produce equal scores (``wworkflow.md`` documents the
formula, weights and thresholds).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from app.config import Settings, get_settings
from app.modules.recovery_agent.detect import RecoveryPolicy, _truthy, as_utc

# Priority levels, most urgent first.
LEVEL_CRITICAL = "critical"
LEVEL_HIGH = "high"
LEVEL_MEDIUM = "medium"
LEVEL_LOW = "low"
PRIORITY_LEVELS = (LEVEL_CRITICAL, LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW)

# Factor names (stable, machine-readable).
FACTOR_BUSINESS_VALUE = "business_value"
FACTOR_URGENCY = "urgency"
FACTOR_INTENT = "buying_intent"
FACTOR_RECOVERY_POTENTIAL = "recovery_potential"
FACTOR_CONTACTABILITY = "contactability"

FACTOR_ORDER = (
    FACTOR_BUSINESS_VALUE,
    FACTOR_URGENCY,
    FACTOR_INTENT,
    FACTOR_RECOVERY_POTENTIAL,
    FACTOR_CONTACTABILITY,
)

# Same severity mapping the recoverability score (assess.py) uses, so
# both engines read a signal's severity identically. Unknown severities
# degrade to the LOW ratio — never upward.
_SEVERITY_RATIO = {"high": 1.0, "medium": 0.7, "low": 0.4}
_DEFAULT_SEVERITY_RATIO = 0.4

_MISSING = "missing"


@dataclass(frozen=True)
class PriorityConfig:
    """Weights, references and thresholds for the priority formula."""

    weight_business_value: float = 30.0
    weight_urgency: float = 25.0
    weight_intent: float = 20.0
    weight_recovery_potential: float = 15.0
    weight_contactability: float = 10.0
    deal_value_reference: float = 10_000_000.0
    urgency_full_breach_minutes: int = 10_080
    urgency_full_stall_days: int = 30
    intent_flags: FrozenSet[str] = frozenset()
    level_critical_min: float = 80.0
    level_high_min: float = 60.0
    level_medium_min: float = 40.0

    @property
    def total_weight(self) -> float:
        return max(
            self.weight_business_value
            + self.weight_urgency
            + self.weight_intent
            + self.weight_recovery_potential
            + self.weight_contactability,
            1e-9,  # guard: a all-zero config must not divide by zero
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "PriorityConfig":
        return cls(
            weight_business_value=settings.priority_weight_business_value,
            weight_urgency=settings.priority_weight_urgency,
            weight_intent=settings.priority_weight_intent,
            weight_recovery_potential=settings.priority_weight_recovery_potential,
            weight_contactability=settings.priority_weight_contactability,
            deal_value_reference=settings.priority_deal_value_reference,
            urgency_full_breach_minutes=settings.priority_urgency_full_breach_minutes,
            urgency_full_stall_days=settings.priority_urgency_full_stall_days,
            intent_flags=frozenset(settings.priority_intent_flag_set),
            level_critical_min=settings.priority_level_critical_min,
            level_high_min=settings.priority_level_high_min,
            level_medium_min=settings.priority_level_medium_min,
        )


def priority_config(settings: Optional[Settings] = None) -> PriorityConfig:
    """Build the priority config from application settings.

    Falls back to :func:`get_settings` (and, failing that, the dataclass
    defaults) so the scorer can be used from tests and scripts without a
    live settings object — same pattern as ``policy.eligibility_config``.
    """
    if settings is None:
        try:
            settings = get_settings()
        except Exception:  # noqa: BLE001 - configuration must never crash scoring
            settings = None
    if settings is None:
        return PriorityConfig()
    return PriorityConfig.from_settings(settings)


@dataclass(frozen=True)
class FactorResult:
    """One scoring factor's contribution and supporting evidence."""

    factor: str
    points: float
    max_points: float
    evidence: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "factor": self.factor,
            "points": self.points,
            "max_points": self.max_points,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class PriorityScore:
    """The complete, explainable priority result for one case."""

    score: float
    level: str
    factors: Tuple[FactorResult, ...]
    explanation: str

    def as_breakdown(self, config: PriorityConfig) -> Dict[str, Any]:
        """JSON-serializable breakdown persisted on the case / returned by
        the API: each factor's points, its maximum and the evidence used."""
        return {
            "factors": [f.as_dict() for f in self.factors],
            "explanation": self.explanation,
            "weights": {
                FACTOR_BUSINESS_VALUE: config.weight_business_value,
                FACTOR_URGENCY: config.weight_urgency,
                FACTOR_INTENT: config.weight_intent,
                FACTOR_RECOVERY_POTENTIAL: config.weight_recovery_potential,
                FACTOR_CONTACTABILITY: config.weight_contactability,
            },
        }


def priority_level_for(score: float, config: PriorityConfig) -> str:
    """Map a normalized 0..100 score to a configured priority level."""
    if score >= config.level_critical_min:
        return LEVEL_CRITICAL
    if score >= config.level_high_min:
        return LEVEL_HIGH
    if score >= config.level_medium_min:
        return LEVEL_MEDIUM
    return LEVEL_LOW


# ---------------------------------------------------------------------------
# Factor computations — each returns (ratio 0..1, evidence string).
# Missing/unparseable data always yields ratio 0 with an explicit note.
# ---------------------------------------------------------------------------


def _business_value_ratio(
    lead: Dict[str, Any], config: PriorityConfig
) -> Tuple[float, str]:
    raw = lead.get("deal_value")
    if raw in (None, ""):
        return 0.0, "deal_value missing — no points awarded"
    try:
        value = abs(float(raw))
    except (TypeError, ValueError):
        return 0.0, f"deal_value {raw!r} not numeric — no points awarded"
    ratio = min(value / config.deal_value_reference, 1.0)
    return ratio, (
        f"deal_value {value:.0f} = {ratio * 100:.0f}% of reference "
        f"{config.deal_value_reference:.0f}"
    )


def _urgency_ratio(
    lead: Dict[str, Any],
    signals: List[Dict[str, str]],
    policy: RecoveryPolicy,
    config: PriorityConfig,
    now: datetime,
) -> Tuple[float, str]:
    """Verified time-beyond-SLA magnitude, capped at full points.

    Only the signals actually detected on the case count; a signal whose
    underlying timestamp is unknown contributes nothing (unknown time is
    never assumed to be overdue).
    """
    names = {s.get("signal") for s in signals}
    candidates: List[Tuple[float, str]] = []

    if "never_contacted" in names:
        created = as_utc(lead.get("created_at"))
        if created is not None:
            breach_min = (now - created).total_seconds() / 60 - policy.sla_minutes
            if breach_min > 0:
                ratio = min(breach_min / config.urgency_full_breach_minutes, 1.0)
                candidates.append(
                    (
                        ratio,
                        f"never_contacted: {breach_min:.0f} min beyond "
                        f"{policy.sla_minutes} min SLA "
                        f"(full points at {config.urgency_full_breach_minutes} min)",
                    )
                )
        else:
            candidates.append((0.0, "never_contacted signal but creation time unknown"))

    if "stalled_follow_up" in names:
        last_contact = as_utc(lead.get("last_contact_at"))
        if last_contact is not None:
            stalled_days = (now - last_contact).days - policy.stall_days
            if stalled_days > 0:
                ratio = min(stalled_days / config.urgency_full_stall_days, 1.0)
                candidates.append(
                    (
                        ratio,
                        f"stalled_follow_up: {stalled_days} days beyond "
                        f"{policy.stall_days} day stall window "
                        f"(full points at {config.urgency_full_stall_days} days)",
                    )
                )
        else:
            candidates.append((0.0, "stalled_follow_up signal but last contact unknown"))

    if not candidates:
        return 0.0, "no verified SLA/stall breach — no points awarded"
    ratio, evidence = max(candidates, key=lambda c: c[0])
    return ratio, evidence


def _intent_ratio(
    lead: Dict[str, Any], config: PriorityConfig
) -> Tuple[float, str]:
    """Evidence-backed engagement only.

    Half the factor for a recorded prior contact (real interaction on
    record), half for any configured intent flag actually set in
    ``custom_fields``. With no engagement data the ratio is 0 — intent
    is never inferred from missing information.
    """
    ratio = 0.0
    notes: List[str] = []

    if as_utc(lead.get("last_contact_at")) is not None:
        ratio += 0.5
        notes.append(f"prior contact recorded at {lead.get('last_contact_at')}")
    else:
        notes.append("no prior contact recorded")

    if config.intent_flags:
        custom = lead.get("custom_fields") or {}
        if isinstance(custom, dict):
            found = sorted(f for f in config.intent_flags if _truthy(custom.get(f)))
        else:
            found = []
        if found:
            ratio += 0.5
            notes.append(f"intent flag(s) set: {', '.join(found)}")
        else:
            notes.append("no configured intent flag set")
    return ratio, "; ".join(notes)


def _recovery_potential_ratio(
    signals: List[Dict[str, str]]
) -> Tuple[float, str]:
    """Strength of the verified recovery signals persisted on the case."""
    if not signals:
        return 0.0, "no verified recovery signals on the case — no points awarded"
    best = 0.0
    for s in signals:
        severity = str(s.get("severity") or "").lower()
        best = max(best, _SEVERITY_RATIO.get(severity, _DEFAULT_SEVERITY_RATIO))
    described = ", ".join(
        f"{s.get('signal')} ({s.get('severity')})" for s in signals
    )
    return best, f"signals: {described}"


def _contactability_ratio(
    lead: Dict[str, Any]
) -> Tuple[float, str]:
    """Real, usable contact data — never placeholder values.

    Every ranked case already passed the eligibility contact rule, so a
    usable phone is the norm; email and language add real reachability
    and localization value. Absent fields simply add nothing.
    """
    ratio = 0.0
    notes: List[str] = []

    phone = lead.get("phone")
    if phone is not None and (not isinstance(phone, str) or phone.strip()):
        ratio += 0.6
        notes.append("phone available")
    else:
        notes.append("no usable phone")

    email = lead.get("email")
    if email is not None and (not isinstance(email, str) or email.strip()):
        ratio += 0.2
        notes.append("email available")

    language = lead.get("language")
    if language is not None and (not isinstance(language, str) or language.strip()):
        ratio += 0.2
        notes.append(f"language {language}")

    return ratio, "; ".join(notes)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def score_priority(
    lead: Dict[str, Any],
    signals: List[Dict[str, str]],
    policy: RecoveryPolicy,
    config: PriorityConfig,
    now: datetime,
) -> PriorityScore:
    """Compute the deterministic 0..100 priority score for one case.

    ``lead`` is the *current* canonical lead data (freshly read, so a
    changed deal value or contact detail is reflected on the next run);
    ``signals`` are the verified recovery signals already persisted on
    the case. Pure function: same inputs → same output, always.
    """
    total = config.total_weight
    raw = [
        (
            FACTOR_BUSINESS_VALUE,
            *_business_value_ratio(lead, config),
            config.weight_business_value,
        ),
        (
            FACTOR_URGENCY,
            *_urgency_ratio(lead, signals, policy, config, now),
            config.weight_urgency,
        ),
        (FACTOR_INTENT, *_intent_ratio(lead, config), config.weight_intent),
        (
            FACTOR_RECOVERY_POTENTIAL,
            *_recovery_potential_ratio(signals),
            config.weight_recovery_potential,
        ),
        (FACTOR_CONTACTABILITY, *_contactability_ratio(lead), config.weight_contactability),
    ]

    factors: List[FactorResult] = []
    for name, ratio, evidence, weight in raw:
        # Round per factor, then sum — the breakdown always adds up to
        # the reported score exactly (explainable to the cent).
        points = round(max(min(ratio, 1.0), 0.0) * weight * 100.0 / total, 2)
        factors.append(
            FactorResult(
                factor=name,
                points=points,
                max_points=round(weight * 100.0 / total, 2),
                evidence=evidence,
            )
        )

    score = round(min(max(sum(f.points for f in factors), 0.0), 100.0), 2)
    level = priority_level_for(score, config)
    explanation = _explain(score, level, factors)
    return PriorityScore(score=score, level=level, factors=tuple(factors), explanation=explanation)


def _explain(score: float, level: str, factors: List[FactorResult]) -> str:
    """One deterministic sentence per factor — why this lead got this score."""
    parts = []
    for f in factors:
        if f.points > 0:
            parts.append(f"{f.factor} {f.points:g}/{f.max_points:g} ({f.evidence})")
        else:
            parts.append(f"{f.factor} 0/{f.max_points:g} ({f.evidence})")
    return f"priority {score:g}/100 ({level}): " + "; ".join(parts)
