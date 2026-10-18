
"""Business logic for synchronizing Callora leads with an external CRM."""

from uuid import uuid4
from typing import Any

from app.modules.crm_sync.client import CRMClient, CRMClientError
from app.modules.crm_sync.schemas import CRMSyncRequest


class CRMSyncService:
    """Coordinates lead synchronization with an external CRM."""

    def __init__(self, client: CRMClient):
        self.client = client

        # Temporary development mapping.
        # This will be replaced with persistent database storage later.
        self._external_ids: dict[str, str] = {}

    @staticmethod
    def _to_crm_payload(lead: Any) -> dict:
        """Convert Callora's lead fields to UrbanNestCRM's API format."""

        custom_fields = lead.custom_fields or {}

        payload = {
            "full_name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "lead_source": lead.source,
            "last_contact_at": (
                lead.last_contact_at.isoformat()
                if lead.last_contact_at
                else None
            ),
        }

        # Map optional UrbanNestCRM fields from imported custom fields.
        for field in (
            "property_type",
            "preferred_city",
            "budget_min",
            "budget_max",
        ):
            value = custom_fields.get(field)
            if value is not None:
                payload[field] = value

        if not payload["full_name"] or not payload["phone"]:
            raise ValueError("Lead must contain both name and phone.")

        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }

    async def sync_lead(self, request: CRMSyncRequest) -> dict:
        """Create or update a lead in the external CRM."""

        lead = request.lead

        mapping_key = (
            f"{request.connection_id}:"
            f"{lead.business_id}:"
            f"{lead.id}"
        )

        payload = self._to_crm_payload(lead)
        external_crm_id = self._external_ids.get(mapping_key)

        try:
            if external_crm_id:
                result = await self.client.update_contact(
                    external_crm_id=external_crm_id,
                    lead_data=payload,
                )
                external_crm_id = result["id"]
                sync_status = "updated"
                message = "Lead updated in the external CRM."

            else:
                result = await self.client.create_contact(payload)
                external_crm_id = result["id"]
                self._external_ids[mapping_key] = external_crm_id
                sync_status = "created"
                message = "Lead created in the external CRM."

            return {
                "sync_id": str(uuid4()),
                "lead_id": lead.id,
                "business_id": lead.business_id,
                "status": sync_status,
                "external_crm_id": external_crm_id,
                "message": message,
            }

        except CRMClientError:
            raise
