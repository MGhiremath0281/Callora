"""HTTP client for external CRM APIs."""

from typing import Optional

import httpx

from app.config import get_settings
from app.modules.crm_sync.destination_guard import (
    DestinationNotAllowedError,
    ensure_destination_allowed,
)


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

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

        headers = {"Accept": "application/json"}

        if api_key:
            # The external CRM authenticates with its OWN x-api-key header.
            # This is a separate layer from Callora's inbound X-API-Key
            # authentication; the two keys are never interchangeable.
            headers["x-api-key"] = api_key

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            # Never follow redirects: a redirect could point at an internal
            # address that bypassed the destination checks for the base URL.
            follow_redirects=False,
        )

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Send a request after re-validating the destination.

        The check runs on every request (not only when a connection is
        saved) so DNS-based tricks cannot bypass the SSRF guard. Redirects
        are disabled in the transport.
        """
        try:
            await ensure_destination_allowed(self.base_url, get_settings())
        except DestinationNotAllowedError as exc:
            raise CRMClientError(str(exc)) from exc
        except OSError as exc:
            raise CRMUnavailableError(
                f"Could not resolve external CRM host: {exc}"
            ) from exc
        return await self.client.request(method, url, **kwargs)

    def _redact(self, text: str) -> str:
        """Ensure the stored external API key never leaks into messages."""
        if self.api_key and self.api_key in text:
            return text.replace(self.api_key, "***")
        return text

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map HTTP failures to typed client errors (key-redacted)."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise CRMAuthError(
                    f"CRM authentication failed (HTTP {exc.response.status_code})"
                ) from exc
            if 300 <= exc.response.status_code < 400:
                raise CRMClientError(
                    f"External CRM returned HTTP {exc.response.status_code} "
                    "redirect; redirects are not followed."
                ) from exc
            if exc.response.status_code >= 500:
                raise CRMUnavailableError(
                    f"CRM returned server error HTTP {exc.response.status_code}: "
                    f"{self._redact(exc.response.text[:500])}"
                ) from exc
            raise CRMClientError(
                f"External CRM returned HTTP {exc.response.status_code}: "
                f"{self._redact(exc.response.text[:500])}"
            ) from exc

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
        """Create a contact via UrbanNestCRM ``POST /contacts``."""
        try:
            response = await self._request(
                "POST",
                "/contacts",
                json=lead_data,
            )
            self._raise_for_status(response)

            result = response.json()

            if not isinstance(result, dict) or not result.get("id"):
                raise CRMClientError(
                    "CRM response must contain an external record ID."
                )

            return result

        except httpx.TimeoutException as exc:
            raise CRMTimeoutError("CRM request timed out") from exc

        except httpx.RequestError as exc:
            raise CRMUnavailableError(
                f"Could not reach external CRM: {exc}"
            ) from exc

        except ValueError as exc:
            raise CRMClientError(
                "CRM returned a response body that is not valid JSON."
            ) from exc

    async def update_contact(
        self,
        external_crm_id: str,
        lead_data: dict,
    ) -> dict:
        """Update a contact via UrbanNestCRM ``PATCH /contacts/{contact_id}``."""
        try:
            response = await self._request(
                "PATCH",
                f"/contacts/{external_crm_id}",
                json=lead_data,
            )
            self._raise_for_status(response)

            result = response.json()

            if not isinstance(result, dict):
                raise CRMClientError(
                    "CRM response must be a JSON object."
                )

            return {"id": result.get("id", external_crm_id)}

        except httpx.TimeoutException as exc:
            raise CRMTimeoutError("CRM request timed out") from exc

        except httpx.RequestError as exc:
            raise CRMUnavailableError(
                f"Could not reach external CRM: {exc}"
            ) from exc

        except ValueError as exc:
            raise CRMClientError(
                "CRM returned a response body that is not valid JSON."
            ) from exc

    async def get_health(self) -> dict:
        """Check connectivity/auth via the CRM's ``GET /health`` endpoint.

        UrbanNestCRM documents ``GET /health``; it is used to validate a
        connection before it is saved. Non-JSON health bodies are
        tolerated — only reachability and authentication matter here.
        """
        try:
            response = await self._request("GET", "/health")
            self._raise_for_status(response)
        except httpx.TimeoutException as exc:
            raise CRMTimeoutError("CRM request timed out") from exc
        except httpx.RequestError as exc:
            raise CRMUnavailableError(
                f"Could not reach external CRM: {exc}"
            ) from exc

        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
