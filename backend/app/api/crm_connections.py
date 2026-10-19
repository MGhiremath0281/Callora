"""API routes for CRM connection management.

Businesses configure their external CRM by submitting its base URL and
its own x-api-key credential. The connection is validated with a real
request before it is saved; the key is stored server-side and never
returned.
"""

from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.db.session import get_db
from app.logging_config import bind_context, get_logger
from app.models import CRMConnection
from app.modules.crm_sync.schemas import (
    CRMConnectionResponse,
    CRMConnectionSaveResponse,
    CRMConnectionUpsertRequest,
)
from app.modules.crm_sync.service import CRMSyncService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/crm/connections",
    tags=["CRM Connections"],
)


def _to_response(connection: CRMConnection) -> CRMConnectionResponse:
    return CRMConnectionResponse(
        connection_id=connection.connection_id,
        name=connection.name,
        is_active=connection.is_active,
        crm_base_url=connection.crm_base_url,
        has_api_key=bool(connection.crm_api_key),
        crm_timeout_seconds=connection.crm_timeout_seconds,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
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
    """List all CRM connections for the authenticated business.

    The external CRM API key is never included; ``has_api_key`` only
    reports whether one is configured.
    """
    connections = (
        db.query(CRMConnection)
        .filter(CRMConnection.business_id == business_id)
        .all()
    )
    return [_to_response(c) for c in connections]


@router.post(
    "",
    response_model=CRMConnectionSaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or update the external CRM connection",
)
async def upsert_crm_connection(
    payload: CRMConnectionUpsertRequest,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    """Save the business's external CRM connection after real validation.

    The submitted URL and ``crm_x_api_key`` belong to the external CRM
    (an independent credential from Callora's inbound X-API-Key). Before
    anything is persisted, Callora calls the CRM's ``GET /health`` with
    the supplied credentials:

    - auth rejected -> ``502 crm_authentication_failed``
    - unreachable   -> ``503 crm_unavailable`` / ``504 crm_timeout``
    - other failure -> ``502 crm_connection_validation_failed``

    Nothing is saved unless validation succeeds, and a connection is
    never reported as verified unless the check actually passed. Existing
    rows with the same ``connection_id`` are updated in place.
    """
    bind_context(business=business_id)

    connection = (
        db.query(CRMConnection)
        .filter(
            CRMConnection.business_id == business_id,
            CRMConnection.connection_id == payload.connection_id,
        )
        .first()
    )
    created = connection is None
    if connection is None:
        connection = CRMConnection(
            business_id=business_id,
            connection_id=payload.connection_id,
            name=payload.name,
            is_active=True,
            crm_base_url=payload.crm_base_url,
            crm_timeout_seconds=payload.crm_timeout_seconds,
        )
        db.add(connection)
    else:
        connection.name = payload.name
        connection.crm_base_url = payload.crm_base_url
        connection.crm_timeout_seconds = payload.crm_timeout_seconds
    connection.crm_api_key = payload.crm_x_api_key

    # Real connectivity + authentication check against the external CRM.
    # Runs before commit: if it fails, nothing is persisted or changed.
    await CRMSyncService.validate_connection(connection)

    connection.is_active = True
    db.commit()
    db.refresh(connection)

    # Log the outcome only — never the URL's userinfo or the API key.
    logger.info(
        "crm connection validated and saved",
        extra={
            "connection_id": connection.connection_id,
            "is_new_connection": created,
            "verified": True,
        },
    )
    return CRMConnectionSaveResponse(
        connection=_to_response(connection),
        verified=True,
        message=(
            "Connection validated against the external CRM "
            "(GET /health) and saved."
        ),
    )
