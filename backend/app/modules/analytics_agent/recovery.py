"""Recovery Analytics for Callora.

Calculates recovered opportunities, recovery rates, priority-level breakdowns,
and channel effectiveness from stored recovery cases and outreach records.
"""

from typing import Any, Dict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Lead, RecoveryCase, RecoveryCaseStatus
from app.modules.analytics_agent.schemas import RecoveryMetrics


class RecoveryAnalyzer:
    """Computes recovery performance metrics."""

    @classmethod
    def calculate_recovery_metrics(
        cls,
        session: Session,
        business_id: str,
    ) -> RecoveryMetrics:
        """Compute recovery KPIs safely handling empty or missing records."""
        # 1. Total leads for lead-to-recovery conversion rate
        total_leads = session.execute(
            select(func.count()).select_from(Lead).where(Lead.business_id == business_id)
        ).scalar_one()

        # 2. Recovery cases count by status
        cases = session.execute(
            select(RecoveryCase).where(RecoveryCase.business_id == business_id)
        ).scalars().all()

        total_cases = len(cases)
        status_counts: Dict[str, int] = {
            RecoveryCaseStatus.RECOMMENDED: 0,
            RecoveryCaseStatus.IN_RECOVERY: 0,
            RecoveryCaseStatus.RECOVERED: 0,
            RecoveryCaseStatus.EXHAUSTED: 0,
            RecoveryCaseStatus.ARCHIVED: 0,
        }
        for c in cases:
            if c.status in status_counts:
                status_counts[c.status] += 1
            else:
                status_counts[c.status] = status_counts.get(c.status, 0) + 1

        recovered_count = status_counts[RecoveryCaseStatus.RECOVERED]
        in_recovery_count = status_counts[RecoveryCaseStatus.IN_RECOVERY]
        exhausted_count = status_counts[RecoveryCaseStatus.EXHAUSTED]

        recovery_rate = (
            round((recovered_count / total_cases) * 100.0, 1)
            if total_cases > 0
            else 0.0
        )
        conversion_rate = (
            round((recovered_count / total_leads) * 100.0, 1)
            if total_leads > 0
            else 0.0
        )

        # 3. Breakdown by priority level (critical, high, medium, low)
        priority_breakdown: Dict[str, Dict[str, Any]] = {
            "critical": {"total": 0, "recovered": 0, "rate_pct": 0.0},
            "high": {"total": 0, "recovered": 0, "rate_pct": 0.0},
            "medium": {"total": 0, "recovered": 0, "rate_pct": 0.0},
            "low": {"total": 0, "recovered": 0, "rate_pct": 0.0},
        }

        for c in cases:
            lvl = (c.priority_level or "medium").lower()
            if lvl not in priority_breakdown:
                lvl = "medium"
            priority_breakdown[lvl]["total"] += 1
            if c.status == RecoveryCaseStatus.RECOVERED:
                priority_breakdown[lvl]["recovered"] += 1

        for lvl, data in priority_breakdown.items():
            tot = data["total"]
            rec = data["recovered"]
            data["rate_pct"] = round((rec / tot) * 100.0, 1) if tot > 0 else 0.0

        return RecoveryMetrics(
            total_eligible_cases=total_cases,
            in_recovery=in_recovery_count,
            recovered_count=recovered_count,
            exhausted_count=exhausted_count,
            recovery_rate_pct=recovery_rate,
            conversion_rate_pct=conversion_rate,
            priority_breakdown=priority_breakdown,
        )
