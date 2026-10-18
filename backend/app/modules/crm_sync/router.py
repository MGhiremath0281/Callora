"""API routes for CRM synchronization."""

from fastapi import APIRouter, HTTPException, status

from app.modules.crm_sync.client import CRMClient, CRMClientError
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
async def sync_lead(request: CRMSyncRequest):
    """Synchronize one Module 1 lead with an external CRM."""

    try:
        client = CRMClient()
    except CRMClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        service = CRMSyncService(client)
        return await service.sync_lead(request)
    except CRMClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    finally:
        await client.close()