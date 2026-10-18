"""Lead endpoints: JSON creation, listing, retrieval.

See ``app/api/imports.py`` for the CSV import workflow endpoints.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import ConflictError, LeadNotFoundError
from app.logging_config import bind_context, get_logger
from app.models import IdempotencyKey, Lead
from app.schemas.lead import LeadCreate, LeadListResponse, LeadRead
from app.services import events as events_service
from app.services import importer as importer_service

logger = get_logger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


def _publish_events(session: Session, settings: Settings) -> None:
    """Deliver any pending outbox events; failures never fail the request."""
    importer_service.finalize_import(session, settings)
    session.commit()


@router.post(
    "",
    response_model=LeadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a lead from a JSON request",
)
def create_lead(
    payload: LeadCreate,
    response: Response,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    bind_context(business=business_id)

    if idempotency_key:
        replay = (
            db.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.business_id == business_id,
                    IdempotencyKey.scope == "create_lead",
                    IdempotencyKey.key == idempotency_key,
                )
            )
            .scalars()
            .first()
        )
        if replay and replay.response_ref:
            existing = db.get(Lead, replay.response_ref)
            if existing is not None:
                response.status_code = status.HTTP_200_OK
                response.headers["Idempotency-Replayed"] = "true"
                return existing

    existing_phone = (
        db.execute(
            select(Lead).where(Lead.business_id == business_id, Lead.phone == payload.phone)
        )
        .scalars()
        .first()
    )
    if existing_phone is not None:
        raise ConflictError(
            f"a lead with phone '{payload.phone}' already exists",
            details=[{"lead_id": existing_phone.id, "reason": "duplicate_phone"}],
        )

    custom_fields = dict(payload.custom_fields)
    if settings.custom_field_allowlist_set:
        custom_fields = {
            k: v for k, v in custom_fields.items() if k in settings.custom_field_allowlist_set
        }
    if not settings.auto_capture_custom_fields and not settings.custom_field_allowlist_set:
        custom_fields = {}

    lead = Lead(
        business_id=business_id,
        name=payload.name,
        phone=payload.phone,
        email=str(payload.email) if payload.email else None,
        source=payload.source,
        language=payload.language,
        deal_value=payload.deal_value,
        last_contact_at=payload.last_contact_at,
        custom_fields=custom_fields,
        external_id=payload.external_id,
    )
    db.add(lead)
    db.flush()
    # The outbox event is committed atomically with the lead so that a
    # committed lead always has a reliable lead.created event record.
    events_service.enqueue_lead_created(db, business_id, lead.id)

    if idempotency_key:
        try:
            with db.begin_nested():
                db.add(
                    IdempotencyKey(
                        business_id=business_id,
                        scope="create_lead",
                        key=idempotency_key,
                        response_ref=lead.id,
                    )
                )
                db.flush()
        except IntegrityError:
            logger.warning("concurrent idempotency key replay", extra={"key": idempotency_key})

    db.commit()
    _publish_events(db, settings)
    logger.info("lead created", extra={"lead_id": lead.id})
    return lead


@router.get("", response_model=LeadListResponse, summary="List leads with filters and pagination")
def list_leads(
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
    source: Optional[str] = Query(default=None, description="Filter by lead source"),
    query: Optional[str] = Query(default=None, description="Case-insensitive name search"),
    created_after: Optional[datetime] = Query(default=None),
    created_before: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    bind_context(business=business_id)
    filters = [Lead.business_id == business_id]
    if source:
        filters.append(Lead.source == source.strip().lower())
    if query:
        filters.append(func.lower(Lead.name).contains(query.strip().lower()))
    if created_after:
        filters.append(Lead.created_at >= created_after)
    if created_before:
        filters.append(Lead.created_at <= created_before)

    total = db.execute(select(func.count()).select_from(Lead).where(*filters)).scalar_one()
    rows: List[Lead] = list(
        db.execute(
            select(Lead)
            .where(*filters)
            .order_by(Lead.created_at.desc(), Lead.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return LeadListResponse(items=rows, total=total, page=page, page_size=page_size)


@router.get("/{lead_id}", response_model=LeadRead, summary="Retrieve an individual lead")
def get_lead(
    lead_id: str,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    bind_context(business=business_id)
    lead = db.get(Lead, lead_id)
    # 404 for both "missing" and "owned by another business" so leads
    # cannot be probed across tenants.
    if lead is None or lead.business_id != business_id:
        raise LeadNotFoundError(lead_id)
    return lead
