"""Pydantic schemas for Callora Analytics Agent."""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Funnel & Sales Leakage Schemas
# ---------------------------------------------------------------------------


class StageLossReason(BaseModel):
    reason: str
    count: int = 0
    percentage: float = 0.0
    estimated_lost_value: float = 0.0


class FunnelStageLeakage(BaseModel):
    stage_id: str
    stage_name: str
    entered: int = 0
    converted: int = 0
    dropped: int = 0
    drop_off_rate_pct: float = 0.0
    conversion_rate_pct: float = 0.0
    potential_deal_value: float = 0.0
    lost_deal_value: float = 0.0
    top_loss_reasons: List[StageLossReason] = Field(default_factory=list)


class GroupedLossReason(BaseModel):
    category: str
    reason: str
    count: int = 0
    percentage_of_total_losses: float = 0.0
    estimated_lost_revenue: float = 0.0


class SalesLeakageResponse(BaseModel):
    business_id: str
    total_leads: int = 0
    total_potential_revenue: float = 0.0
    total_lost_revenue: float = 0.0
    overall_leakage_rate_pct: float = 0.0
    stages: List[FunnelStageLeakage] = Field(default_factory=list)
    loss_reasons: List[GroupedLossReason] = Field(default_factory=list)
    actionable_recommendations: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversation Analytics Schemas
# ---------------------------------------------------------------------------


class ObjectionItem(BaseModel):
    objection: str
    count: int = 0
    frequency_pct: float = 0.0
    sample_quote: Optional[str] = None


class RefusalReasonItem(BaseModel):
    refusal_category: str
    count: int = 0
    percentage: float = 0.0
    sample_reasons: List[str] = Field(default_factory=list)


class CallInsightItem(BaseModel):
    call_id: str
    lead_id: str
    lead_name: Optional[str] = None
    duration_seconds: int = 0
    cost: float = 0.0
    status: str
    disposition: str
    qualified: Optional[bool] = None
    customer_intent: Optional[str] = None
    sentiment: str = "neutral"
    sentiment_score: float = 0.5
    objections: List[str] = Field(default_factory=list)
    refusal_category: Optional[str] = None
    refusal_reason: Optional[str] = None
    follow_up_actions: List[str] = Field(default_factory=list)
    handoff_status: Optional[str] = None
    created_at: Optional[str] = None


class SentimentBreakdown(BaseModel):
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    positive_pct: float = 0.0
    neutral_pct: float = 0.0
    negative_pct: float = 0.0


class ConversationAnalyticsResponse(BaseModel):
    business_id: str
    total_conversations: int = 0
    sentiment_breakdown: SentimentBreakdown = Field(default_factory=SentimentBreakdown)
    intent_distribution: Dict[str, int] = Field(default_factory=dict)
    common_objections: List[ObjectionItem] = Field(default_factory=list)
    grouped_refusal_reasons: List[RefusalReasonItem] = Field(default_factory=list)
    call_insights: List[CallInsightItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# ---------------------------------------------------------------------------
# Summary Analytics Schemas
# ---------------------------------------------------------------------------


class LeadFunnelSummary(BaseModel):
    total_leads: int = 0
    contact_attempted: int = 0
    calls_connected: int = 0
    leads_qualified: int = 0
    handoffs_initiated: int = 0
    recovered_opportunities: int = 0


class ConversionAndPerformanceKPIs(BaseModel):
    connection_rate_pct: float = 0.0
    qualification_rate_pct: float = 0.0
    recovery_rate_pct: float = 0.0
    overall_conversion_rate_pct: float = 0.0
    follow_ups_scheduled: int = 0
    callbacks_requested: int = 0
    callbacks_completed: int = 0
    warm_transfers_completed: int = 0
    average_duration_seconds: float = 0.0


class RecoveryMetrics(BaseModel):
    total_eligible_cases: int = 0
    in_recovery: int = 0
    recovered_count: int = 0
    exhausted_count: int = 0
    recovery_rate_pct: float = 0.0
    conversion_rate_pct: float = 0.0
    priority_breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class CostAndRevenueMetrics(BaseModel):
    total_call_cost: float = 0.0
    currency: str = "INR"
    recorded_revenue_recovered: float = 0.0
    potential_pipeline_value: float = 0.0
    lost_revenue_leakage: float = 0.0
    cost_per_recovered_opportunity: float = 0.0
    cost_per_qualified_lead: float = 0.0
    net_revenue_recovered: float = 0.0
    roi_multiple: float = 0.0


class AnalyticsSummaryResponse(BaseModel):
    business_id: str
    period: Optional[str] = "all_time"
    lead_funnel: LeadFunnelSummary = Field(default_factory=LeadFunnelSummary)
    rates_and_kpis: ConversionAndPerformanceKPIs = Field(default_factory=ConversionAndPerformanceKPIs)
    recovery_metrics: RecoveryMetrics = Field(default_factory=RecoveryMetrics)
    financials: CostAndRevenueMetrics = Field(default_factory=CostAndRevenueMetrics)
    top_loss_reasons: List[GroupedLossReason] = Field(default_factory=list)
    top_objections: List[ObjectionItem] = Field(default_factory=list)
