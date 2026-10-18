
"""HTTP client for external CRM APIs."""

import httpx

from app.config import get_settings


class CRMClientError(Exception):
    """Raised when an external CRM request fails."""


class CRMClient:
    def __init__(self):
        settings = get_settings()

        if not settings.crm_base_url:
            raise CRMClientError("CRM base URL is not configured.")

        headers = {"Accept": "application/json"}

        if settings.crm_api_key:
            headers["Authorization"] = f"Bearer {settings.crm_api_key}"

        self.client = httpx.AsyncClient(
            base_url=settings.crm_base_url.rstrip("/"),
            headers=headers,
            timeout=settings.crm_timeout_seconds,
        )

    async def create_contact(self, lead_data: dict) -> dict:
        """Create a contact in the external CRM."""
        try:
            response = await self.client.post(
                "/contacts",
                json=lead_data,
            )
            response.raise_for_status()

            result = response.json()

            if not isinstance(result, dict) or not result.get("id"):
                raise CRMClientError(
                    "CRM response must contain an external record ID."
                )

            return result

        except httpx.HTTPStatusError as exc:
            raise CRMClientError(
                f"External CRM returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.RequestError as exc:
            raise CRMClientError(
                f"Could not reach external CRM: {exc}"
            ) from exc

    async def update_contact(
        self,
        external_crm_id: str,
        lead_data: dict,
    ) -> dict:
        """Update an existing contact in the external CRM."""
        try:
            response = await self.client.patch(
                f"/contacts/{external_crm_id}",
                json=lead_data,
            )
            response.raise_for_status()

            result = response.json()

            if not isinstance(result, dict):
                raise CRMClientError(
                    "CRM response must be a JSON object."
                )

            return {"id": result.get("id", external_crm_id)}

        except httpx.HTTPStatusError as exc:
            raise CRMClientError(
                f"External CRM returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.RequestError as exc:
            raise CRMClientError(
                f"Could not reach external CRM: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
