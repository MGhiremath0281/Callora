"""Unit tests for row validation against the canonical lead structure."""

from app.config import Settings
from app.modules.lead_import.validators import validate_row


def make_settings(**overrides) -> Settings:
    defaults = dict(database_url="sqlite://", api_keys="biz_001:k")
    defaults.update(overrides)
    return Settings(**defaults)


VALID_ROW = {
    "name": "Rahul Sharma",
    "phone": "9876543210",
    "source": "99acres",
    "email": "rahul@example.com",
    "language": "hindi",
    "deal_value": "7500000",
    "last_contact_at": "2026-01-15",
    "external_id": "CRM-1",
    "custom_fields": {},
}


class TestValidRow:
    def test_valid_row_is_normalized(self):
        result = validate_row(VALID_ROW, make_settings())
        assert result.is_valid
        assert result.values["name"] == "Rahul Sharma"
        assert result.values["phone"] == "9876543210"
        assert result.values["source"] == "99acres"
        assert str(result.values["deal_value"]) == "7500000"
        assert result.values["last_contact_at"] is not None

    def test_missing_source_defaults_to_configured_value(self):
        row = dict(VALID_ROW, source="")
        result = validate_row(row, make_settings())
        assert result.is_valid
        assert result.values["source"] == "import"


class TestRequiredFields:
    def test_missing_name_rejected(self):
        result = validate_row(dict(VALID_ROW, name="  "), make_settings())
        assert not result.is_valid
        assert any(e.field == "name" and e.error_code == "missing_required" for e in result.errors)

    def test_missing_phone_rejected(self):
        result = validate_row(dict(VALID_ROW, phone=""), make_settings())
        assert not result.is_valid
        assert any(e.field == "phone" for e in result.errors)

    def test_multiple_errors_collected_per_row(self):
        row = dict(VALID_ROW, name="", phone="123", email="bad", deal_value="abc", last_contact_at="nope")
        result = validate_row(row, make_settings())
        assert not result.is_valid
        fields = {e.field for e in result.errors}
        assert {"name", "phone", "email", "deal_value", "last_contact_at"} <= fields


class TestCustomFieldPolicy:
    def test_auto_capture_enabled_by_default(self):
        row = dict(VALID_ROW, custom_fields={"rera_number": "A1"})
        result = validate_row(row, make_settings())
        assert result.values["custom_fields"] == {"rera_number": "A1"}

    def test_auto_capture_disabled_drops_fields(self):
        row = dict(VALID_ROW, custom_fields={"rera_number": "A1"})
        result = validate_row(row, make_settings(auto_capture_custom_fields=False))
        assert result.values["custom_fields"] == {}

    def test_allowlist_restricts_fields(self):
        row = dict(VALID_ROW, custom_fields={"rera_number": "A1", "budget_note": "x"})
        settings = make_settings(custom_field_allowlist="rera_number")
        result = validate_row(row, settings)
        assert result.values["custom_fields"] == {"rera_number": "A1"}

    def test_max_custom_fields_cap(self):
        custom = {f"key_{i}": i for i in range(100)}
        row = dict(VALID_ROW, custom_fields=custom)
        settings = make_settings(max_custom_fields=5)
        result = validate_row(row, settings)
        assert len(result.values["custom_fields"]) == 5
