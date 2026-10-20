"""Unit tests for automatic column mapping."""

from app.modules.lead_import.mapping import (
    build_row_values,
    detect_mappings,
    merge_first_last_name,
    resolve_mappings,
)


def _suggestions(columns):
    return merge_first_last_name(detect_mappings(columns))


class TestExactSynonyms:
    def test_real_estate_headers(self):
        suggestions = _suggestions(
            ["Customer Name", "Mobile Number", "Lead Source", "Property Budget"]
        )
        by_column = {s.column: s for s in suggestions}
        assert by_column["Customer Name"].canonical_field == "name"
        assert by_column["Mobile Number"].canonical_field == "phone"
        assert by_column["Lead Source"].canonical_field == "source"
        assert by_column["Property Budget"].canonical_field == "deal_value"
        assert all(not s.ambiguous for s in by_column.values())

    def test_education_style_headers(self):
        suggestions = _suggestions(["Full Name", "Phone", "Campaign", "Course Fee", "Preferred Language"])
        by_column = {s.column: s for s in suggestions}
        assert by_column["Full Name"].canonical_field == "name"
        assert by_column["Phone"].canonical_field == "phone"
        assert by_column["Campaign"].canonical_field == "source"
        assert by_column["Course Fee"].canonical_field == "deal_value"
        assert by_column["Preferred Language"].canonical_field == "language"

    def test_healthcare_style_headers(self):
        suggestions = _suggestions(["Patient Name", "Contact Number", "Email", "Last Contacted"])
        by_column = {s.column: s for s in suggestions}
        assert by_column["Patient Name"].canonical_field == "name"
        assert by_column["Contact Number"].canonical_field == "phone"
        assert by_column["Email"].canonical_field == "email"
        assert by_column["Last Contacted"].canonical_field == "last_contact_at"

    def test_external_id_column(self):
        suggestions = _suggestions(["CRM ID", "Name", "Mobile"])
        by_column = {s.column: s for s in suggestions}
        assert by_column["CRM ID"].canonical_field == "external_id"


class TestAmbiguity:
    def test_weak_match_is_flagged_ambiguous(self):
        suggestions = _suggestions(["Source Channel", "Mobile Number"])
        weak = next(s for s in suggestions if s.column == "Source Channel")
        assert weak.ambiguous is True
        assert weak.confidence < 0.82

    def test_ambiguous_mappings_are_withheld_until_confirmed(self):
        columns = ["Source Channel", "Mobile Number", "Name"]
        resolved = resolve_mappings(columns)
        assert resolved["Source Channel"] is None  # withheld
        assert resolved["Mobile Number"] == "phone"
        assert resolved["Name"] == "name"

    def test_business_override_resolves_ambiguity(self):
        columns = ["Source Channel", "Mobile Number", "Name"]
        resolved = resolve_mappings(columns, {"Source Channel": "source"})
        assert resolved["Source Channel"] == "source"

    def test_override_can_force_custom_or_ignore(self):
        columns = ["Comments", "Name", "Mobile Number"]
        resolved = resolve_mappings(columns, {"Comments": "custom"})
        assert resolved["Comments"] == "custom"
        assert resolved["Name"] == "name"

    def test_override_ignore_skips_column(self):
        columns = ["Comments", "Name", "Mobile Number"]
        resolved = resolve_mappings(columns, {"Comments": "ignore"})
        assert resolved["Comments"] == "ignore"
        assert resolved["Name"] == "name"

    def test_unknown_column_not_mapped(self):
        resolved = resolve_mappings(["Internal Notes", "Name"])
        assert resolved["Internal Notes"] is None


class TestFirstNameLastName:
    def test_first_and_last_are_combined_into_name(self):
        suggestions = _suggestions(["First Name", "Last Name", "Mobile Number"])
        resolved = resolve_mappings([s.column for s in suggestions])
        row = {"First Name": "Rahul", "Last Name": "Sharma", "Mobile Number": "9876543210"}
        values = build_row_values(row, resolved)
        assert values["name"] == "Rahul Sharma"
        assert values["phone"] == "9876543210"

    def test_explicit_name_column_wins(self):
        suggestions = _suggestions(["Name", "First Name", "Mobile"])
        by_column = {s.column: s for s in suggestions}
        resolved = resolve_mappings([s.column for s in suggestions])
        assert resolved["Name"] == "name"
        row = {"Name": "Rahul Sharma", "First Name": "IGNORED", "Mobile": "9876543210"}
        values = build_row_values(row, resolved)
        assert values["name"] == "Rahul Sharma"


class TestBuildRowValues:
    def test_unmapped_columns_go_to_custom_fields(self):
        columns = ["Name", "Mobile Number", "RERA Number", "Preferred Locality"]
        resolved = resolve_mappings(columns)
        row = {
            "Name": "Rahul Sharma",
            "Mobile Number": "9876543210",
            "RERA Number": "A51800000000",
            "Preferred Locality": "Whitefield",
        }
        values = build_row_values(row, resolved)
        assert values["custom_fields"] == {
            "rera_number": "A51800000000",
            "preferred_locality": "Whitefield",
        }
        assert values["name"] == "Rahul Sharma"

    def test_mapped_columns_are_not_duplicated_in_custom_fields(self):
        columns = ["Customer Name", "Mobile Number"]
        resolved = resolve_mappings(columns)
        row = {"Customer Name": "Rahul", "Mobile Number": "9876543210"}
        values = build_row_values(row, resolved)
        assert values["custom_fields"] == {}
