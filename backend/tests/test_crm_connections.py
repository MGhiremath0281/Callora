"""Tests for external CRM connection configuration and validation.

Covers:
- Saving a connection with a business-supplied URL and x-api-key
- Real connectivity/authentication validation via GET /health (mocked HTTP)
- Credential protection (key never returned, never in errors)
- Update-in-place without duplicate records
- Business scoping and Callora authentication separation
- SSRF/URL validation
- POST /api/v1/crm/sync using the STORED connection credentials with the
  external CRM's x-api-key header (no key in the sync request)
"""

import asyncio
import ipaddress
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_session_factory, reset_engine
from app.main import app
from app.models import CRMConnection, Lead

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"

EXT_KEY = "ext-crm-secret-123"
DEV_CRM_URL = "http://host.docker.internal:9000"
DEV_ALLOWLIST_ENV = "CALLORA_CRM_DEV_ALLOWED_DESTINATIONS"
SAVE_BODY = {
    "connection_id": "urban-1",
    "name": "UrbanNest CRM",
    "crm_base_url": "https://crm.example.com",
    "crm_x_api_key": EXT_KEY,
    "crm_timeout_seconds": 5.0,
}


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Deterministic DNS for mocked clients; individual tests override it."""

    async def _resolve(host: str, port: int):
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(
        "app.modules.crm_sync.destination_guard.resolve_host_ips", _resolve
    )


@pytest.fixture()
def allow_dev_crm(monkeypatch, settings):
    """Enable the explicit development allowlist for the local fake CRM."""
    monkeypatch.setenv(DEV_ALLOWLIST_ENV, "host.docker.internal:9000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def auth_headers(business: str = BIZ_1) -> dict:
    return {"X-API-Key": {BIZ_1: KEY_1, BIZ_2: KEY_2}[business]}


def _get_client():
    from app.db.session import get_db

    def _override_get_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


@pytest.fixture()
def conn_client(settings):
    """Fresh database + test client for connection tests."""
    Base.metadata.create_all(get_engine())
    client = _get_client()
    yield client
    app.dependency_overrides.clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())


def _transport_client(handler, connection) -> "app.modules.crm_sync.client.CRMClient":
    """A REAL CRMClient whose HTTP layer is served by a mock transport."""
    from app.modules.crm_sync.client import CRMClient

    client = CRMClient(
        base_url=connection.crm_base_url,
        api_key=connection.crm_api_key,
        timeout=connection.crm_timeout_seconds,
    )
    client.client = httpx.AsyncClient(
        base_url=connection.crm_base_url,
        headers={
            "Accept": "application/json",
            "x-api-key": connection.crm_api_key or "",
        },
        transport=httpx.MockTransport(handler),
        timeout=connection.crm_timeout_seconds,
        follow_redirects=False,
    )
    return client


def _patched(handler):
    """Patch the service's client factory with the transport-backed client."""
    return patch(
        "app.modules.crm_sync.service.CRMClient.from_connection",
        side_effect=lambda connection: _transport_client(handler, connection),
    )


def _ok_health_handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.url.path == "/health"
    # The external CRM key (whatever is stored on the connection) is sent,
    # never Callora's inbound key.
    assert request.headers.get("x-api-key")
    return httpx.Response(200, json={"status": "ok"})


# ---------------------------------------------------------------------------
# Connection configuration and validation
# ---------------------------------------------------------------------------


class TestConnectionConfiguration:
    def test_save_connection_validates_and_persists(self, conn_client):
        """POST /api/v1/crm/connections validates then saves the connection."""
        with _patched(_ok_health_handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["verified"] is True
        assert data["connection"]["connection_id"] == "urban-1"
        assert data["connection"]["name"] == "UrbanNest CRM"
        assert data["connection"]["crm_base_url"] == "https://crm.example.com"
        assert data["connection"]["has_api_key"] is True
        assert data["connection"]["is_active"] is True

        session = get_session_factory()()
        stored = session.query(CRMConnection).one()
        assert stored.crm_api_key == EXT_KEY
        session.close()

    def test_connection_api_key_never_exposed(self, conn_client):
        """The stored external key never appears in any response body."""
        with _patched(_ok_health_handler):
            save = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
        assert save.status_code == 200
        assert EXT_KEY not in save.text
        assert "crm_x_api_key" not in save.text

        listing = conn_client.get("/api/v1/crm/connections", headers=auth_headers())
        assert listing.status_code == 200
        assert EXT_KEY not in listing.text
        assert "crm_api_key" not in listing.text
        assert listing.json()[0]["has_api_key"] is True

    def test_save_requires_callora_auth(self, conn_client):
        """Callora's inbound X-API-Key gates connection configuration."""
        assert (
            conn_client.post("/api/v1/crm/connections", json=SAVE_BODY).status_code
            == 401
        )
        resp = conn_client.post(
            "/api/v1/crm/connections",
            json=SAVE_BODY,
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_update_existing_connection_without_duplicates(self, conn_client):
        """Same connection_id updates the row in place (no duplicates)."""
        with _patched(_ok_health_handler):
            first = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
            updated_body = {
                **SAVE_BODY,
                "name": "UrbanNest CRM v2",
                "crm_base_url": "https://crm2.example.com",
                "crm_x_api_key": "rotated-key-456",
            }
            second = conn_client.post(
                "/api/v1/crm/connections", json=updated_body, headers=auth_headers()
            )

        assert first.status_code == 200
        assert second.status_code == 200, second.text
        assert second.json()["connection"]["name"] == "UrbanNest CRM v2"
        assert second.json()["connection"]["crm_base_url"] == "https://crm2.example.com"

        session = get_session_factory()()
        rows = session.query(CRMConnection).all()
        assert len(rows) == 1
        assert rows[0].crm_api_key == "rotated-key-456"
        session.close()

    def test_connections_are_business_scoped(self, conn_client):
        """Each business only sees and manages its own connection."""
        with _patched(_ok_health_handler):
            conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )

        listing = conn_client.get(
            "/api/v1/crm/connections", headers=auth_headers(BIZ_2)
        )
        assert listing.status_code == 200
        assert listing.json() == []

    def test_validation_failure_is_not_saved(self, conn_client):
        """A connection that fails validation is never persisted."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )

        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "crm_authentication_failed"
        session = get_session_factory()()
        assert session.query(CRMConnection).count() == 0
        session.close()

    def test_validation_failure_does_not_clobber_existing(self, conn_client):
        """A failed re-validation leaves the stored connection untouched."""
        with _patched(_ok_health_handler):
            conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("All connection attempts failed")

        bad_body = {**SAVE_BODY, "crm_base_url": "https://down.example.com"}
        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=bad_body, headers=auth_headers()
            )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "crm_unavailable"

        session = get_session_factory()()
        stored = session.query(CRMConnection).one()
        assert stored.crm_base_url == "https://crm.example.com"
        assert stored.crm_api_key == EXT_KEY
        session.close()


# ---------------------------------------------------------------------------
# External CRM failure modes during validation
# ---------------------------------------------------------------------------


class TestValidationFailures:
    def test_auth_failure_maps_to_crm_auth_error(self, conn_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "crm_authentication_failed"
        assert EXT_KEY not in resp.text

    def test_unreachable_crm_maps_to_unavailable(self, conn_client):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("All connection attempts failed")

        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "crm_unavailable"

    def test_timeout_maps_to_timeout_error(self, conn_client):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
        assert resp.status_code == 504
        assert resp.json()["error"]["code"] == "crm_timeout"

    def test_unknown_contract_reports_provider_verification(self, conn_client):
        """404 on GET /health -> clear message, nothing saved."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
        assert resp.status_code == 502
        body = resp.json()["error"]
        assert body["code"] == "crm_connection_validation_failed"
        assert "provider-specific verification" in body["message"]

        session = get_session_factory()()
        assert session.query(CRMConnection).count() == 0
        session.close()


# ---------------------------------------------------------------------------
# URL / SSRF validation
# ---------------------------------------------------------------------------


class TestURLValidation:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "ftp://crm.example.com",
            "crm.example.com",
            "http://localhost:9000",
            "http://127.0.0.1:9000",
            "http://0.0.0.0:9000",
            "http://[::1]:9000",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.5:9000",
            "http://192.168.1.10:9000",
            "http://user:pass@crm.example.com",
            "http://metadata.google.internal",
        ],
    )
    def test_invalid_or_internal_urls_rejected(self, conn_client, bad_url):
        body = {**SAVE_BODY, "crm_base_url": bad_url}
        resp = conn_client.post(
            "/api/v1/crm/connections", json=body, headers=auth_headers()
        )
        assert resp.status_code == 422, resp.text

    def test_loopback_ip_rejected_as_request_validation_error(self, conn_client):
        """`127.0.0.1` is a literal-IP SSRF rejection, not a routing issue.

        The guard raises inside the Pydantic field validator, so FastAPI
        reports a schema-level ``request_validation_error`` (HTTP 422). This
        pins the documented reason for the reported symptom.
        """
        body = {**SAVE_BODY, "crm_base_url": "http://127.0.0.1:9000"}
        resp = conn_client.post(
            "/api/v1/crm/connections", json=body, headers=auth_headers()
        )
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "request_validation_error"
        assert error["details"][0]["location"] == "body.crm_base_url"
        assert (
            "IP '127.0.0.1' is not an allowed external destination"
            in error["details"][0]["message"]
        )

    def test_missing_required_fields_rejected(self, conn_client):
        for field in ("name", "crm_base_url", "crm_x_api_key"):
            body = {k: v for k, v in SAVE_BODY.items() if k != field}
            resp = conn_client.post(
                "/api/v1/crm/connections", json=body, headers=auth_headers()
            )
            assert resp.status_code == 422, (field, resp.text)


# ---------------------------------------------------------------------------
# Synchronization uses the STORED connection credentials
# ---------------------------------------------------------------------------


class TestSyncUsesStoredCredentials:
    def test_sync_sends_stored_key_as_x_api_key_header(self, conn_client):
        """POST /api/v1/crm/sync authenticates to the CRM with the stored
        key — the sync request itself carries no CRM credential."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.method == "POST" and request.url.path == "/contacts":
                seen["headers"] = dict(request.headers)
                seen["payload"] = json.loads(request.content)
                return httpx.Response(201, json={"id": "UC-9001"})
            return httpx.Response(404)

        with _patched(handler):
            saved = conn_client.post(
                "/api/v1/crm/connections", json=SAVE_BODY, headers=auth_headers()
            )
            assert saved.status_code == 200, saved.text

            session = get_session_factory()()
            lead = Lead(
                id="lead_sync000000000001",
                business_id=BIZ_1,
                name="Rahul Sharma",
                phone="9876543210",
                source="website",
            )
            session.add(lead)
            session.commit()
            session.close()

            sync_body = {
                "connection_id": "urban-1",
                "lead": {
                    "id": "lead_sync000000000001",
                    "business_id": BIZ_1,
                    "name": "Rahul Sharma",
                    "phone": "9876543210",
                    "source": "website",
                    "created_at": "2026-10-18T18:17:27.814533",
                },
            }
            resp = conn_client.post(
                "/api/v1/crm/sync", json=sync_body, headers=auth_headers()
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["external_crm_id"] == "UC-9001"
        # The external CRM saw its own x-api-key header...
        assert seen["headers"].get("x-api-key") == EXT_KEY
        # ...and Callora's key never crossed over.
        assert KEY_1 not in seen["headers"].values()
        assert seen["payload"]["full_name"] == "Rahul Sharma"

        session = get_session_factory()()
        lead = session.get(Lead, "lead_sync000000000001")
        assert lead.external_id == "UC-9001"
        session.close()

    def test_sync_route_registered(self, conn_client):
        """POST /api/v1/crm/sync is registered exactly once under /api/v1."""
        paths = app.openapi()["paths"]
        assert "/api/v1/crm/sync" in paths
        assert "post" in paths["/api/v1/crm/sync"]
        assert "/api/v1/crm/connections" in paths
        assert "post" in paths["/api/v1/crm/connections"]
        assert not any("/api/v1/api/v1" in p for p in paths)


# ---------------------------------------------------------------------------
# Explicit development allowlist (Docker -> fake CRM on the host)
# ---------------------------------------------------------------------------


class TestDevelopmentAllowlist:
    def test_host_docker_internal_rejected_by_default(self, conn_client):
        """The reported 422: host.docker.internal is blocked by default."""
        body = {**SAVE_BODY, "crm_base_url": DEV_CRM_URL}
        resp = conn_client.post(
            "/api/v1/crm/connections", json=body, headers=auth_headers()
        )
        assert resp.status_code == 422
        assert "not an allowed external destination" in resp.text
        assert "host.docker.internal" in resp.text

    def test_allowed_development_destination_validates_and_saves(
        self, conn_client, allow_dev_crm
    ):
        """With the exact host:port allowlisted, the fake CRM works."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["x-api-key"] = request.headers.get("x-api-key")
            return httpx.Response(200, json={"status": "ok"})

        body = {**SAVE_BODY, "crm_base_url": DEV_CRM_URL}
        with _patched(handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=body, headers=auth_headers()
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["verified"] is True
        assert data["connection"]["crm_base_url"] == DEV_CRM_URL

        # The request went to the allowlisted destination with the external
        # CRM's key (never Callora's), and the key is not echoed back.
        assert seen == {
            "url": "http://host.docker.internal:9000/health",
            "method": "GET",
            "x-api-key": EXT_KEY,
        }
        assert EXT_KEY not in resp.text
        assert KEY_1 not in resp.text

        session = get_session_factory()()
        stored = session.query(CRMConnection).one()
        assert stored.crm_base_url == DEV_CRM_URL
        assert stored.crm_api_key == EXT_KEY
        session.close()

    def test_allowlist_is_port_scoped(self, conn_client, allow_dev_crm):
        """Only host.docker.internal:9000 is allowed, not other ports."""
        body = {**SAVE_BODY, "crm_base_url": "http://host.docker.internal:9001"}
        resp = conn_client.post(
            "/api/v1/crm/connections", json=body, headers=auth_headers()
        )
        assert resp.status_code == 422
        assert "not an allowed external destination" in resp.text

    @pytest.mark.parametrize(
        "blocked_url",
        [
            "http://localhost:9000",
            "http://127.0.0.1:9000",
            "http://0.0.0.0:9000",
            "http://[::1]:9000",
            "http://10.0.0.5:9000",
            "http://192.168.1.10:9000",
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal",
            "http://evil.internal:9000",
        ],
    )
    def test_other_private_destinations_still_blocked(
        self, conn_client, allow_dev_crm, blocked_url
    ):
        """Enabling the narrow exception does not open other private hosts."""
        body = {**SAVE_BODY, "crm_base_url": blocked_url}
        resp = conn_client.post(
            "/api/v1/crm/connections", json=body, headers=auth_headers()
        )
        assert resp.status_code == 422, resp.text

    def test_sync_rechecks_destination_when_exception_is_removed(
        self, conn_client, allow_dev_crm, monkeypatch
    ):
        """The guard also runs on outgoing requests, not only connection save."""
        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(201, json={"id": "CRM-0001"})

        body = {**SAVE_BODY, "crm_base_url": DEV_CRM_URL}
        with _patched(handler):
            saved = conn_client.post(
                "/api/v1/crm/connections", json=body, headers=auth_headers()
            )
            assert saved.status_code == 200, saved.text

            session = get_session_factory()()
            session.add(
                Lead(
                    id="lead_dev0000000000001",
                    business_id=BIZ_1,
                    name="Dev Lead",
                    phone="9000000001",
                    source="website",
                )
            )
            session.commit()
            session.close()

            # The exception is no longer configured (simulates production).
            monkeypatch.delenv(DEV_ALLOWLIST_ENV, raising=False)
            get_settings.cache_clear()

            sync_body = {
                "connection_id": "urban-1",
                "lead": {
                    "id": "lead_dev0000000000001",
                    "business_id": BIZ_1,
                    "name": "Dev Lead",
                    "phone": "9000000001",
                    "source": "website",
                    "created_at": "2026-10-18T18:17:27.814533",
                },
            }
            resp = conn_client.post(
                "/api/v1/crm/sync", json=sync_body, headers=auth_headers()
            )

        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "crm_sync_failed"
        assert "POST /contacts" not in calls
        assert calls == ["GET /health"]


# ---------------------------------------------------------------------------
# Post-DNS validation and redirect refusal
# ---------------------------------------------------------------------------


class TestResolvedDestinationGuard:
    def test_hostname_resolving_to_private_ip_is_rejected(
        self, conn_client, monkeypatch
    ):
        """A public-looking hostname that resolves internally is blocked."""
        async def _private(host: str, port: int):
            return [ipaddress.ip_address("127.0.0.1")]

        monkeypatch.setattr(
            "app.modules.crm_sync.destination_guard.resolve_host_ips", _private
        )
        body = {**SAVE_BODY, "crm_base_url": "https://rebind.example.com"}
        with _patched(_ok_health_handler):
            resp = conn_client.post(
                "/api/v1/crm/connections", json=body, headers=auth_headers()
            )

        assert resp.status_code == 502
        error = resp.json()["error"]
        assert error["code"] == "crm_connection_validation_failed"
        assert "non-public" in error["message"]

        session = get_session_factory()()
        assert session.query(CRMConnection).count() == 0
        session.close()

    def test_client_blocks_private_resolution_before_sending(self):
        """The check runs in the client before the HTTP request is sent."""
        from app.modules.crm_sync.client import CRMClient, CRMClientError

        sent: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(201, json={"id": "X"})

        client = CRMClient(base_url="https://rebind.example.com", api_key="k")
        client.client = httpx.AsyncClient(
            base_url="https://rebind.example.com",
            transport=httpx.MockTransport(handler),
        )

        async def _private(host: str, port: int):
            return [ipaddress.ip_address("10.0.0.7")]

        with patch(
            "app.modules.crm_sync.destination_guard.resolve_host_ips", _private
        ):
            with pytest.raises(CRMClientError, match="non-public"):
                asyncio.run(client.create_contact({"full_name": "X"}))
        asyncio.run(client.close())

        assert sent == []

    def test_client_does_not_follow_redirects(self):
        """A 3xx is rejected instead of being followed elsewhere."""
        from app.modules.crm_sync.client import CRMClient, CRMClientError

        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest"}
            )

        client = CRMClient(base_url="https://crm.example.com", api_key="k")
        assert client.client.follow_redirects is False
        client.client = httpx.AsyncClient(
            base_url="https://crm.example.com",
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(CRMClientError, match="redirect"):
            asyncio.run(client.create_contact({"full_name": "X"}))
        asyncio.run(client.close())

        assert calls == ["https://crm.example.com/contacts"]
