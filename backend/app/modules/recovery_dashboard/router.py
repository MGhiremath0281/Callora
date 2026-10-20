"""Recovery Dashboard API routes.

All routes require ``X-API-Key`` (business-scoped), enforce isolation,
and never fabricate data. They reuse the existing routing conventions.
"""

from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import (
    InvalidParameterError,
    LeadNotFoundError,
    OutreachBlockedError,
)
from app.models import RecoveryCaseStatus
from app.modules.agent_storage import repository as lead_repo
from app.modules.recovery_agent import repository as case_repo
from datetime import datetime, timezone

from app.modules.recovery_agent.detect import RecoveryPolicy, is_opted_out
from app.modules.recovery_agent.guardrails import hard_block
from app.modules.recovery_agent.policy import eligibility_config, recovered_statuses
from app.modules.recovery_dashboard import outreach
from app.modules.recovery_dashboard import repository as dash_repo

router = APIRouter(
    prefix="/agent/recovery",
    tags=["Recovery Dashboard"],
)


async def business_credentials(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    business_id: str = Depends(authenticate_business),
) -> Tuple[str, str]:
    return business_id, x_api_key


@router.get("/dashboard/summary", status_code=status.HTTP_200_OK)
async def dashboard_summary(
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Total cases, eligible leads, priority counts, awaiting, outcomes."""
    business_id, _ = credentials
    return dash_repo.dashboard_summary(db, business_id)


@router.get("/cases", response_model=None)
async def list_recovery_cases(
    credentials: Tuple[str, str] = Depends(business_credentials),
    case_status: Optional[str] = Query(default=None, alias="status"),
    priority_level: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    needs_human_approval: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated, filterable cases ordered by priority.

    Extends the existing behavior: filters are additive, do not break
    backward compatibility.
    """
    business_id, _ = credentials
    if priority_level is not None:
        level = str(priority_level).lower()
        if level not in ("critical", "high", "medium", "low"):
            raise InvalidParameterError(
                f"priority_level must be one of critical, high, medium, low (got {priority_level!r})"
            )
        priority_level = level

    cases = case_repo.list_cases(
        db,
        business_id,
        status=case_status,
        limit=limit,
        offset=offset,
        priority_level=priority_level,
        query=query,
        needs_human_approval=needs_human_approval,
    )
    total = case_repo.count_cases(
        db,
        business_id,
        status=case_status,
        priority_level=priority_level,
        query=query,
        needs_human_approval=needs_human_approval,
    )
    return {
        "items": [dash_repo.safe_case_dict(c) for c in cases],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
        "total": total,
        "ranking": case_repo.priority_summary(db, business_id),
    }


@router.get("/cases/{case_id}", response_model=None)
async def get_recovery_case(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    business_id, _ = credentials
    case = dash_repo.get_dashboard_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    return dash_repo.safe_case_dict(case)


@router.get("/cases/{case_id}/score-breakdown", status_code=status.HTTP_200_OK)
async def get_score_breakdown(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    business_id, _ = credentials
    case = dash_repo.get_dashboard_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    payload = dash_repo.safe_case_dict(case)
    bd = payload["score_breakdown"] or {}
    return {
        "case_id": case_id,
        "lead_id": payload["lead_id"],
        "priority_score": payload["priority_score"],
        "priority_level": payload["priority_level"],
        "scored_at": payload["scored_at"],
        "score_breakdown": bd,
        "explanation": bd.get("explanation") if isinstance(bd, dict) else None,
        "ranking_reason": payload["rationale"],
    }


@router.get("/cases/{case_id}/outcomes", status_code=status.HTTP_200_OK)
async def get_case_outcomes(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    business_id, _ = credentials
    case = dash_repo.get_dashboard_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    outcomes = dash_repo.execution_outcomes(db, business_id, case_id)
    events = dash_repo.list_outreach_events(db, business_id, case_id, limit=50)
    return {
        "case_id": case_id,
        "has_execution_data": outcomes["has_execution_data"],
        "counts": outcomes["counts"],
        "last_execution_at": outcomes["last_execution_at"],
        "note": outcomes.get("note"),
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "channel": e.channel,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "detail": e.detail,
                "engine": e.engine,
            }
            for e in events
        ],
    }


@router.get("/cases/{case_id}/outreach-content", status_code=status.HTTP_200_OK)
async def get_case_outreach_content(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    business_id, _ = credentials
    case = dash_repo.get_dashboard_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    content = dash_repo.get_outreach_content(db, business_id, case_id)
    if content is not None:
        return {
            "case_id": case_id,
            "source": "cached",
            "generated_at": content.generated_at.isoformat() if content.generated_at else None,
            "regenerated_at": content.regenerated_at.isoformat() if content.regenerated_at else None,
            "generation_count": content.generation_count,
            "engine": content.engine,
            "model": content.model,
            "content": content.content,
        }
    # On-demand safe generation (reuse existing Groq integration; deterministic fallback)
    lead = dash_repo.get_lead(db, business_id, case.lead_id)
    pack = outreach.generate_outreach_pack(dash_repo.safe_case_dict(case), lead_repo.lead_overview(lead) or {}, settings)
    oc = dash_repo.upsert_outreach_content(
        db,
        business_id,
        case_id,
        case.lead_id,
        pack,
        (lead.language if lead else None),
        pack.get("generated_by", {}).get("engine", "rules-fallback"),
        pack.get("generated_by", {}).get("model"),
    )
    dash_repo.record_outreach_event(
        db,
        business_id,
        case_id,
        case.lead_id,
        "content_generated",
        channel=None,
        engine=pack.get("generated_by", {}).get("engine", "rules-fallback"),
        detail={"generated_at": (oc.generated_at.isoformat() if oc.generated_at else None), "generation_count": 1},
    )
    db.commit()
    return {
        "case_id": case_id,
        "source": "generated",
        "generated_at": oc.generated_at.isoformat() if oc.generated_at else None,
        "generation_count": oc.generation_count,
        "engine": oc.engine,
        "model": oc.model,
        "content": oc.content,
    }


@router.post("/cases/{case_id}/outreach-content/generate", status_code=status.HTTP_200_OK)
async def generate_case_outreach_content(
    case_id: str,
    credentials: Tuple[str, str] = Depends(business_credentials),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    business_id, _ = credentials
    case = dash_repo.get_dashboard_case(db, business_id, case_id)
    if case is None:
        raise LeadNotFoundError(case_id)
    lead = dash_repo.get_lead(db, business_id, case.lead_id)
    lead_d = lead_repo.lead_overview(lead) or {}
    # Block if opted out / no usable contact or recovered-like
    if is_opted_out(lead_d):
        raise OutreachBlockedError("Lead opted out or suppressed")
    now = datetime.now(timezone.utc)
    block = hard_block(lead_d, now, RecoveryPolicy.from_settings(settings), 0)
    if block:
        raise OutreachBlockedError(f"Outreach blocked: {block}")
    if lead_d.get("status") and lead_d["status"].lower() in recovered_statuses(eligibility_config(settings)):
        raise OutreachBlockedError("Lead already recovered/closed to recovery")
    pack = outreach.generate_outreach_pack(dash_repo.safe_case_dict(case), lead_d, settings)
    oc = dash_repo.upsert_outreach_content(
        db,
        business_id,
        case_id,
        case.lead_id,
        pack,
        lead_d.get("language") if lead else lead_d.get("language"),
        pack.get("generated_by", {}).get("engine", "rules-fallback"),
        pack.get("generated_by", {}).get("model"),
    )
    dash_repo.record_outreach_event(
        db,
        business_id,
        case_id,
        case.lead_id,
        "content_generated",
        channel=None,
        engine=pack.get("generated_by", {}).get("engine", "rules-fallback"),
        detail={
            "generated_at": (oc.generated_at.isoformat() if oc.generated_at else None),
            "regenerated_at": (oc.regenerated_at.isoformat() if oc.regenerated_at else None),
            "generation_count": oc.generation_count,
            "regenerated": oc.regenerated_at is not None,
        },
    )
    db.commit()
    return {
        "case_id": case_id,
        "source": "generated",
        "generated_at": oc.generated_at.isoformat() if oc.generated_at else None,
        "regenerated_at": oc.regenerated_at.isoformat() if oc.regenerated_at else None,
        "generation_count": oc.generation_count,
        "engine": oc.engine,
        "model": oc.model,
        "content": oc.content,
    }
