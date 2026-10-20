"""API routes for agent-facing lead queries."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.modules.agent_storage.repository import (
    get_agent_lead,
    get_agent_lead_by_external_id,
    list_agent_leads,
)

router = APIRouter(
    prefix="/agent/leads",
    tags=["Agent Leads"],
)


@router.get(
    "/{lead_id}",
    status_code=status.HTTP_200_OK,
)
async def get_agent_lead_by_id(
    lead_id: str,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    """Retrieve one agent-queryable lead by Callora lead ID."""
    record = get_agent_lead(db, lead_id)
    if record is None or record.business_id != business_id:
        from app.errors import LeadNotFoundError

        raise LeadNotFoundError(lead_id)
    return record


@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def list_agent_leads_endpoint(
    business_id: str = Depends(authenticate_business),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List leads for a business with optional status filtering."""
    records = list_agent_leads(db, business_id, status_filter, limit, offset)
    return {"items": records, "business_id": business_id, "limit": limit, "offset": offset}


@router.get(
    "/by-external/{external_crm_id}",
    status_code=status.HTTP_200_OK,
)
async def get_agent_lead_by_external_crm_id(
    external_crm_id: str,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    """Retrieve a lead by its external CRM ID, scoped to the business."""
    record = get_agent_lead_by_external_id(db, business_id, external_crm_id)
    if record is None:
        from app.errors import LeadNotFoundError

        raise LeadNotFoundError(external_crm_id)
    return record
