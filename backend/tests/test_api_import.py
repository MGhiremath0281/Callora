"""API tests for the CSV import workflow and import reports."""

import json

from app.models import ImportStatus, Lead
from tests.csv_helpers import (
    REALESTATE_HEADER,
    REALESTATE_ROWS,
    build_csv,
    upload,
)
from tests.conftest import BIZ_2, auth_headers


# The preview endpoint was removed from the allowlist; its behaviour is
# covered implicitly by the import tests and by the route-registration
# assertions in tests/test_auto_sync.py.


class TestCsvImport:
    def test_import_summary_and_persistence(self, client, db):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["import_id"].startswith("imp_")
        assert body["status"] == ImportStatus.COMPLETED
        assert body["total_rows"] == 4
        assert body["created"] == 2
        assert body["duplicates"] == 1
        assert body["rejected"] == 1
        assert body["errors_url"] == f"/api/v1/imports/{body['import_id']}/errors"

        # Counts reconcile with persisted data.
        assert db.query(Lead).count() == 2
        assert body["created"] + body["duplicates"] + body["rejected"] == body["total_rows"]

        # Canonical normalization was applied.
        from decimal import Decimal

        lead = db.query(Lead).filter(Lead.phone == "9876543210").one()
        assert lead.name == "Rahul Sharma"
        assert lead.source == "99acres"
        assert lead.deal_value == Decimal("7500000")
        intl = db.query(Lead).filter(Lead.phone == "+919812345678").one()
        assert intl.name == "Priya Patel"

    def test_import_errors_endpoint(self, client):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        import_id = upload(client, csv_bytes, auth_headers()).json()["import_id"]
        response = client.get(f"/api/v1/imports/{import_id}/errors", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["import_id"] == import_id
        # One record per field-level problem: 4 validation errors on the
        # rejected row + 1 duplicate row.
        assert body["total"] == 5
        codes = {item["error_code"] for item in body["items"]}
        assert codes == {"duplicate", "validation_error"}
        rejected = [i for i in body["items"] if i["error_code"] == "validation_error"]
        assert all(i["row_number"] == 5 for i in rejected)
        phone_error = next(i for i in rejected if i["field"] == "phone")
        assert "10 to 15 digits" in phone_error["message"]
        assert phone_error["raw_data"]["Customer Name"] == "Bad Phone"

    def test_retrying_same_file_creates_no_duplicates(self, client, db):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        first = upload(client, csv_bytes, auth_headers()).json()
        assert first["created"] == 2
        second = upload(client, csv_bytes, auth_headers()).json()
        assert second["created"] == 0
        assert second["duplicates"] == 3  # 2 existing + 1 in-file
        assert second["rejected"] == 1
        assert db.query(Lead).count() == 2

    def test_idempotency_key_replays_import(self, client, db):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        headers = {**auth_headers(), "Idempotency-Key": "import-1"}
        first = upload(client, csv_bytes, headers)
        assert first.status_code == 201
        replay = upload(client, csv_bytes, headers)
        assert replay.status_code == 200
        assert replay.headers.get("Idempotency-Replayed") == "true"
        assert replay.json()["import_id"] == first.json()["import_id"]
        assert db.query(Lead).count() == 2

    def test_business_confirmed_mapping_override(self, client, db):
        csv_bytes = build_csv(
            ["Source Channel", "Mobile Number", "Name"],
            [["walk-in", "9876543210", "Rahul"]],
        )
        body = upload(client, csv_bytes, auth_headers(), mappings={"Source Channel": "source"}).json()
        assert body["created"] == 1
        lead = db.query(Lead).one()
        assert lead.source == "walk_in"
        assert lead.custom_fields == {}

    def test_unmapped_columns_become_custom_fields(self, client, db):
        csv_bytes = build_csv(
            ["Name", "Mobile Number", "RERA Number"],
            [["Rahul", "9876543210", "A51800000000"]],
        )
        upload(client, csv_bytes, auth_headers())
        lead = db.query(Lead).one()
        assert lead.custom_fields == {"rera_number": "A51800000000"}

    def test_first_and_last_name_columns_combined(self, client, db):
        csv_bytes = build_csv(
            ["First Name", "Last Name", "Mobile Number"],
            [["Rahul", "Sharma", "9876543210"]],
        )
        upload(client, csv_bytes, auth_headers())
        assert db.query(Lead).one().name == "Rahul Sharma"

    def test_events_emitted_for_created_leads_only(self, client, db):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        upload(client, csv_bytes, auth_headers())
        leads = db.query(Lead).all()
        assert len(leads) == 2
        from app.models import OutboxEvent

        events = db.query(OutboxEvent).all()
        assert len(events) == 2
        assert {e.lead_id for e in events} == {lead.id for lead in leads}
        assert all(e.payload["event"] == "lead.created" for e in events)

    def test_async_import_for_large_files(self, client, db, monkeypatch):
        monkeypatch.setenv("CALLORA_SYNC_PROCESSING_ROW_THRESHOLD", "2")
        from app.config import get_settings

        get_settings.cache_clear()
        rows = [[f"Customer {i}", f"98765430{i:02d}", "website", "100000", "", "", ""]
                for i in range(5)]
        csv_bytes = build_csv(REALESTATE_HEADER, rows)
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 202
        import_id = response.json()["import_id"]

        # Background processing has completed by the time we poll.
        status_body = client.get(f"/api/v1/imports/{import_id}", headers=auth_headers()).json()
        assert status_body["status"] == ImportStatus.COMPLETED
        assert status_body["created"] == 5
        assert db.query(Lead).count() == 5

    def test_import_status_endpoint(self, client):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        import_id = upload(client, csv_bytes, auth_headers()).json()["import_id"]
        body = client.get(f"/api/v1/imports/{import_id}", headers=auth_headers()).json()
        assert body["import_id"] == import_id
        assert body["status"] == ImportStatus.COMPLETED
        assert body["original_filename"] == "leads.csv"
        assert body["source_type"] == "csv_upload"
        assert body["completed_at"] is not None
        assert "resolved" in body["mapping"]

    def test_cross_tenant_import_is_404(self, client):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS)
        import_id = upload(client, csv_bytes, auth_headers()).json()["import_id"]
        assert client.get(f"/api/v1/imports/{import_id}", headers=auth_headers(BIZ_2)).status_code == 404
        assert (
            client.get(f"/api/v1/imports/{import_id}/errors", headers=auth_headers(BIZ_2)).status_code
            == 404
        )


class TestMappingField:
    """Tests for the mappings form field parsing and validation."""

    def test_valid_mappings_accepted(self, client, db):
        """Valid JSON mappings are accepted and applied."""
        csv_bytes = build_csv(
            ["Customer Name", "Mobile Number", "Lead Source", "Budget", "Email"],
            [["Rahul Sharma", "9876543210", "99acres", "7500000", "rahul@example.com"]],
        )
        mappings = json.dumps({
            "Customer Name": "name",
            "Mobile Number": "phone",
            "Lead Source": "source",
            "Budget": "deal_value",
            "Email": "email",
        })
        response = upload(client, csv_bytes, auth_headers(), mappings=mappings)
        assert response.status_code == 201, response.text
        lead = db.query(Lead).one()
        assert lead.name == "Rahul Sharma"
        assert lead.phone == "9876543210"
        assert lead.source == "99acres"

    def test_malformed_json_rejected(self, client):
        """Malformed JSON in mappings returns 422 with a clear error."""
        csv_bytes = build_csv(["Name", "Phone"], [["Rahul", "9876543210"]])
        response = upload(client, csv_bytes, auth_headers(), mappings="not-json")
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "malformed_file"
        assert "valid JSON" in body["error"]["message"]

    def test_swagger_string_sentinel_treated_as_empty(self, client, db):
        """Swagger UI sends 'string' as default — treat it as no mappings."""
        csv_bytes = build_csv(
            ["Customer Name", "Mobile Number", "Lead Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        response = upload(client, csv_bytes, auth_headers(), mappings="string")
        assert response.status_code == 201, response.text
        lead = db.query(Lead).one()
        assert lead.name == "Rahul Sharma"

    def test_mappings_non_object_rejected(self, client):
        """A JSON array (not object) is rejected."""
        csv_bytes = build_csv(["Name", "Phone"], [["Rahul", "9876543210"]])
        response = upload(client, csv_bytes, auth_headers(), mappings='["name", "phone"]')
        assert response.status_code == 422
        assert "JSON object" in response.json()["error"]["message"]

    def test_mappings_with_custom_field(self, client, db):
        """Mapping a column to 'custom' preserves it in custom_fields."""
        csv_bytes = build_csv(
            ["Name", "Mobile Number", "Property Type"],
            [["Rahul", "9876543210", "Apartment"]],
        )
        mappings = json.dumps({"Property Type": "custom"})
        upload(client, csv_bytes, auth_headers(), mappings=mappings)
        lead = db.query(Lead).one()
        assert lead.custom_fields.get("property_type") == "Apartment"

    def test_mappings_with_ignore(self, client, db):
        """Mapping a column to 'ignore' skips it entirely."""
        csv_bytes = build_csv(
            ["Name", "Mobile Number", "Internal Note"],
            [["Rahul", "9876543210", "some note"]],
        )
        mappings = json.dumps({"Internal Note": "ignore"})
        upload(client, csv_bytes, auth_headers(), mappings=mappings)
        lead = db.query(Lead).one()
        assert "internal_note" not in lead.custom_fields

    def test_urban_nest_full_header_set(self, client, db):
        """The full UrbanNestCRM header set maps correctly."""
        csv_bytes = build_csv(
            [
                "Customer Name",
                "Mobile Number",
                "Lead Source",
                "Property Type",
                "Budget",
                "City",
                "Email",
                "Preferred Language",
            ],
            [["Rahul Sharma", "9876543210", "99acres", "Apartment", "7500000", "Bengaluru", "rahul@example.com", "english"]],
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201, response.text
        lead = db.query(Lead).one()
        assert lead.name == "Rahul Sharma"
        assert lead.phone == "9876543210"
        assert lead.source == "99acres"
        assert lead.email == "rahul@example.com"
        assert lead.language == "english"
        assert lead.custom_fields.get("property_type") == "Apartment"
        assert lead.custom_fields.get("city") == "Bengaluru"

    def test_duplicate_leads_in_file(self, client, db):
        """Duplicate rows within the same file are detected."""
        csv_bytes = build_csv(
            ["Customer Name", "Mobile Number", "Lead Source"],
            [
                ["Rahul Sharma", "9876543210", "99acres"],
                ["Rahul Sharma", "9876543210", "99acres"],
            ],
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201
        body = response.json()
        assert body["created"] == 1
        assert body["duplicates"] == 1
        assert db.query(Lead).count() == 1

    def test_invalid_records_rejected(self, client, db):
        """Rows with invalid data are rejected with row-level errors."""
        csv_bytes = build_csv(
            ["Customer Name", "Mobile Number", "Lead Source"],
            [
                ["Rahul Sharma", "9876543210", "99acres"],
                ["Bad Phone", "123", "website"],
            ],
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201
        body = response.json()
        assert body["created"] == 1
        assert body["rejected"] == 1
        assert db.query(Lead).count() == 1


class TestLeadSchemaConsistency:
    def test_json_and_csv_paths_share_canonical_schema(self, client, db):
        """Both entry points produce identical canonical column sets."""
        csv_bytes = build_csv(["Name", "Phone", "Source"], [["Rahul Sharma", "9876543210", "Website"]])
        upload(client, csv_bytes, auth_headers())
        client.post(
            "/api/v1/leads",
            json={"name": "Priya Patel", "phone": "9812345678", "source": "Website"},
            headers=auth_headers(),
        )
        leads = db.query(Lead).all()
        assert len(leads) == 2
        for lead in leads:
            assert set(lead.custom_fields.keys()) <= set()
            assert lead.business_id == "biz_001"
            assert lead.source == "website"
