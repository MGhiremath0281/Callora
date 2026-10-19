"""API routes for CRM connection management."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.modules.crm_sync.schemas import CRMConnectionResponse
from app.models import CRMConnection

router = APIRouter(
    prefix="/crm/connections",
    tags=["CRM Connections"],
)


@router.get(
    "",
    response_model=list[CRMConnectionResponse],
    status_code=status.HTTP_200_OK,
)
async def list_crm_connections(
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    """List all CRM connections for the authenticated business."""
    connections = (
        db.query(CRMConnection)
        .filter(CRMConnection.business_id == business_id)
        .all()
    )
    return connections
