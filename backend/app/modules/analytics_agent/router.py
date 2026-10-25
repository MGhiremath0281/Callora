"""Analytics Agent API Router.

Exposes endpoints for conversation analytics, sales leakage detection,
recovery analytics, cost-revenue analysis, and the visual dashboard.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import get_settings
from app.db.session import get_db
from app.modules.analytics_agent.dashboard_ui import render_analytics_dashboard
from app.modules.analytics_agent.schemas import (
    AnalyticsSummaryResponse,
    ConversationAnalyticsResponse,
    SalesLeakageResponse,
)
from app.modules.analytics_agent.service import AnalyticsService

router = APIRouter(
    prefix="/analytics",
    tags=["Analytics"],
)


def resolve_business_id(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    business_id: Optional[str] = Query(default=None),
) -> str:
    """Resolve business_id from API key header, query param, or default fallback."""
    if x_api_key:
        return authenticate_business(x_api_key)
    if business_id:
        return business_id
    settings = get_settings()
    if settings.api_key_map:
        return next(iter(settings.api_key_map.keys()))
    return "biz_001"


@router.get(
    "/summary",
    response_model=AnalyticsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get business analytics summary",
)
def get_analytics_summary(
    business_id: str = Depends(resolve_business_id),
    period: Optional[str] = Query(default="all_time"),
    db: Session = Depends(get_db),
) -> AnalyticsSummaryResponse:
    """Return high-level summary metrics across funnel, recovery, performance, and financials."""
    service = AnalyticsService(db)
    return service.get_summary(business_id=business_id, period=period)


@router.get(
    "/leakage",
    response_model=SalesLeakageResponse,
    status_code=status.HTTP_200_OK,
    summary="Get sales leakage and funnel drop-off analysis",
)
def get_sales_leakage(
    business_id: str = Depends(resolve_business_id),
    db: Session = Depends(get_db),
) -> SalesLeakageResponse:
    """Calculate sales funnel losses by stage, group loss reasons, and recommend mitigations."""
    service = AnalyticsService(db)
    return service.get_leakage(business_id=business_id)


@router.get(
    "/conversations",
    response_model=ConversationAnalyticsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get conversation analytics and per-call insights",
)
def get_conversation_analytics(
    business_id: str = Depends(resolve_business_id),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    sentiment: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> ConversationAnalyticsResponse:
    """Extract customer intent, objections, sentiment breakdown, and grouped refusal reasons."""
    service = AnalyticsService(db)
    return service.get_conversations(
        business_id=business_id,
        limit=limit,
        offset=offset,
        status=status_filter,
        sentiment=sentiment,
    )


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
    status_code=status.HTTP_200_OK,
    summary="Render interactive analytics dashboard UI",
)
def get_analytics_dashboard_ui(
    business_id: str = Depends(resolve_business_id),
) -> HTMLResponse:
    """Serve the modern glassmorphism analytics dashboard with interactive charts and KPI cards."""
    html = render_analytics_dashboard(business_id=business_id)
    return HTMLResponse(content=html)
