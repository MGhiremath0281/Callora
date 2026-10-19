"""Field normalization helpers.

Normalization converts heterogeneous incoming values into consistent
canonical formats. Deliberate design choice: ambiguous values are *not*
guessed. For example a 10-digit Indian mobile is stored as-is (no country
code is invented) while a value that already carries ``+`` is kept in
international form. This keeps deduplication predictable and avoids
corrupting data for businesses operating in different countries.
"""

import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple

_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# ISO-8601 variants plus common business spreadsheet formats. Day-first
# formats are tried before month-first to match the primary markets; a
# fully ambiguous value (both parts <= 12) is interpreted day-first and
# never silently reformatted elsewhere.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d %m %Y",
    "%d %b %Y",
    "%d %B %Y",
)


def normalize_header(column: str) -> str:
    """Turn an arbitrary CSV header into a comparable snake_case key."""
    text = unicodedata.normalize("NFKD", str(column))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_name(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if text and text.isupper():
        # All-caps spreadsheet exports are a cosmetic problem, not an
        # ambiguous value: title-case them for display consistency.
        text = text.title()
    return text


def normalize_phone(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Normalize a phone number.

    Returns ``(normalized, error)``. Numbers with an explicit ``+`` country
    code are kept in international form (8-15 digits); local numbers must
    contain 10-15 digits. No country code is inferred.
    """
    if value is None:
        return None, "phone is required"
    raw = str(value).strip()
    if not raw:
        return None, "phone is required"
    has_country_code = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None, "phone must contain only digits, spaces, dashes or a leading '+'"
    if has_country_code:
        if not 8 <= len(digits) <= 15:
            return None, "international phone number must contain 8 to 15 digits"
        return f"+{digits}", None
    if not 10 <= len(digits) <= 15:
        return None, "phone number must contain 10 to 15 digits"
    return digits, None


def normalize_email(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if value is None or not str(value).strip():
        return None, None
    email = str(value).strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 255:
        return None, f"'{email}' is not a valid email address"
    return email, None


def normalize_source(value: Any) -> str:
    if value is None:
        return ""
    return normalize_header(str(value))


def normalize_language(value: Any) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lower()[:32]


def parse_decimal(value: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    """Parse numbers that may carry currency symbols or grouping commas.

    ``"₹75,00,000"`` -> ``7500000`` (Indian lakh grouping included).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        text = str(value).strip()
        # Keep digits, sign and decimal point; drop currency codes/symbols
        # and any grouping (including Indian lakh/crore grouping).
        text = re.sub(r"[^0-9.\-+]", "", text)
        if text in ("", "-", "+", "."):
            return None, f"'{value}' is not a valid number"
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None, f"'{value}' is not a valid number"
    if number < 0:
        return None, "value must not be negative"
    if number.as_tuple().exponent < -2:
        number = number.quantize(Decimal("0.01"))
    return number, None


def parse_datetime(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """Parse ISO-8601 or common spreadsheet date formats into UTC."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        # Normalise a trailing "Z" and space separator for fromisoformat.
        candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
        dt = None
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None, f"'{value}' is not a recognized date or timestamp"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), None


def truncate(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    return value[:max_length]
