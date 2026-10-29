"""Tamper-evident audit hash chain.

Each audit row stores ``previous_hash`` (the ``entry_hash`` of the previous
row for the same business) and ``entry_hash`` (SHA-256 over the canonical
row content plus ``previous_hash``). Editing or removing a row breaks the
chain, which ``verify_chain`` reports. Rows written before the chain was
introduced have no hash and are reported as ``legacy`` rather than
invalid.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.voice.models import AuditLog


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(
    *,
    business_id: Optional[str],
    actor: str,
    action: str,
    entity_type: Optional[str],
    entity_id: Optional[str],
    decision: Optional[str],
    reason_code: Optional[str],
    reason: Optional[str],
    metadata: Dict[str, Any],
    created_at: datetime,
    previous_hash: Optional[str],
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "business_id": business_id,
                "actor": actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "decision": decision,
                "reason_code": reason_code,
                "reason": reason,
                "metadata": metadata,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        ).encode("utf-8")
    ).hexdigest()


def last_audit_hash(session: Session, business_id: Optional[str]) -> Optional[str]:
    stmt = select(AuditLog)
    if business_id is None:
        stmt = stmt.where(AuditLog.business_id.is_(None))
    else:
        stmt = stmt.where(AuditLog.business_id == business_id)
    row = session.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1)
    ).scalars().first()
    return row.entry_hash if row is not None else None


def verify_chain(
    session: Session, business_id: str, limit: int = 1000
) -> Dict[str, Any]:
    rows = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.business_id == business_id)
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
            .limit(limit)
        ).scalars()
    )
    previous: Optional[str] = None
    legacy = 0
    checked = 0
    broken: list[Dict[str, Any]] = []
    for row in rows:
        if not row.entry_hash:
            legacy += 1
            previous = None
            continue
        expected = compute_entry_hash(
            business_id=row.business_id,
            actor=row.actor,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            decision=row.decision,
            reason_code=row.reason_code,
            reason=row.reason,
            metadata=row.audit_metadata or {},
            created_at=row.created_at,
            previous_hash=row.previous_hash,
        )
        if row.previous_hash != previous or expected != row.entry_hash:
            broken.append(
                {
                    "audit_id": row.id,
                    "action": row.action,
                    "created_at": row.created_at.isoformat(),
                    "reason": (
                        "previous hash mismatch"
                        if row.previous_hash != previous
                        else "entry hash mismatch"
                    ),
                }
            )
        previous = row.entry_hash
        checked += 1
    return {
        "business_id": business_id,
        "checked": checked,
        "legacy_rows": legacy,
        "broken": broken,
        "valid": not broken,
    }
