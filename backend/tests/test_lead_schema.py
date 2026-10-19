"""Schema verification tests for the standardized lead output contract.

Both entry points — direct JSON submission (``POST /leads``) and CSV
upload (``POST /leads/import``) — must resolve to the exact same lead
JSON schema: identical keys, phone always a string, deal_value always a
decimal string with two fractional digits.
"""

from tests.conftest import BIZ_1, BIZ_2, auth_headers
from tests.csv_helpers import build_csv, upload

# The exact standardized lead output contract (see the Module 1 spec).
STANDARD_LEAD_KEYS = {
    "id",
    "business_id",
    "name",
    "phone",
    "email",
    "source",
    "language",
    "deal_value",
    "last_contact_at",
    "custom_fields",
    "external_id",
    "created_at",
}

JSON_PAYLOAD = {
    "name": "Rahul Sharma",
    "phone": "9876543210",
    "email": "rahul.test@example.com",
    "source": "99acres",
    "language": "English",
    "deal_value": "7500000",
    "custom_fields": {"property_type": "Apartment", "city": "Bengaluru"},
}

CSV_HEADER = ["Name", "Phone", "Email", "Source", "Language", "Deal Value", "Property Type", "City"]
CSV_ROW = [
    "Rahul Sharma",
    "9876543210",
    "rahul.test@example.com",
    "99acres",
    "English",
    "7500000",
    "Apartment",
    "Bengaluru",
]


def create_via_json(client, business=BIZ_2):
    response = client.post("/api/v1/leads", json=JSON_PAYLOAD, headers=auth_headers(business))
    assert response.status_code == 201, response.text
    return response.json()


def create_via_csv(client, business=BIZ_2):
    csv_bytes = build_csv(CSV_HEADER, [CSV_ROW])
    body = upload(client, csv_bytes, auth_headers(business)).json()
    assert body["created"] == 1, body
    items = client.get("/api/v1/leads", headers=auth_headers(business)).json()["items"]
    lead_id = next(item["id"] for item in items if item["phone"] == "9876543210")
    response = client.get(f"/api/v1/leads/{lead_id}", headers=auth_headers(business))
    assert response.status_code == 200
    return response.json()


class TestStandardizedLeadSchema:
    def test_json_created_lead_matches_standard_schema(self, client):
        body = create_via_json(client)
        assert set(body.keys()) == STANDARD_LEAD_KEYS
        assert body["id"].startswith("lead_")
        assert body["business_id"] == BIZ_2
        assert body["name"] == "Rahul Sharma"
        assert body["source"] == "99acres"
        assert body["language"] == "english"
        assert body["last_contact_at"] is None
        assert body["external_id"] is None
        assert body["custom_fields"] == {"property_type": "Apartment", "city": "Bengaluru"}

    def test_csv_imported_lead_matches_standard_schema(self, client):
        body = create_via_csv(client)
        assert set(body.keys()) == STANDARD_LEAD_KEYS
        assert body["business_id"] == BIZ_2
        assert body["name"] == "Rahul Sharma"
        assert body["source"] == "99acres"
        assert body["custom_fields"] == {"property_type": "Apartment", "city": "Bengaluru"}

    def test_phone_is_always_a_string(self, client):
        json_body = create_via_json(client, business=BIZ_1)
        csv_body = create_via_csv(client, business=BIZ_2)
        for body in (json_body, csv_body):
            assert isinstance(body["phone"], str)
            assert body["phone"] == "9876543210"

    def test_deal_value_is_always_a_decimal_string(self, client):
        json_body = create_via_json(client, business=BIZ_1)
        csv_body = create_via_csv(client, business=BIZ_2)
        for body in (json_body, csv_body):
            assert isinstance(body["deal_value"], str)
            assert body["deal_value"] == "7500000.00"

    def test_deal_value_null_stays_null(self, client):
        response = client.post(
            "/api/v1/leads",
            json={"name": "Priya", "phone": "9812345678", "source": "website"},
            headers=auth_headers(BIZ_2),
        )
        assert response.json()["deal_value"] is None

    def test_json_and_csv_outputs_are_identical(self, client):
        """Same content through both paths -> identical lead JSON."""
        json_body = create_via_json(client, business=BIZ_1)
        csv_body = create_via_csv(client, business=BIZ_2)
        # Only tenant-scoped ids and timestamps may differ.
        volatile = {"id", "created_at", "business_id"}
        assert set(json_body.keys()) == set(csv_body.keys())
        assert {k: v for k, v in json_body.items() if k not in volatile} == {
            k: v for k, v in csv_body.items() if k not in volatile
        }

    def test_list_endpoint_uses_standard_schema(self, client):
        create_via_json(client)
        items = client.get("/api/v1/leads", headers=auth_headers(BIZ_2)).json()["items"]
        assert len(items) == 1
        assert set(items[0].keys()) == STANDARD_LEAD_KEYS
