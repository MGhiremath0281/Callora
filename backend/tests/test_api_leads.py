"""API tests for the lead endpoints (create, list, get, auth)."""

from app.models import Lead, OutboxEvent
from tests.conftest import BIZ_2, auth_headers


class TestAuthentication:
    def test_missing_api_key_rejected(self, client):
        response = client.get("/leads")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_failed"

    def test_invalid_api_key_rejected(self, client):
        response = client.get("/leads", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401

    def test_valid_api_key_accepted(self, client):
        response = client.get("/leads", headers=auth_headers())
        assert response.status_code == 200


class TestCreateLead:
    def test_create_lead_returns_201_and_persists(self, client, db):
        payload = {
            "name": "Rahul Sharma",
            "phone": "9876543210",
            "source": "Website",
            "email": "rahul@example.com",
            "language": "Hindi",
            "deal_value": "7500000",
            "custom_fields": {"locality": "Whitefield"},
        }
        response = client.post("/leads", json=payload, headers=auth_headers())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["id"].startswith("lead_")
        assert body["business_id"] == "biz_001"
        assert body["phone"] == "9876543210"
        assert body["source"] == "website"
        assert body["language"] == "hindi"
        assert body["custom_fields"] == {"locality": "Whitefield"}
        assert db.query(Lead).count() == 1

    def test_business_id_from_token_not_payload(self, client, db):
        payload = {
            "name": "X",
            "phone": "9876543210",
            "source": "website",
            "business_id": "biz_attacker",
        }
        response = client.post("/leads", json=payload, headers=auth_headers())
        assert response.status_code == 201
        assert response.json()["business_id"] == "biz_001"

    def test_duplicate_phone_conflict(self, client):
        payload = {"name": "Rahul", "phone": "9876543210", "source": "website"}
        assert client.post("/leads", json=payload, headers=auth_headers()).status_code == 201
        response = client.post("/leads", json=payload, headers=auth_headers())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_invalid_phone_rejected_by_schema(self, client):
        payload = {"name": "Rahul", "phone": "123", "source": "website"}
        response = client.post("/leads", json=payload, headers=auth_headers())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_validation_error"

    def test_missing_required_field_rejected(self, client):
        response = client.post("/leads", json={"name": "Rahul"}, headers=auth_headers())
        assert response.status_code == 422

    def test_lead_created_event_is_stored(self, client, db):
        payload = {"name": "Rahul", "phone": "9876543210", "source": "website"}
        response = client.post("/leads", json=payload, headers=auth_headers())
        lead_id = response.json()["id"]
        event = db.query(OutboxEvent).filter(OutboxEvent.lead_id == lead_id).one()
        assert event.event_type == "lead.created"
        assert event.payload == {
            "event": "lead.created",
            "lead_id": lead_id,
            "business_id": "biz_001",
        }


class TestIdempotency:
    def test_replayed_request_returns_same_lead(self, client, db):
        payload = {"name": "Rahul", "phone": "9876543210", "source": "website"}
        headers = {**auth_headers(), "Idempotency-Key": "abc-123"}
        first = client.post("/leads", json=payload, headers=headers)
        assert first.status_code == 201
        second = client.post("/leads", json=payload, headers=headers)
        assert second.status_code == 200
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert second.json()["id"] == first.json()["id"]
        assert db.query(Lead).count() == 1

    def test_different_keys_create_different_leads(self, client):
        base = {"phone": "9876543210", "source": "website"}
        first = client.post(
            "/leads", json={**base, "name": "A"}, headers={**auth_headers(), "Idempotency-Key": "k1"}
        )
        second = client.post(
            "/leads", json={**base, "name": "B"}, headers={**auth_headers(), "Idempotency-Key": "k2"}
        )
        # Different content, same phone: second is a content conflict, but
        # the first request's idempotency key never masked it.
        assert first.status_code == 201
        assert second.status_code == 409


class TestListAndGet:
    def _create(self, client, name, phone, source="website"):
        return client.post(
            "/leads",
            json={"name": name, "phone": phone, "source": source},
            headers=auth_headers(),
        )

    def test_list_pagination_and_total(self, client):
        for i in range(5):
            self._create(client, f"Customer {i}", f"987654321{i}")
        response = client.get("/leads?page=1&page_size=2", headers=auth_headers())
        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        response = client.get("/leads?page=3&page_size=2", headers=auth_headers())
        assert len(response.json()["items"]) == 1

    def test_filter_by_source(self, client):
        self._create(client, "A", "9876543210", source="website")
        self._create(client, "B", "9876543211", source="99acres")
        body = client.get("/leads?source=99acres", headers=auth_headers()).json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "B"

    def test_search_by_name(self, client):
        self._create(client, "Rahul Sharma", "9876543210")
        self._create(client, "Priya Patel", "9876543211")
        body = client.get("/leads?query=rahul", headers=auth_headers()).json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Rahul Sharma"

    def test_business_isolation(self, client):
        self._create(client, "A", "9876543210")
        body = client.get("/leads", headers=auth_headers(BIZ_2)).json()
        assert body["total"] == 0

    def test_get_single_lead(self, client):
        lead_id = self._create(client, "Rahul", "9876543210").json()["id"]
        response = client.get(f"/leads/{lead_id}", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["name"] == "Rahul"

    def test_get_missing_lead_404(self, client):
        response = client.get("/leads/lead_missing", headers=auth_headers())
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "lead_not_found"

    def test_get_other_tenants_lead_404(self, client):
        lead_id = self._create(client, "Rahul", "9876543210").json()["id"]
        response = client.get(f"/leads/{lead_id}", headers=auth_headers(BIZ_2))
        assert response.status_code == 404
