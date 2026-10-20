"""Repository for querying agent-facing lead data.

All queries enforce business-level data isolation — callers must supply
the ``business_id`` and results are always scoped to that business.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import AgentLead


def get_agent_lead(session: Session, lead_id: str) -> Optional[AgentLead]:
    """Retrieve one agent-queryable lead by Callora lead ID."""
    return session.execute(
        select(AgentLead).where(AgentLead.lead_id == lead_id)
    ).scalar_one_or_none()


def get_agent_lead_scoped(
    session: Session, business_id: str, lead_id: str
) -> Optional[AgentLead]:
    """Retrieve one agent-queryable lead, scoped to the business.

    The recovery dashboard uses this for lead details of a recovery
    case: a case never exposes another business's lead, even if a lead
    id were crafted.
    """
    return session.execute(
        select(AgentLead).where(
            AgentLead.business_id == business_id,
            AgentLead.lead_id == lead_id,
        )
    ).scalar_one_or_none()


def lead_overview(record: Optional[AgentLead]) -> Optional[Dict[str, Any]]:
    """JSON-safe verified-contact overview of a lead.

    Only fields that actually exist on the canonical agent-lead
    projection are returned — nothing is inferred. A missing record
    yields ``None`` (callers must never synthesize a lead).
    """
    if record is None:
        return None
    return {
        "lead_id": record.lead_id,
        "name": record.name,
        "phone": record.phone,
        "email": record.email,
        "source": record.source,
        "status": record.status,
        "language": record.language,
        "deal_value": (
            float(record.deal_value) if record.deal_value is not None else None
        ),
        "last_contact_at": (
            record.last_contact_at.isoformat() if record.last_contact_at else None
        ),
        "external_crm_id": record.external_crm_id,
        "custom_fields": record.custom_fields or {},
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def list_agent_leads(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[AgentLead]:
    """List leads for a business with optional status filtering."""
    stmt = select(AgentLead).where(AgentLead.business_id == business_id)
    if status is not None:
        stmt = stmt.where(AgentLead.status == status)
    stmt = stmt.order_by(AgentLead.updated_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def get_agent_lead_by_external_id(
    session: Session,
    business_id: str,
    external_crm_id: str,
) -> Optional[AgentLead]:
    """Retrieve a lead by its external CRM ID, scoped to the business."""
    return session.execute(
        select(AgentLead).where(
            AgentLead.business_id == business_id,
            AgentLead.external_crm_id == external_crm_id,
        )
    ).scalar_one_or_none()


def count_agent_leads(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
) -> int:
    """Count agent leads for a business, optionally filtered by status."""
    stmt = select(func.count()).select_from(AgentLead).where(
        AgentLead.business_id == business_id
    )
    if status is not None:
        stmt = stmt.where(AgentLead.status == status)
    return session.execute(stmt).scalar_one()
