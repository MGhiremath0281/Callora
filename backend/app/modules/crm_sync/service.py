"""Business logic for synchronizing Callora leads with an external CRM.

The synchronization service:
- resolves the CRM connection from the database
- verifies lead ownership
- enforces idempotency at the database level
- calls the external CRM adapter
- persists the sync record and updates the agent_leads projection
- returns a structured response

A failed CRM sync never deletes or invalidates the canonical lead.
"""

import hashlib
import json
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    CRMConnectionInactiveError,
    CRMConnectionNotFoundError,
    CRMSyncError,
    LeadNotFoundError,
)
from app.models import (
    CRMSyncIdempotency,
    CRMSyncRecord,
    CRMSyncStatus,
    CRMConnection,
    Lead,
)
from app.modules.agent_storage.service import upsert_agent_lead
from app.modules.crm_sync.client import (
    CRMAuthError,
    CRMClient,
    CRMClientError,
    CRMTimeoutError,
    CRMUnavailableError,
)
from app.modules.crm_sync.schemas import CRMSyncRequest, CRMSyncResponse


def _compute_payload_hash(lead: Any) -> str:
    """Compute a stable hash of the canonical lead payload.

    Used to detect idempotency-key reuse with a different payload.
    """
    canonical = json.dumps(
        {
            "id": lead.id,
            "business_id": lead.business_id,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "source": lead.source,
            "language": lead.language,
            "deal_value": str(lead.deal_value) if lead.deal_value is not None else None,
            "last_contact_at": (
                lead.last_contact_at.isoformat()
                if lead.last_contact_at
                else None
            ),
            "custom_fields": lead.custom_fields,
            "external_id": lead.external_id,
            "created_at": lead.created_at.isoformat(),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CRMSyncService:
    """Coordinates lead synchronization with an external CRM."""

    def __init__(self, session: Session):
        self.session = session

    def _resolve_connection(
        self, business_id: str, connection_id: str
    ) -> CRMConnection:
        """Resolve and validate a CRM connection."""
        connection = self.session.execute(
            select(CRMConnection).where(
                CRMConnection.business_id == business_id,
                CRMConnection.connection_id == connection_id,
            )
        ).scalar_one_or_none()

        if connection is None:
            raise CRMConnectionNotFoundError(connection_id)
        if not connection.is_active:
            raise CRMConnectionInactiveError(connection_id)
        return connection

    def _verify_lead(self, business_id: str, lead_id: str) -> Lead:
        """Verify the lead exists and belongs to the business."""
        lead = self.session.get(Lead, lead_id)
        if lead is None or lead.business_id != business_id:
            raise LeadNotFoundError(lead_id)
        return lead

    def _check_idempotency(
        self,
        business_id: str,
        connection_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> Optional[CRMSyncIdempotency]:
        """Check for an existing idempotency record.

        Returns the existing record if found, None otherwise.
        Raises CRMIdempotencyConflictError if the key was used with a
        different payload.
        """
        existing = self.session.execute(
            select(CRMSyncIdempotency).where(
                CRMSyncIdempotency.business_id == business_id,
                CRMSyncIdempotency.connection_id == connection_id,
                CRMSyncIdempotency.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

        if existing is None:
            return None

        if existing.payload_hash != payload_hash:
            from app.errors import CRMIdempotencyConflictError

            raise CRMIdempotencyConflictError(idempotency_key)

        return existing

    def _create_idempotency_record(
        self,
        business_id: str,
        connection_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> None:
        """Create an idempotency record (DB-enforced uniqueness)."""
        record = CRMSyncIdempotency(
            business_id=business_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            status="processing",
        )
        self.session.add(record)
        try:
            self.session.flush()
        except IntegrityError:
            # Concurrent request with the same key — let it proceed
            self.session.rollback()
            from app.errors import CRMIdempotencyConflictError

            raise CRMIdempotencyConflictError(idempotency_key)

    def _store_idempotency_result(
        self,
        business_id: str,
        connection_id: str,
        idempotency_key: str,
        payload_hash: str,
        response_data: dict,
    ) -> None:
        """Store the successful result for idempotent replay."""
        existing = self.session.execute(
            select(CRMSyncIdempotency).where(
                CRMSyncIdempotency.business_id == business_id,
                CRMSyncIdempotency.connection_id == connection_id,
                CRMSyncIdempotency.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

        if existing and existing.payload_hash == payload_hash:
            existing.response_data = response_data
            existing.status = "completed"

    @staticmethod
    def _to_crm_payload(lead: Any) -> dict:
        """Convert Callora's lead fields to the external CRM's API format."""
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

        # Map optional CRM fields from imported custom fields.
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

    async def sync_lead(self, request: CRMSyncRequest) -> CRMSyncResponse:
        """Synchronize one lead with the external CRM.

        This method is atomic with respect to local state: the idempotency
        record, sync record, and agent_leads projection are all updated in
        the same transaction.
        """
        lead = request.lead
        business_id = lead.business_id

        # 1. Verify lead exists and belongs to the business.
        canonical_lead = self._verify_lead(business_id, lead.id)

        # 2. Resolve the CRM connection.
        connection = self._resolve_connection(business_id, request.connection_id)

        # 3. Compute payload hash for idempotency.
        payload_hash = _compute_payload_hash(lead)

        # 4. Check idempotency.
        if request.idempotency_key:
            existing_idem = self._check_idempotency(
                business_id, request.connection_id, request.idempotency_key, payload_hash
            )
            if existing_idem and existing_idem.status == "completed":
                # Replay the stored result.
                data = existing_idem.response_data
                return CRMSyncResponse(
                    sync_id=data.get("sync_id", str(uuid4())),
                    lead_id=lead.id,
                    business_id=business_id,
                    status=data.get("status", "created"),
                    external_crm_id=data.get("external_crm_id"),
                    message=data.get("message", "Lead created in the external CRM."),
                )
        else:
            existing_idem = None

        # 5. Create idempotency record.
        if request.idempotency_key:
            self._create_idempotency_record(
                business_id, request.connection_id, request.idempotency_key, payload_hash
            )

        # 6. Build CRM payload and call the external CRM.
        payload = self._to_crm_payload(lead)

        client = CRMClient.from_connection(connection)
        try:
            # Check if we already have an external ID for this lead.
            external_crm_id = canonical_lead.external_id

            if external_crm_id:
                result = await client.update_contact(
                    external_crm_id=external_crm_id,
                    lead_data=payload,
                )
                sync_status = "updated"
                message = "Lead updated in the external CRM."
            else:
                result = await client.create_contact(payload)
                external_crm_id = result["id"]
                sync_status = "created"
                message = "Lead created in the external CRM."

        except CRMAuthError as exc:
            self._record_sync_failure(
                lead.id, business_id, request.connection_id,
                request.idempotency_key, str(exc),
            )
            raise CRMSyncError(
                f"CRM authentication failed: {exc}",
                details=[{"connection_id": request.connection_id}],
            ) from exc

        except CRMTimeoutError as exc:
            self._record_sync_failure(
                lead.id, business_id, request.connection_id,
                request.idempotency_key, str(exc),
            )
            raise CRMSyncError(
                f"CRM request timed out: {exc}",
                details=[{"connection_id": request.connection_id}],
            ) from exc

        except CRMUnavailableError as exc:
            self._record_sync_failure(
                lead.id, business_id, request.connection_id,
                request.idempotency_key, str(exc),
            )
            raise CRMSyncError(
                f"CRM unavailable: {exc}",
                details=[{"connection_id": request.connection_id}],
            ) from exc

        except CRMClientError as exc:
            self._record_sync_failure(
                lead.id, business_id, request.connection_id,
                request.idempotency_key, str(exc),
            )
            raise CRMSyncError(
                f"CRM sync failed: {exc}",
                details=[{"connection_id": request.connection_id}],
            ) from exc

        finally:
            await client.close()

        # 7. Persist the sync record and update agent storage.
        sync_id = str(uuid4())
        sync_record = CRMSyncRecord(
            id=sync_id,
            lead_id=lead.id,
            business_id=business_id,
            connection_id=request.connection_id,
            idempotency_key=request.idempotency_key,
            status=CRMSyncStatus.CREATED if sync_status == "created" else CRMSyncStatus.UPDATED,
            external_crm_id=external_crm_id,
        )
        self.session.add(sync_record)

        # Update the canonical lead's external_id.
        canonical_lead.external_id = external_crm_id

        # Upsert the agent_leads projection.
        upsert_agent_lead(
            self.session,
            lead_id=lead.id,
            business_id=business_id,
            name=lead.name,
            phone=lead.phone,
            source=lead.source,
            email=lead.email,
            language=lead.language,
            deal_value=lead.deal_value,
            last_contact_at=lead.last_contact_at,
            custom_fields=lead.custom_fields,
            external_crm_id=external_crm_id,
        )

        # Store idempotency result.
        if request.idempotency_key:
            self._store_idempotency_result(
                business_id,
                request.connection_id,
                request.idempotency_key,
                payload_hash,
                {
                    "sync_id": sync_id,
                    "status": sync_status,
                    "external_crm_id": external_crm_id,
                    "message": message,
                },
            )

        self.session.commit()

        return CRMSyncResponse(
            sync_id=sync_id,
            lead_id=lead.id,
            business_id=business_id,
            status=sync_status,
            external_crm_id=external_crm_id,
            message=message,
        )

    def _record_sync_failure(
        self,
        lead_id: str,
        business_id: str,
        connection_id: str,
        idempotency_key: Optional[str],
        error_message: str,
    ) -> None:
        """Persist a failed sync attempt without invalidating the lead."""
        try:
            sync_record = CRMSyncRecord(
                lead_id=lead_id,
                business_id=business_id,
                connection_id=connection_id,
                idempotency_key=idempotency_key,
                status=CRMSyncStatus.FAILED,
                error_message=error_message,
            )
            self.session.add(sync_record)
            self.session.commit()
        except Exception:
            self.session.rollback()
