"""HTTP client for external CRM APIs."""

from typing import Optional

import httpx

from app.config import get_settings


class CRMClientError(Exception):
    """Raised when an external CRM request fails."""


class CRMAuthError(CRMClientError):
    """Raised when the CRM rejects our credentials (401/403)."""


class CRMTimeoutError(CRMClientError):
    """Raised when the CRM does not respond in time."""


class CRMUnavailableError(CRMClientError):
    """Raised when the CRM is unreachable (5xx, connection error)."""


class CRMClient:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
    ):
        if not base_url:
            raise CRMClientError("CRM base URL is not configured.")

        headers = {"Accept": "application/json"}

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls) -> "CRMClient":
        """Build a client from the global development settings."""
        settings = get_settings()
        return cls(
            base_url=settings.crm_base_url or "",
            api_key=settings.crm_api_key,
            timeout=settings.crm_timeout_seconds,
        )

    @classmethod
    def from_connection(cls, connection) -> "CRMClient":
        """Build a client from a CRMConnection model instance."""
        return cls(
            base_url=connection.crm_base_url,
            api_key=connection.crm_api_key,
            timeout=connection.crm_timeout_seconds,
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
            if exc.response.status_code in (401, 403):
                raise CRMAuthError(
                    f"CRM authentication failed (HTTP {exc.response.status_code})"
                ) from exc
            if exc.response.status_code >= 500:
                raise CRMUnavailableError(
                    f"CRM returned server error HTTP {exc.response.status_code}: "
                    f"{exc.response.text[:500]}"
                ) from exc
            raise CRMClientError(
                f"External CRM returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.TimeoutException as exc:
            raise CRMTimeoutError("CRM request timed out") from exc

        except httpx.RequestError as exc:
            raise CRMUnavailableError(
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
            if exc.response.status_code in (401, 403):
                raise CRMAuthError(
                    f"CRM authentication failed (HTTP {exc.response.status_code})"
                ) from exc
            if exc.response.status_code >= 500:
                raise CRMUnavailableError(
                    f"CRM returned server error HTTP {exc.response.status_code}: "
                    f"{exc.response.text[:500]}"
                ) from exc
            raise CRMClientError(
                f"External CRM returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.TimeoutException as exc:
            raise CRMTimeoutError("CRM request timed out") from exc

        except httpx.RequestError as exc:
            raise CRMUnavailableError(
                f"Could not reach external CRM: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
