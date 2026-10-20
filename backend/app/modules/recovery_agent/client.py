"""Data fetching — GET /api/v1/agent/leads with X-API-Key authentication.

The Recovery Agent consumes the same documented API contract external
agents use (pagination envelope, business API key). When
``CALLORA_RECOVERY_API_BASE_URL`` is unset the request is dispatched
in-process through an ASGI transport — the identical route, auth and
serialization code paths, without a network hop or a hardcoded URL.

Failure mapping (the sweep never runs on partial data):

===============================  =========================================
Failure                          Error
===============================  =========================================
Connect/transport error, 5xx     ``recovery_leads_unavailable``   (503)
Timeout                          ``recovery_leads_timeout``       (504)
Bad JSON / contract violation    ``recovery_leads_invalid_response`` (502)
===============================  =========================================
"""

from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings
from app.errors import (
    RecoveryLeadsInvalidResponseError,
    RecoveryLeadsTimeoutError,
    RecoveryLeadsUnavailableError,
)

AGENT_LEADS_PATH = "/api/v1/agent/leads"


class AgentLeadsClient:
    """Async client for the agent-ready leads endpoint."""

    def __init__(self, settings: Settings, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._settings = settings
        self._api_key = api_key
        if transport is None and settings.recovery_api_base_url:
            transport = httpx.AsyncHTTPTransport(retries=0)
        if transport is None:
            # In-process dispatch through the exact same ASGI app.
            from app.main import app  # imported lazily to avoid a cycle

            transport = httpx.ASGITransport(app=app)
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url=settings.recovery_api_base_url or "http://callora.in-process",
            timeout=settings.recovery_fetch_timeout_seconds,
            headers={"X-API-Key": api_key, "accept": "*/*"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_leads(self) -> List[Dict[str, Any]]:
        """Fetch every agent-ready lead for the authenticated business.

        Paginates until a short page; all-or-nothing on failure (a
        partial sweep would produce wrong recommendations).
        """
        page_size = self._settings.recovery_fetch_page_size
        leads: List[Dict[str, Any]] = []
        offset = 0
        try:
            while True:
                response = await self._client.get(
                    AGENT_LEADS_PATH, params={"limit": page_size, "offset": offset}
                )
                self._raise_for_status(response)
                payload = self._parse_payload(response)
                items = payload["items"]
                leads.extend(items)
                if len(items) < page_size:
                    return leads
                offset += page_size
        except httpx.TimeoutException as exc:
            raise RecoveryLeadsTimeoutError(
                f"The agent-leads API did not respond within "
                f"{self._settings.recovery_fetch_timeout_seconds:.0f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise RecoveryLeadsUnavailableError(
                f"Could not reach the agent-leads API: {exc}"
            ) from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise RecoveryLeadsUnavailableError(
                f"The agent-leads API returned HTTP {response.status_code}",
                details=[{"status_code": response.status_code}],
            )

    @staticmethod
    def _parse_payload(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RecoveryLeadsInvalidResponseError(
                "The agent-leads API returned a non-JSON body"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RecoveryLeadsInvalidResponseError(
                "The agent-leads API response does not match the contract "
                "(expected an object with an 'items' list)"
            )
        if not all(isinstance(item, dict) for item in payload["items"]):
            raise RecoveryLeadsInvalidResponseError(
                "The agent-leads API returned a non-object entry in 'items'"
            )
        return payload


def build_agent_leads_client(settings: Settings, api_key: str) -> AgentLeadsClient:
    return AgentLeadsClient(settings, api_key)
