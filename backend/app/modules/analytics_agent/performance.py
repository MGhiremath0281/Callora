"""Performance Analytics for Callora.

Reports follow-up and conversion KPIs across voice calling and lead outreach.
"""

from typing import List
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Lead, RecoveryCase, RecoveryCaseStatus
from app.modules.voice.models import Call, CallOutcome, Handoff
from app.modules.analytics_agent.schemas import ConversionAndPerformanceKPIs


class PerformanceAnalyzer:
    """Computes conversion, connection, and follow-up KPIs."""

    @classmethod
    def calculate_performance_kpis(
        cls,
        session: Session,
        business_id: str,
    ) -> ConversionAndPerformanceKPIs:
        """Compute performance KPIs safely handling empty datasets."""
        # 1. Calls query
        calls = session.execute(
            select(Call).where(Call.business_id == business_id)
        ).scalars().all()

        total_calls = len(calls)
        connected_calls = [
            c for c in calls
            if (c.duration_seconds and c.duration_seconds > 0)
            or c.status in ("completed", "in_progress", "disconnected")
        ]
        connected_count = len(connected_calls)

        connection_rate = (
            round((connected_count / total_calls) * 100.0, 1)
            if total_calls > 0
            else 0.0
        )

        total_duration = sum(c.duration_seconds or 0 for c in connected_calls)
        avg_duration = (
            round(total_duration / connected_count, 1)
            if connected_count > 0
            else 0.0
        )

        # 2. Outcomes query
        outcomes = session.execute(
            select(CallOutcome).where(CallOutcome.business_id == business_id)
        ).scalars().all()

        qualified_count = sum(
            1 for o in outcomes if o.qualified is True or o.disposition == "qualified"
        )
        qualification_rate = (
            round((qualified_count / connected_count) * 100.0, 1)
            if connected_count > 0
            else 0.0
        )

        # Follow-up actions
        follow_ups_count = sum(
            len(o.follow_up_actions) for o in outcomes if o.follow_up_actions
        )

        # 3. Handoffs query
        handoffs = session.execute(
            select(Handoff).where(Handoff.business_id == business_id)
        ).scalars().all()

        callbacks_req = sum(
            1 for h in handoffs if h.kind == "callback"
        ) + sum(
            1 for o in outcomes if o.disposition == "callback_requested"
        )
        callbacks_comp = sum(
            1 for h in handoffs
            if h.kind == "callback" and h.status in ("completed", "resolved")
        )
        warm_transfers_comp = sum(
            1 for h in handoffs
            if h.kind == "warm_transfer" and h.status in ("completed", "initiated")
        )

        # 4. Total leads and recovery for overall conversion
        total_leads = session.execute(
            select(func.count()).select_from(Lead).where(Lead.business_id == business_id)
        ).scalar_one()

        cases = session.execute(
            select(RecoveryCase).where(RecoveryCase.business_id == business_id)
        ).scalars().all()
        recovered_count = sum(
            1 for c in cases if c.status == RecoveryCaseStatus.RECOVERED
        )
        recovery_rate = (
            round((recovered_count / len(cases)) * 100.0, 1)
            if cases
            else 0.0
        )

        # Successful conversion = qualified leads + recovered cases
        successful_leads_set = {
            o.lead_id for o in outcomes if o.qualified is True or o.disposition == "qualified"
        } | {
            c.lead_id for c in cases if c.status == RecoveryCaseStatus.RECOVERED
        }
        overall_conversion_rate = (
            round((len(successful_leads_set) / total_leads) * 100.0, 1)
            if total_leads > 0
            else 0.0
        )

        return ConversionAndPerformanceKPIs(
            connection_rate_pct=connection_rate,
            qualification_rate_pct=qualification_rate,
            recovery_rate_pct=recovery_rate,
            overall_conversion_rate_pct=overall_conversion_rate,
            follow_ups_scheduled=follow_ups_count,
            callbacks_requested=callbacks_req,
            callbacks_completed=callbacks_comp,
            warm_transfers_completed=warm_transfers_comp,
            average_duration_seconds=avg_duration,
        )
