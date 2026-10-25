"""Cost and Revenue Analysis for Callora.

Calculates total call spend, recorded revenue recovered, cost per recovered opportunity,
and Return on Investment (ROI) multiple.
"""

from decimal import Decimal
from typing import Dict, List, Set
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentLead, Lead, RecoveryCase, RecoveryCaseStatus
from app.modules.voice.models import Call, CallOutcome
from app.modules.analytics_agent.schemas import CostAndRevenueMetrics


class CostRevenueAnalyzer:
    """Computes cost and revenue analytics."""

    @classmethod
    def calculate_cost_and_revenue(
        cls,
        session: Session,
        business_id: str,
    ) -> CostAndRevenueMetrics:
        """Compute financial performance metrics from stored records."""
        # 1. Calls spend
        calls = session.execute(
            select(Call).where(Call.business_id == business_id)
        ).scalars().all()

        total_cost = 0.0
        currency = "INR"
        for c in calls:
            if c.cost is not None:
                total_cost += float(c.cost)
            if c.currency:
                currency = c.currency

        total_cost = round(total_cost, 2)

        # 2. Leads deal values
        leads = session.execute(
            select(Lead.id, Lead.deal_value).where(Lead.business_id == business_id)
        ).all()
        lead_deal_map = {lid: float(dv) for lid, dv in leads if dv is not None}

        known_values = list(lead_deal_map.values())
        avg_deal_value = (
            sum(known_values) / len(known_values)
            if known_values
            else 50000.0  # sensible fallback ₹50,000
        )
        potential_pipeline = sum(lead_deal_map.get(lid, avg_deal_value) for lid, _ in leads)
        potential_pipeline = round(potential_pipeline, 2)

        # 3. Recovered leads
        recovered_cases = session.execute(
            select(RecoveryCase).where(
                RecoveryCase.business_id == business_id,
                RecoveryCase.status == RecoveryCaseStatus.RECOVERED,
            )
        ).scalars().all()
        recovered_lead_ids: Set[str] = {c.lead_id for c in recovered_cases if c.lead_id}

        agent_leads = session.execute(
            select(AgentLead).where(
                AgentLead.business_id == business_id,
                AgentLead.status.in_(["Closed Won", "Recovered", "Won"]),
            )
        ).scalars().all()
        recovered_lead_ids.update(al.lead_id for al in agent_leads if al.lead_id)

        recorded_recovered_rev = 0.0
        for lid in recovered_lead_ids:
            recorded_recovered_rev += lead_deal_map.get(lid, avg_deal_value)
        recorded_recovered_rev = round(recorded_recovered_rev, 2)

        recovered_count = len(recovered_lead_ids)

        # 4. Qualified leads for cost-per-qualified-lead
        outcomes = session.execute(
            select(CallOutcome).where(CallOutcome.business_id == business_id)
        ).scalars().all()
        qualified_leads = {
            o.lead_id for o in outcomes
            if o.qualified is True or o.disposition == "qualified"
        }
        qualified_count = len(qualified_leads)

        # Cost per recovered opportunity (CAC of recovery)
        cost_per_recovered = (
            round(total_cost / recovered_count, 2)
            if recovered_count > 0
            else 0.0
        )

        cost_per_qualified = (
            round(total_cost / qualified_count, 2)
            if qualified_count > 0
            else 0.0
        )

        net_recovered = round(recorded_recovered_rev - total_cost, 2)
        roi_multiple = (
            round(recorded_recovered_rev / total_cost, 2)
            if total_cost > 0
            else (round(recorded_recovered_rev / 1.0, 2) if recorded_recovered_rev > 0 else 0.0)
        )
        lost_leakage = max(0.0, round(potential_pipeline - recorded_recovered_rev, 2))

        return CostAndRevenueMetrics(
            total_call_cost=total_cost,
            currency=currency,
            recorded_revenue_recovered=recorded_recovered_rev,
            potential_pipeline_value=potential_pipeline,
            lost_revenue_leakage=lost_leakage,
            cost_per_recovered_opportunity=cost_per_recovered,
            cost_per_qualified_lead=cost_per_qualified,
            net_revenue_recovered=net_recovered,
            roi_multiple=roi_multiple,
        )
