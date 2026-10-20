"""Repository for querying agent-facing lead data.

All queries enforce business-level data isolation — callers must supply
the ``business_id`` and results are always scoped to that business.
"""

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import AgentLead


def get_agent_lead(session: Session, lead_id: str) -> Optional[AgentLead]:
    """Retrieve one agent-queryable lead by Callora lead ID."""
    return session.execute(
        select(AgentLead).where(AgentLead.lead_id == lead_id)
    ).scalar_one_or_none()


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
