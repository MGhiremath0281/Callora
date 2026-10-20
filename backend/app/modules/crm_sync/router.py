"""API routes for CRM synchronization.

``POST /api/v1/crm/sync`` is registered under the ``/api/v1`` prefix only.
It authenticates the caller with Callora's own ``X-API-Key``; the external
CRM's URL and ``x-api-key`` come from the stored connection named by
``connection_id`` and are never supplied per request.

A ``404 lead_not_found`` is not a routing problem: the route exists, but the
``lead.id`` in the request body is not a persisted lead owned by the
authenticated business (the canonical Module 1 record is authoritative).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.errors import LeadNotFoundError
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
        raise LeadNotFoundError(request.lead.id)

    service = CRMSyncService(db)
    return await service.sync_lead(request)
