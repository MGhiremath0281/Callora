"""Lead-facing voice endpoints: profile, consent and the DND registry.

    GET    /api/v1/voice/leads                       list the voice state of the leads
    GET    /api/v1/voice/leads/{lead_id}             read the voice state of one lead
    PUT    /api/v1/voice/leads/{lead_id}/profile     change the voice state of one lead
    GET    /api/v1/voice/leads/{lead_id}/consent     read the consent evidence
    PUT    /api/v1/voice/leads/{lead_id}/consent     record the consent evidence
    GET    /api/v1/voice/dnd                         list the DND registry
    POST   /api/v1/voice/dnd                         add a number to the DND registry
    DELETE /api/v1/voice/dnd/{entry_id}              deactivate a DND entry

The canonical lead record lives in Module 1 (``leads``); this module only
adds the voice-specific state, the consent evidence and the
do-not-disturb registry the compliance gate reads.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.errors import LeadNotFoundError
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import current_business
from app.modules.voice.api.serializers import (
    consent_payload,
    dnd_payload,
    list_payload,
)
from app.modules.voice.constants import AuditAction, ConsentChannel
from app.modules.voice.phone import normalize_phone
from app.modules.voice.schemas import (
    ConsentRecordRequest,
    ConsentRecordResponse,
    DndEntryCreate,
    DndEntryResponse,
    VoiceLeadProfile,
    VoiceLeadProfileUpdate,
)

router = APIRouter(
    prefix="/voice",
    tags=["Voice — Leads"],
)


def _lead_or_404(session: Session, business_id: str, lead_id: str):
    lead = repo.get_lead(session, business_id, lead_id)
    if lead is None:
        raise LeadNotFoundError(lead_id)
    return lead


def _profile_payload(session: Session, business_id: str, lead) -> Dict[str, Any]:
    profile = repo.get_lead_profile(session, business_id, lead.id)
    consent = repo.get_consent(session, business_id, lead.id, ConsentChannel.VOICE)
    dnd_entry = None
    phone = normalize_phone(lead.phone)
    if phone:
        dnd_entry = repo.find_dnd_entry(session, business_id, phone)
    return {
        "lead_id": lead.id,
        "business_id": business_id,
        "lead_name": lead.name,
        "phone": phone,
        "preferred_language": (profile.preferred_language if profile else None),
        "timezone": (profile.timezone if profile else None),
        "do_not_call": bool(profile.do_not_call) if profile else False,
        "notes": (profile.notes if profile else None),
        "call_attempts": profile.call_attempts if profile else 0,
        "last_call_at": profile.last_call_at if profile else None,
        "created_at": profile.created_at if profile else None,
        "updated_at": profile.updated_at if profile else None,
        "consent": consent_payload(consent) if consent else None,
        "dnd_active": dnd_entry is not None,
    }


@router.get("/leads", status_code=status.HTTP_200_OK)
async def list_voice_leads(
    business_id: str = Depends(current_business),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List the leads of the business with their voice state."""
    leads = repo.list_leads(db, business_id, limit=limit, offset=offset)
    items = [_profile_payload(db, business_id, lead) for lead in leads]
    return list_payload(items, business_id, limit, offset)


@router.get("/leads/{lead_id}", status_code=status.HTTP_200_OK)
async def read_voice_lead(
    lead_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read the voice state of one lead."""
    lead = _lead_or_404(db, business_id, lead_id)
    return _profile_payload(db, business_id, lead)


@router.put("/leads/{lead_id}/profile", status_code=status.HTTP_200_OK)
async def update_voice_lead_profile(
    lead_id: str,
    payload: VoiceLeadProfileUpdate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Change the voice state of one lead.

    ``do_not_call`` is the per-lead kill switch: setting it blocks every
    future call to that lead, independently of the DND registry.
    """
    lead = _lead_or_404(db, business_id, lead_id)
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    profile = repo.upsert_lead_profile(db, business_id, lead.id, values)
    db.commit()
    return _profile_payload(db, business_id, lead)


@router.get(
    "/leads/{lead_id}/consent",
    response_model=List[ConsentRecordResponse],
    status_code=status.HTTP_200_OK,
)
async def read_consent(
    lead_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read every consent record of one lead."""
    _lead_or_404(db, business_id, lead_id)
    records = repo.list_consents(db, business_id, lead_id)
    return [consent_payload(record) for record in records]


@router.put(
    "/leads/{lead_id}/consent",
    response_model=ConsentRecordResponse,
    status_code=status.HTTP_200_OK,
)
async def record_consent(
    lead_id: str,
    payload: ConsentRecordRequest,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Record (or change) the consent evidence of one lead.

    Consent is *evidence*, not a flag: the record keeps the source, the
    timestamps and the evidence payload so a later compliance decision
    can be audited against what was actually known at the time.
    """
    lead = _lead_or_404(db, business_id, lead_id)
    record = repo.record_consent(
        db,
        business_id=business_id,
        lead_id=lead.id,
        channel=payload.channel,
        status=payload.status,
        phone=payload.phone or normalize_phone(lead.phone),
        source=payload.source,
        granted_at=payload.granted_at,
        expires_at=payload.expires_at,
        revoked_at=payload.revoked_at,
        evidence=payload.evidence,
    )
    repo.add_audit_log(
        db,
        action=AuditAction.CONSENT_RECORDED,
        business_id=business_id,
        actor="api",
        entity_type="consent_record",
        entity_id=record.id,
        decision=payload.status,
        reason_code=f"consent_{payload.status}",
        reason=f"the {payload.channel} consent of the lead was set to '{payload.status}'",
        metadata={"lead_id": lead.id, "source": payload.source},
    )
    db.commit()
    return consent_payload(record)


@router.get("/dnd", status_code=status.HTTP_200_OK)
async def list_dnd_entries(
    business_id: str = Depends(current_business),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List the do-not-disturb registry of the business."""
    entries = repo.list_dnd_entries(db, business_id)
    items = [dnd_payload(entry) for entry in entries[offset : offset + limit]]
    return list_payload(items, business_id, limit, offset)


@router.post(
    "/dnd",
    response_model=DndEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_dnd_entry(
    payload: DndEntryCreate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Add a number to the do-not-disturb registry.

    A ``global`` entry suppresses the number for every business (for
    example a number registered on a national registry); a ``business``
    entry only suppresses it for the calling business.
    """
    phone = normalize_phone(payload.phone)
    if not phone:
        from app.errors import InvalidParameterError

        raise InvalidParameterError("the phone number could not be normalised")
    entry = repo.add_dnd_entry(
        db,
        business_id=business_id,
        phone=phone,
        scope=payload.scope,
        reason=payload.reason,
        source=payload.source,
        expires_at=payload.expires_at,
    )
    repo.add_audit_log(
        db,
        action=AuditAction.DND_RECORDED,
        business_id=business_id,
        actor="api",
        entity_type="dnd_entry",
        entity_id=entry.id,
        decision="block",
        reason_code="dnd_active",
        reason=payload.reason or "the number was added to the do-not-disturb registry",
        metadata={"phone": phone, "scope": payload.scope, "source": payload.source},
    )
    db.commit()
    return dnd_payload(entry)


@router.delete("/dnd/{entry_id}", status_code=status.HTTP_200_OK)
async def deactivate_dnd_entry(
    entry_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Deactivate a do-not-disturb entry.

    The entry is kept (not deleted) so the history of the suppression
    stays auditable.
    """
    entries = repo.list_dnd_entries(db, business_id)
    entry = next((item for item in entries if item.id == entry_id), None)
    if entry is None:
        from app.modules.voice.errors import DndEntryNotFoundError

        raise DndEntryNotFoundError(entry_id)
    repo.deactivate_dnd_entry(db, entry)
    repo.add_audit_log(
        db,
        action=AuditAction.DND_RECORDED,
        business_id=business_id,
        actor="api",
        entity_type="dnd_entry",
        entity_id=entry.id,
        decision="allow",
        reason_code="dnd_deactivated",
        reason="the do-not-disturb entry was deactivated",
        metadata={"phone": entry.phone},
    )
    db.commit()
    return dnd_payload(entry)


__all__ = ["router", "VoiceLeadProfile"]
