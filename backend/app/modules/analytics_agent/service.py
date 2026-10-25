"""Analytics Service coordinating all analytics dimensions."""

from typing import Optional
from sqlalchemy.orm import Session

from app.modules.analytics_agent.conversation import ConversationAnalyzer
from app.modules.analytics_agent.cost_revenue import CostRevenueAnalyzer
from app.modules.analytics_agent.leakage import LeakageDetector
from app.modules.analytics_agent.performance import PerformanceAnalyzer
from app.modules.analytics_agent.recovery import RecoveryAnalyzer
from app.modules.analytics_agent.schemas import (
    AnalyticsSummaryResponse,
    ConversationAnalyticsResponse,
    LeadFunnelSummary,
    SalesLeakageResponse,
)


class AnalyticsService:
    """Core coordinator for business analytics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_summary(
        self,
        business_id: str,
        period: Optional[str] = "all_time",
    ) -> AnalyticsSummaryResponse:
        """Compute full summary metrics across funnel, recovery, performance, and financials."""
        leakage = LeakageDetector.calculate_sales_leakage(self.session, business_id)
        recovery_metrics = RecoveryAnalyzer.calculate_recovery_metrics(self.session, business_id)
        performance_kpis = PerformanceAnalyzer.calculate_performance_kpis(self.session, business_id)
        financials = CostRevenueAnalyzer.calculate_cost_and_revenue(self.session, business_id)
        conversations = ConversationAnalyzer.get_conversation_analytics(self.session, business_id, limit=5)

        # Stage entered/converted numbers
        s1 = leakage.stages[0] if len(leakage.stages) > 0 else None
        s2 = leakage.stages[1] if len(leakage.stages) > 1 else None
        s3 = leakage.stages[2] if len(leakage.stages) > 2 else None
        s4 = leakage.stages[3] if len(leakage.stages) > 3 else None
        s5 = leakage.stages[4] if len(leakage.stages) > 4 else None

        lead_funnel = LeadFunnelSummary(
            total_leads=s1.entered if s1 else 0,
            contact_attempted=s1.converted if s1 else 0,
            calls_connected=s2.converted if s2 else 0,
            leads_qualified=s3.converted if s3 else 0,
            handoffs_initiated=s4.converted if s4 else 0,
            recovered_opportunities=recovery_metrics.recovered_count,
        )

        return AnalyticsSummaryResponse(
            business_id=business_id,
            period=period or "all_time",
            lead_funnel=lead_funnel,
            rates_and_kpis=performance_kpis,
            recovery_metrics=recovery_metrics,
            financials=financials,
            top_loss_reasons=leakage.loss_reasons[:5],
            top_objections=conversations.common_objections[:5],
        )

    def get_leakage(
        self,
        business_id: str,
    ) -> SalesLeakageResponse:
        """Compute stage-by-stage sales leakage analysis."""
        return LeakageDetector.calculate_sales_leakage(self.session, business_id)

    def get_conversations(
        self,
        business_id: str,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> ConversationAnalyticsResponse:
        """Compute conversation intelligence, sentiment breakdown, and per-call insights."""
        return ConversationAnalyzer.get_conversation_analytics(
            self.session,
            business_id=business_id,
            limit=limit,
            offset=offset,
            status_filter=status,
            sentiment_filter=sentiment,
        )
