"""Seed CRM connections from configuration.

On startup, if a default CRM base URL is configured, a default connection
is created for each business that doesn't already have one. This allows
the system to work out of the box with docker-compose.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CRMConnection


def seed_crm_connections(session: Session) -> None:
    """Seed default CRM connections from global config."""
    settings = get_settings()

    if not settings.crm_base_url:
        return

    api_key_map = settings.api_key_map

    for business_id in api_key_map:
        # Check if this business already has any connection.
        existing = session.execute(
            select(CRMConnection).where(CRMConnection.business_id == business_id)
        ).scalars().first()

        if existing is None:
            connection = CRMConnection(
                business_id=business_id,
                connection_id="default",
                name="Default CRM Connection",
                is_active=True,
                crm_base_url=settings.crm_base_url,
                crm_api_key=settings.crm_api_key,
                crm_timeout_seconds=settings.crm_timeout_seconds,
            )
            session.add(connection)

    session.commit()
