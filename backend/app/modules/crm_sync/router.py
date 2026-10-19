"""API routes for CRM synchronization."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.modules.crm_sync.schemas import (
    CRMSyncRequest,
    CRMSyncResponse,
)
from app.modules.crm_sync.service import CRMSyncService

router = APIRouter(
    prefix="/crm/sync",
    tags=["CRM Synchronization"],
)


@router.post(
    "",
    response_model=CRMSyncResponse,
    status_code=status.HTTP_200_OK,
)
async def sync_lead(
    request: CRMSyncRequest,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    """Synchronize one Module 1 lead with an external CRM.

    The business_id is derived from the authenticated API key and must
    match the lead's business ownership.
    """
    # Ensure the lead's business matches the authenticated business.
    if request.lead.business_id != business_id:
        from app.errors import LeadNotFoundError

        raise LeadNotFoundError(request.lead.id)

    service = CRMSyncService(db)
    return await service.sync_lead(request)
