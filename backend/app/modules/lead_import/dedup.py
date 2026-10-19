"""Duplicate detection for leads.

Deduplication runs on the normalized phone number scoped to the owning
business. Two layers are checked:

1. against leads already stored in PostgreSQL;
2. within the same uploaded file (a repeated phone in one batch).

Country codes are never inferred, so ``9876543210`` and ``+919876543210``
are intentionally treated as distinct values rather than guessing.
"""

from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead

REASON_EXISTING = "existing_lead"
REASON_IN_FILE = "duplicate_in_file"


class DuplicateChecker:
    """Stateful checker used while streaming rows of one import."""

    def __init__(self, session: Session, business_id: str):
        self.session = session
        self.business_id = business_id
        self._existing_phones: Optional[Set[str]] = None
        self._existing_emails: Optional[Set[str]] = None
        self._seen_phones: Set[str] = set()
        self._seen_emails: Set[str] = set()

    def _load_existing(self) -> None:
        if self._existing_phones is not None:
            return
        rows = self.session.execute(
            select(Lead.phone, Lead.email).where(Lead.business_id == self.business_id)
        ).all()
        self._existing_phones = {r[0] for r in rows if r[0]}
        self._existing_emails = {r[1] for r in rows if r[1]}

    @staticmethod
    def dedup_key(values: Dict) -> Optional[str]:
        """Primary dedup key: normalized phone, falling back to email."""
        if values.get("phone"):
            return f"phone:{values['phone']}"
        if values.get("email"):
            return f"email:{values['email']}"
        return None

    def check(self, values: Dict) -> Optional[str]:
        """Return a duplicate reason when the row is a duplicate, else None."""
        self._load_existing()
        phone = values.get("phone")
        email = values.get("email")
        if phone and phone in self._existing_phones:
            return REASON_EXISTING
        if not phone and email and email in self._existing_emails:
            return REASON_EXISTING
        key = self.dedup_key(values)
        if key and key in self._seen_phones:
            return REASON_IN_FILE
        if key:
            self._seen_phones.add(key)
        return None

    def register_created(self, values: Dict) -> None:
        """Record a row accepted within the current batch so later rows of
        the *same* file are classified as in-file duplicates."""
        key = self.dedup_key(values)
        if key:
            self._seen_phones.add(key)

    def register_persisted(self, values: Dict) -> None:
        """Record a row that now exists in PostgreSQL (e.g. committed by
        another writer mid-import)."""
        self._load_existing()
        if values.get("phone"):
            self._existing_phones.add(values["phone"])
        if values.get("email"):
            self._existing_emails.add(values["email"])
        self.register_created(values)


def filter_duplicates(
    session: Session, business_id: str, rows: Iterable[Dict]
) -> List[Optional[str]]:
    """Convenience wrapper returning a reason per row (None when unique)."""
    checker = DuplicateChecker(session, business_id)
    results: List[Optional[str]] = []
    for values in rows:
        reason = checker.check(values)
        results.append(reason)
        if reason is None:
            checker.register_created(values)
    return results
