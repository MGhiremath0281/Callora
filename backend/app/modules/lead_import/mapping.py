"""Automatic column-to-canonical-field mapping.

Businesses upload spreadsheets with different column names and ordering.
This service detects the likely canonical field for each incoming column,
reports confidence, flags ambiguity for business confirmation, and lets
the caller override any detected mapping.

Unmapped columns can be preserved in ``custom_fields`` (subject to the
configured allowlist), which is how industry-specific attributes such as
"Property Budget" or "Course Name" survive without schema changes.
"""

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.modules.lead_import.normalizer import normalize_header

# Canonical field -> accepted header synonyms (already normalized). The
# first synonym listed is the canonical header spelling.
CANONICAL_SYNONYMS: Dict[str, List[str]] = {
    "name": [
        "name",
        "customer_name",
        "full_name",
        "contact_name",
        "lead_name",
        "client_name",
        "patient_name",
        "student_name",
        "account_name",
        "person_name",
        "customer",
        "client",
        "patient",
        "student",
    ],
    "phone": [
        "phone",
        "phone_number",
        "mobile",
        "mobile_number",
        "mobile_no",
        "phone_no",
        "contact_number",
        "contact_no",
        "contact_phone",
        "whatsapp_number",
        "whatsapp",
        "telephone",
        "tel",
        "cell",
    ],
    "email": [
        "email",
        "email_address",
        "email_id",
        "mail",
        "mail_address",
        "e_mail",
    ],
    "source": [
        "source",
        "lead_source",
        "campaign",
        "channel",
        "origin",
        "referral_source",
        "acquisition_source",
    ],
    "language": [
        "language",
        "preferred_language",
        "lang",
        "conversation_language",
    ],
    "deal_value": [
        "deal_value",
        "budget",
        "property_budget",
        "estimated_value",
        "deal_size",
        "expected_value",
        "opportunity_value",
        "course_fee",
        "fee",
        "price",
        "amount",
        "contract_value",
        "annual_value",
        "ltv",
    ],
    "last_contact_at": [
        "last_contact_at",
        "last_contacted",
        "last_contact",
        "last_touch",
        "last_interaction",
        "last_interaction_date",
        "last_contact_date",
        "last_reached_at",
    ],
    "external_id": [
        "external_id",
        "crm_id",
        "record_id",
        "source_record_id",
        "lead_id",
        "reference_id",
        "id",
    ],
}

# Minimum fuzzy-match ratio before a suggestion is trusted without review.
FUZZY_MATCH_THRESHOLD = 0.82

# Fields that a fuzzy match must not steal from more likely candidates.
_ALL_CANONICAL = [alias for aliases in CANONICAL_SYNONYMS.values() for alias in aliases]


@dataclass
class MappingSuggestion:
    column: str
    canonical_field: Optional[str]
    confidence: float
    matched_on: Optional[str] = None
    ambiguous: bool = False
    alternatives: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "column": self.column,
            "canonical_field": self.canonical_field,
            "confidence": round(self.confidence, 2),
            "matched_on": self.matched_on,
            "ambiguous": self.ambiguous,
            "alternatives": self.alternatives,
        }


def _alias_to_field() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for canonical, aliases in CANONICAL_SYNONYMS.items():
        for alias in aliases:
            mapping.setdefault(alias, canonical)
    return mapping


def detect_mappings(columns: List[str]) -> List[MappingSuggestion]:
    """Suggest a canonical field for each incoming column header.

    Runs in two passes: exact alias matches reserve their canonical field
    first, so a fuzzy near-miss on an unrelated column (e.g. "Property
    Type" ~ "property_budget") can never steal a field that an exact
    synonym ("Budget" -> deal_value) should own. Fuzzy matches only fill
    fields left unclaimed.
    """
    alias_map = _alias_to_field()
    normalized = [(col, normalize_header(col)) for col in columns]
    taken: Dict[str, str] = {}  # canonical_field -> raw_column
    suggestions: Dict[str, MappingSuggestion] = {
        col: MappingSuggestion(column=col, canonical_field=None, confidence=0.0)
        for col, _ in normalized
    }

    # Pass 1: exact alias matches.
    for raw_column, header in normalized:
        if not header or header not in alias_map:
            continue
        target = alias_map[header]
        suggestion = suggestions[raw_column]
        if target in taken:
            suggestion.alternatives = [taken[target]]
            suggestion.ambiguous = True
            suggestion.matched_on = header
        else:
            suggestion.canonical_field = target
            suggestion.matched_on = header
            # Exact canonical spelling scores highest; synonyms slightly
            # lower so the preview communicates certainty honestly.
            suggestion.confidence = 1.0 if header == target else 0.95
            taken[target] = raw_column

    # Pass 2: fuzzy matches, restricted to fields not already owned by an
    # exact match.
    for raw_column, header in normalized:
        if not header or header in alias_map:
            continue
        suggestion = suggestions[raw_column]
        open_aliases = [a for a in _ALL_CANONICAL if alias_map[a] not in taken]
        matches = difflib.get_close_matches(header, open_aliases, n=2, cutoff=0.6)
        if not matches:
            continue
        best = matches[0]
        ratio = difflib.SequenceMatcher(None, header, best).ratio()
        target = alias_map[best]
        alternatives = [alias_map[m] for m in matches[1:2] if alias_map[m] != target]
        suggestion.canonical_field = target
        suggestion.matched_on = best
        suggestion.confidence = ratio
        suggestion.ambiguous = ratio < FUZZY_MATCH_THRESHOLD
        suggestion.alternatives = alternatives
        if ratio >= 0.6:
            taken[target] = raw_column

    return [suggestions[col] for col, _ in normalized]


def merge_first_last_name(suggestions: List[MappingSuggestion]) -> List[MappingSuggestion]:
    """Mark ``first_name`` + ``last_name`` columns for combination into ``name``."""
    lowered = {normalize_header(s.column): s for s in suggestions}
    has_first = "first_name" in lowered or "firstname" in lowered
    has_last = "last_name" in lowered or "lastname" in lowered
    # A fuzzy match may have tentatively claimed "name" (e.g. "first_name"
    # ~ "full_name"); only a confident, exact name column wins over the
    # first+last combination.
    name_suggestion = next(
        (s for s in suggestions if s.canonical_field == "name" and not s.ambiguous),
        None,
    )
    if has_first and has_last and name_suggestion is None:
        for key in ("first_name", "firstname"):
            if key in lowered:
                lowered[key].canonical_field = "name_part"
                lowered[key].confidence = 0.9
                lowered[key].ambiguous = False
                lowered[key].matched_on = key
                break
        for key in ("last_name", "lastname"):
            if key in lowered:
                lowered[key].canonical_field = "name_part"
                lowered[key].confidence = 0.9
                lowered[key].ambiguous = False
                lowered[key].matched_on = key
                break
    return suggestions


def resolve_mappings(
    columns: List[str],
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Optional[str]]:
    """Produce the final ``{incoming_column: canonical_field}`` mapping.

    ``overrides`` (business-confirmed mappings) always win and may map a
    column to ``"custom"`` (force into custom_fields) or ``None`` (ignore).
    """
    suggestions = merge_first_last_name(detect_mappings(columns))
    resolved: Dict[str, Optional[str]] = {}
    for suggestion in suggestions:
        # Untrusted (ambiguous/low-confidence) suggestions are withheld
        # until confirmed; their data still lands in custom_fields.
        resolved[suggestion.column] = (
            suggestion.canonical_field
            if suggestion.canonical_field and not suggestion.ambiguous
            else None
        )
    for column, override in (overrides or {}).items():
        if column not in {c for c in columns}:
            continue
        resolved[column] = None if override in ("", "custom", "ignore") else override
    return resolved


def build_row_values(
    row: Dict[str, str],
    resolved: Dict[str, Optional[str]],
    name_parts: Optional[Dict[str, str]] = None,
) -> Dict[str, Optional[str]]:
    """Apply a resolved mapping to one raw CSV row.

    Returns a dict with canonical keys (string values, still raw) plus a
    ``custom_fields`` sub-dict for unmapped columns.
    """
    values: Dict[str, Optional[str]] = {key: None for key in CANONICAL_SYNONYMS}
    custom_fields: Dict[str, str] = {}
    used_columns = set()
    name_parts_values: List[str] = []

    for column, raw_value in row.items():
        target = resolved.get(column)
        if target == "name_part":
            name_parts_values.append(str(raw_value or "").strip())
            used_columns.add(column)
        elif target and target in values and not values[target]:
            values[target] = str(raw_value).strip() if raw_value is not None else None
            used_columns.add(column)
        elif target is None:
            # Left to the caller: it decides whether unmapped columns are
            # captured as custom fields (allowlist enforced there).
            pass

    if not values.get("name") and name_parts_values:
        values["name"] = " ".join(p for p in name_parts_values if p)

    for column, raw_value in row.items():
        if column in used_columns:
            continue
        if resolved.get(column) is None and raw_value is not None and str(raw_value).strip():
            custom_fields[normalize_header(column) or column] = str(raw_value).strip()

    values["custom_fields"] = custom_fields  # type: ignore[assignment]
    return values
