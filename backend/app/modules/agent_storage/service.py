"""Service for upserting agent lead records."""

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import AgentLead


def upsert_agent_lead(
    session: Session,
    lead_id: str,
    business_id: str,
    name: str,
    phone: str,
    source: str,
    email: Optional[str] = None,
    language: Optional[str] = None,
    deal_value: Optional[Any] = None,
    last_contact_at: Optional[Any] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    external_crm_id: Optional[str] = None,
    status: str = "New",
    created_at: Optional[Any] = None,
) -> AgentLead:
    """Insert or update an agent_leads record keyed on lead_id.

    Uses an upsert so repeated successful synchronizations do not create
    duplicate agent records. ``created_at`` is the canonical lead's
    creation time (needed by Module 3's opportunity detection); when it
    is omitted the projection stamp is used.
    """
    existing = session.get(AgentLead, lead_id)

    if existing is None:
        record = AgentLead(
            lead_id=lead_id,
            business_id=business_id,
            name=name,
            phone=phone,
            email=email,
            source=source,
            status=status,
            external_crm_id=external_crm_id,
            language=language,
            deal_value=deal_value,
            last_contact_at=last_contact_at,
            custom_fields=custom_fields or {},
            **({"created_at": created_at} if created_at is not None else {}),
        )
        session.add(record)
        return record

    existing.business_id = business_id
    existing.name = name
    existing.phone = phone
    existing.email = email
    existing.source = source
    existing.status = status
    existing.external_crm_id = external_crm_id
    existing.language = language
    existing.deal_value = deal_value
    existing.last_contact_at = last_contact_at
    existing.custom_fields = custom_fields or {}
    if created_at is not None:
        existing.created_at = created_at
    return existing
