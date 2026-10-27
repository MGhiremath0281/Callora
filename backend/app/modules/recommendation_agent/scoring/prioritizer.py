"""Deterministic priority and confidence scoring engine."""

from typing import Any, Dict
from decimal import Decimal


class PriorityScoringEngine:
    """Implements the documented deterministic scoring system."""

    # Weights as specified
    WEIGHT_IMPACT = 0.35
    WEIGHT_FREQUENCY = 0.20
    WEIGHT_RECOVERABILITY = 0.20
    WEIGHT_URGENCY = 0.10
    WEIGHT_EVIDENCE_STRENGTH = 0.15

    def calculate_score(self, components: Dict[str, float]) -> Dict[str, Any]:
        """Calculate priority score from components (all normalized 0-100)."""
        impact = max(0, min(100, components.get("impact", 0)))
        frequency = max(0, min(100, components.get("frequency", 0)))
        recoverability = max(0, min(100, components.get("recoverability", 0)))
        urgency = max(0, min(100, components.get("urgency", 0)))
        evidence_strength = max(0, min(100, components.get("evidence_strength", 0)))

        score = (
            self.WEIGHT_IMPACT * impact
            + self.WEIGHT_FREQUENCY * frequency
            + self.WEIGHT_RECOVERABILITY * recoverability
            + self.WEIGHT_URGENCY * urgency
            + self.WEIGHT_EVIDENCE_STRENGTH * evidence_strength
        )

        score = round(float(score), 2)

        if score >= 75:
            priority = "P1"
        elif score >= 50:
            priority = "P2"
        else:
            priority = "P3"

        # Calculate confidence separately
        confidence = self.calculate_confidence(components)

        return {
            "priority_score": score,
            "priority": priority,
            "impact_score": impact,
            "frequency_score": frequency,
            "recoverability_score": recoverability,
            "urgency_score": urgency,
            "evidence_strength_score": evidence_strength,
            "confidence": confidence,
        }

    def calculate_confidence(self, components: Dict[str, Any]) -> float:
        """Calculate confidence based on sample size, consistency, completeness."""
        sample_size = components.get("sample_size", 0)
        evidence_count = components.get("evidence_count", 0)
        data_completeness = components.get("data_completeness", 0.5)  # 0-1
        consistency = components.get("consistency", 0.5)  # 0-1

        # Sample size factor
        if sample_size >= 50:
            sample_factor = 1.0
        elif sample_size >= 30:
            sample_factor = 0.9
        elif sample_size >= 20:
            sample_factor = 0.8
        elif sample_size >= 10:
            sample_factor = 0.7
        elif sample_size >= 5:
            sample_factor = 0.6
        else:
            sample_factor = 0.4

        confidence = (
            sample_factor * 0.4
            + min(evidence_count / 10, 1.0) * 0.2
            + data_completeness * 0.2
            + consistency * 0.2
        )
        return round(float(confidence * 100), 2)
