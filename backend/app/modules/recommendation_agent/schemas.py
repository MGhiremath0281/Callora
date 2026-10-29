"""Pydantic schemas for Recommendation Agent API."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    call_id: str
    lead_id: Optional[str] = None
    evidence_type: str = "call"
    finding_supported: Optional[str] = None
    transcript_excerpt: Optional[str] = None
    timestamp_in_call: Optional[str] = None


class RecommendationBase(BaseModel):
    business_id: str
    title: str
    finding: str
    rationale: str
    text: str
    category: str
    priority: str
    priority_score: float
    confidence: float
    sample_size: int
    expected_direction: Optional[str] = None
    suggested_next_action: Optional[str] = None
    relevant_funnel_stage: Optional[str] = None
    relevant_script_section: Optional[str] = None


class RecommendationResponse(RecommendationBase):
    id: str
    status: str
    impact_score: float
    frequency_score: float
    recoverability_score: float
    urgency_score: float
    evidence_strength_score: float
    evidence_call_ids: List[str] = []
    created_at: datetime
    updated_at: datetime
    extra_metadata: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class RecommendationDetailResponse(RecommendationResponse):
    evidence: List[Dict[str, Any]] = []
    audit_history: List[Dict[str, Any]] = []
    script_suggestions: List[Dict[str, Any]] = []
    outcome: Optional[Dict[str, Any]] = None


class RecommendationListResponse(BaseModel):
    items: List[RecommendationResponse]
    total: int
    business_id: str


class GenerateRecommendationsRequest(BaseModel):
    business_id: str
    force_refresh: bool = False
    categories: Optional[List[str]] = None
    time_window_days: int = 30


class UpdateRecommendationStatusRequest(BaseModel):
    status: str
    reason: Optional[str] = None
    actor: str = "api"


class ScriptSuggestionResponse(BaseModel):
    id: str
    recommendation_id: Optional[str]
    business_id: str
    current_script: Optional[str]
    proposed_script: Optional[str]
    script_section: Optional[str]
    before_text: Optional[str]
    after_text: Optional[str]
    rationale: str
    reasoning: str
    expected_outcome: Optional[str]
    uncertainty: Optional[str]
    potential_downsides: Optional[str]
    supporting_call_ids: List[str]
    sample_size: int
    status: str
    version_number: int
    created_at: datetime


class ApproveScriptRequest(BaseModel):
    actor: str = "api"
    reason: Optional[str] = None


class RecoveryRecommendationResponse(BaseModel):
    id: str
    business_id: str
    lead_id: str
    recovery_reason: str
    suggested_next_action: str
    recommended_timing: Optional[str]
    supporting_call_ids: List[str]
    current_lead_status: Optional[str]
    current_crm_status: Optional[str]
    confidence: float
    expected_outcome: Optional[str]
    status: str
    created_at: datetime


class AnalyticsSummaryResponse(BaseModel):
    business_id: str
    total_recommendations: int
    new_recommendations: int
    p1_count: int
    p2_count: int
    p3_count: int
    approved_count: int
    implemented_count: int
    analyzed_calls: int
    data_sufficiency: Dict[str, Any]
    effectiveness_summary: Dict[str, Any]
