"""API routes for Module 3 — Recovery Agent (Opportunity Detection).

    POST /api/v1/agent/recovery/run         run a detection sweep
    GET  /api/v1/agent/recovery/cases       list recovery cases
    GET  /api/v1/agent/recovery/cases/{id}  retrieve one case

The sweep fetches leads through GET /api/v1/agent/leads using the
caller's own X-API-Key (the same documented contract external agents
use), then persists ``recovery_cases`` rows — the hand-off surface for
downstream workflow steps.
"""

from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import LeadNotFoundError
from app.models import RecoveryCase
from app.modules.recovery_agent import repository
from app.modules.recovery_agent.client import AgentLeadsClient
from app.modules.recovery_agent.service import run_recovery_engine
from starlette.concurrency import run_in_threadpool

router = APIRouter(
    prefix="/agent/recovery",
    tags=["Recovery Agent"],
)


async def business_credentials(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    business_id: str = Depends(authenticate_business),
) -> Tuple[str, str]:
    """Resolve (business_id, raw api key) — the sweep replays the key
    against the agent-leads API exactly as the ticket specifies."""
    return business_id, x_api_key


def build_client(settings: Settings, api_key: str) -> AgentLeadsClient:
    """Overridable factory so tests can inject transports."""
    return AgentLeadsClient(settings, api_key)


def _case_payload(case: RecoveryCase) -> Dict[str, Any]:
    return {
        "case_id": case.id,
        "lead_id": case.lead_id,
        "lead_name": case.lead_name,
        "business_id": case.business_id,
        "score": float(case.score) if case.score is not None else None,
        # Module 5 (Priority Scoring) — additive fields; the existing
        # ``score`` above keeps its Module 3 recoverability meaning.
        "priority_score": (
            float(case.priority_score) if case.priority_score is not None else None
        ),
        "priority_rank": case.priority_rank,
        "priority_level": case.priority_level,
        "score_breakdown": case.score_breakdown,
        "scored_at": case.scored_at.isoformat() if case.scored_at else None,
        "signals": case.signals,
        "route": case.route,
        "action": case.action,
        "channel": case.channel,
        "message": case.message,
        "rationale": case.rationale,
        "engine": case.engine,
        "needs_human_approval": case.needs_human_approval,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


@router.post("/run", status_code=status.HTTP_200_OK)
async def run_recovery_sweep(
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Fetch agent-ready leads and run the opportunity-detection sweep.

    Errors (API unreachable, timeout, malformed payload) surface as
    documented 502/503/504 responses; the workflow itself is unaffected
    and the sweep can simply be retried.
    """
    business_id, api_key = credentials
    client = build_client(settings, api_key)
    try:
        leads = await client.fetch_leads()
        return await run_in_threadpool(
            run_recovery_engine, db, business_id, leads, settings
        )
    finally:
        await client.aclose()


@router.get("/cases", status_code=status.HTTP_200_OK)
async def list_recovery_cases(
    credentials: Tuple[str, str] = Depends(business_credentials),
    case_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List stored recovery cases in priority order (hand-off surface).

    Items are ranked by Module 5 (priority score desc, recoverability
    score desc, case id asc) and carry additive ``priority_*`` /
    ``score_breakdown`` fields. The ``ranking`` block summarizes ALL
    scored cases of the business (not just the current page).
    """
    business_id, _ = credentials
    cases = repository.list_cases(db, business_id, case_status, limit, offset)
    return {
        "items": [_case_payload(c) for c in cases],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
        "ranking": repository.priority_summary(db, business_id),
    }


@router.get("/cases/{case_id}", status_code=status.HTTP_200_OK)
async def get_recovery_case(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Retrieve one recovery case by ID, scoped to the business."""
    business_id, _ = credentials
    case = repository.get_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    return _case_payload(case)
