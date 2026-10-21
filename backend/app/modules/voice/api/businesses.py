"""Business voice configuration endpoints.

    GET   /api/v1/voice/business   read the voice configuration
    PATCH /api/v1/voice/business   change the voice configuration

The configuration is the policy the compliance gate evaluates: calling
window, allowed weekdays, daily quotas, consent requirement, DND screen,
the voice agent to use and the qualification questions the agent asks.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import current_business
from app.modules.voice.api.serializers import business_payload
from app.modules.voice.constants import AuditAction
from app.modules.voice.schemas import BusinessProfile, BusinessProfileUpdate

router = APIRouter(
    prefix="/voice/business",
    tags=["Voice — Business"],
)


def _load_business(session: Session, business_id: str, settings: Settings):
    """Load (or create) the voice configuration of the caller."""
    business = repo.get_or_create_business(
        session,
        business_id,
        timezone=settings.voice_default_timezone,
        default_language="en",
        supported_languages=sorted(settings.voice_supported_language_set),
        calling_window_start=settings.voice_default_calling_window_start,
        calling_window_end=settings.voice_default_calling_window_end,
        max_calls_per_lead_per_day=settings.voice_max_calls_per_lead_per_day,
        max_calls_per_business_per_day=settings.voice_max_calls_per_business_per_day,
        max_concurrent_calls=settings.voice_max_concurrent_calls,
    )
    if business.supported_languages is None:
        business.supported_languages = sorted(settings.voice_supported_language_set)
    return business


@router.get("", response_model=BusinessProfile, status_code=status.HTTP_200_OK)
async def read_business(
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Read the voice configuration of the authenticated business.

    The configuration row is created on first read with the deployment
    defaults, so a business that is already addressable through the API
    key does not need a separate provisioning step.
    """
    business = _load_business(db, business_id, settings)
    db.commit()
    return business_payload(business, settings)


@router.patch("", response_model=BusinessProfile, status_code=status.HTTP_200_OK)
async def update_business(
    payload: BusinessProfileUpdate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Change the voice configuration of the authenticated business.

    Only the supplied fields are changed; everything else keeps its
    current value. The change is audited so the policy in force at any
    point in time can be reconstructed.
    """
    business = _load_business(db, business_id, settings)
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if values:
        repo.update_business(db, business, values)
        repo.add_audit_log(
            db,
            action=AuditAction.BUSINESS_CONFIG_UPDATED,
            business_id=business_id,
            actor="api",
            entity_type="business",
            entity_id=business.id,
            reason_code="business_config_updated",
            reason="the voice configuration was updated",
            metadata={"changed_fields": sorted(values.keys())},
        )
    db.commit()
    return business_payload(business, settings)
