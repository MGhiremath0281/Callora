"""Evidence validation and traceability.

An evidence item is only accepted when it points at a real call that is
owned by the authenticated business **and** that call contains the data the
finding claims to rely on. A call without a transcript can never support a
transcript-derived finding, and an item that names required keywords must
actually contain at least one of them. This prevents the generator from
turning an empty record into "evidence".
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.modules.voice import repository as voice_repo


class EvidenceValidator:
    """Validates evidence references are real and belong to the business."""

    def __init__(self, db: Session):
        self.db = db

    def validate_evidence(
        self, business_id: str, evidence_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Validate each evidence item.

        Required: ``call_id`` resolves to a call owned by ``business_id``.
        For transcript evidence the call must have a non-empty transcript.
        When ``required_keywords`` is supplied at least one must occur in
        the transcript (case-insensitive).
        """
        validated: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in evidence_items:
            call_id = item.get("call_id")
            if not call_id or call_id in seen:
                continue

            call = voice_repo.get_call(self.db, business_id, call_id)
            if not call:
                # Skip cross-business or non-existent references: never
                # let another tenant's call support a recommendation.
                continue

            evidence_type = item.get("evidence_type", "call")
            transcript = (getattr(call, "transcript", None) or "").strip()

            if evidence_type in {"call", "transcript", "objection", "recovery"}:
                if not transcript:
                    continue
                required = [
                    str(k).lower()
                    for k in (item.get("required_keywords") or [])
                    if str(k).strip()
                ]
                if required and not any(k in transcript.lower() for k in required):
                    continue

            validated_item = {
                "call_id": call_id,
                "lead_id": call.lead_id,
                "business_id": business_id,
                "evidence_type": evidence_type,
                "finding_supported": item.get("finding_supported", ""),
                "transcript_excerpt": self._extract_excerpt(call, item),
                "timestamp_in_call": item.get("timestamp_in_call"),
                "evidence_data": item.get("evidence_data", {}),
                "is_valid": True,
            }
            validated.append(validated_item)
            seen.add(call_id)
        return validated

    def _extract_excerpt(self, call, item: Dict[str, Any]) -> Optional[str]:
        """Extract a short, verbatim excerpt from the call transcript."""
        transcript = getattr(call, "transcript", None) or ""
        if not transcript:
            return None
        excerpt = item.get("transcript_excerpt")
        if excerpt and excerpt in transcript and len(excerpt) <= 500:
            return excerpt
        keywords = [str(k).lower() for k in (item.get("required_keywords") or [])]
        lowered = transcript.lower()
        for keyword in keywords:
            index = lowered.find(keyword)
            if index >= 0:
                start = max(0, index - 100)
                return transcript[start : start + 300]
        return transcript[:300] + ("..." if len(transcript) > 300 else "")
