"""Handoff endpoints.

    GET   /api/v1/voice/handoffs              list the handoffs of the business
    GET   /api/v1/voice/handoffs/{id}         read one handoff
    POST  /api/v1/voice/handoffs              request a callback
    PATCH /api/v1/voice/handoffs/{id}         update the status / outcome

A handoff is the hand-off surface between the AI agent and a human: a
warm transfer that already happened, or a callback somebody has to make.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.config import Settings, get_settings
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import current_business
from app.modules.voice.api.serializers import handoff_payload, list_payload
from app.modules.voice.errors import HandoffNotFoundError
from app.modules.voice.handoff import HandoffService
from app.modules.voice.phone import normalize_phone
from app.modules.voice.schemas import CallbackRequest, HandoffResponse, HandoffUpdate

router = APIRouter(
    prefix="/voice/handoffs",
    tags=["Voice — Handoffs"],
)


def _handoff_or_404(session: Session, business_id: str, handoff_id: str):
    handoff = repo.get_handoff(session, business_id, handoff_id)
    if handoff is None:
        raise HandoffNotFoundError(handoff_id)
    return handoff


@router.get("", status_code=status.HTTP_200_OK)
async def list_handoffs(
    business_id: str = Depends(current_business),
    handoff_status: Optional[str] = Query(default=None, alias="status"),
    kind: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List the handoffs of the business."""
    handoffs = repo.list_handoffs(
        db, business_id, status=handoff_status, kind=kind, limit=limit, offset=offset
    )
    items = [handoff_payload(handoff) for handoff in handoffs]
    return list_payload(items, business_id, limit, offset)


@router.get("/{handoff_id}", response_model=HandoffResponse, status_code=status.HTTP_200_OK)
async def read_handoff(
    handoff_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read one handoff."""
    return handoff_payload(_handoff_or_404(db, business_id, handoff_id))


@router.post(
    "",
    response_model=HandoffResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_callback(
    payload: CallbackRequest,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Ask a salesperson to call a lead back.

    The request is recorded in the same handoff queue as the automated
    ones, so nothing is lost between the agent and the sales team.
    """
    if not payload.lead_id and not payload.call_id:
        from app.errors import InvalidParameterError

        raise InvalidParameterError(
            "either lead_id or call_id must be supplied for a callback request"
        )
    lead_name = None
    lead_id = payload.lead_id
    if payload.call_id:
        call = repo.get_call(db, business_id, payload.call_id)
        if call is None:
            from app.modules.voice.errors import CallNotFoundError

            raise CallNotFoundError(payload.call_id)
        lead_id = lead_id or call.lead_id
    if lead_id:
        lead = repo.get_lead(db, business_id, lead_id)
        if lead is None:
            from app.errors import LeadNotFoundError

            raise LeadNotFoundError(lead_id)
        lead_name = lead.name

    service = HandoffService(settings, db)
    handoff = service.request_callback(
        business_id=business_id,
        lead_id=lead_id,
        call_id=payload.call_id,
        reason=payload.reason,
        briefing=payload.briefing,
        requested_by=payload.requested_by,
        target_number=payload.target_number,
        lead_name=lead_name,
        context={"target_number": normalize_phone(payload.target_number)}
        if payload.target_number
        else {},
    )
    db.commit()
    return handoff_payload(handoff)


@router.patch(
    "/{handoff_id}",
    response_model=HandoffResponse,
    status_code=status.HTTP_200_OK,
)
async def update_handoff(
    handoff_id: str,
    payload: HandoffUpdate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Update the status or the outcome of a handoff."""
    handoff = _handoff_or_404(db, business_id, handoff_id)
    service = HandoffService(settings, db)
    if payload.outcome:
        service.record_outcome(handoff, payload.outcome, payload.note)
    if payload.status:
        try:
            service.set_status(handoff, payload.status, payload.note)
        except ValueError as exc:
            from app.errors import InvalidParameterError

            raise InvalidParameterError(str(exc))
    db.commit()
    return handoff_payload(handoff)


__all__ = ["router"]
