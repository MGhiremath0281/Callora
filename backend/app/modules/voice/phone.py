"""Phone-number normalisation.

Every phone number that reaches the voice module is normalised to E.164
before it is stored or compared. Without a canonical form, the DND registry
and the duplicate-call rules would miss obvious duplicates such as
``09876543210`` and ``+919876543210``.
"""

import re
from typing import Optional

_DIGITS = re.compile(r"\d+")

#: India's country code — the default country for this product.
DEFAULT_COUNTRY_CODE = "91"


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Normalise a phone number to E.164.

    Returns ``None`` when the input does not contain a usable number.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    has_plus = text.startswith("+")
    digits = "".join(_DIGITS.findall(text))
    if not digits:
        return None

    if has_plus:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+{DEFAULT_COUNTRY_CODE}{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+{DEFAULT_COUNTRY_CODE}{digits[1:]}"
    if len(digits) == 12 and digits.startswith(DEFAULT_COUNTRY_CODE):
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith(DEFAULT_COUNTRY_CODE):
        return f"+{digits[0]}{digits}"
    return f"+{digits}"


def is_valid_e164(raw: Optional[str]) -> bool:
    """Whether a number is a plausible E.164 number (8-15 digits)."""
    normalized = normalize_phone(raw)
    if not normalized:
        return False
    digits = normalized[1:]
    return 8 <= len(digits) <= 15 and digits.isdigit()


def same_number(left: Optional[str], right: Optional[str]) -> bool:
    """Compare two numbers after normalisation."""
    a = normalize_phone(left)
    b = normalize_phone(right)
    return a is not None and a == b
