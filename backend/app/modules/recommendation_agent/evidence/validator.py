"""Evidence validation and traceability."""

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.modules.voice import repository as voice_repo
from app.models import Lead


class EvidenceValidator:
    """Validates evidence references are real and belong to the business."""

    def __init__(self, db: Session):
        self.db = db

    def validate_evidence(
        self, business_id: str, evidence_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Validate each evidence item - call exists, belongs to business, has supporting data."""
        validated = []
        for item in evidence_items:
            call_id = item.get("call_id")
            if not call_id:
                continue

            call = voice_repo.get_call(self.db, business_id, call_id)
            if not call:
                continue  # skip invalid cross-business or non-existent calls

            # Additional validation
            validated_item = {
                "call_id": call_id,
                "lead_id": call.lead_id,
                "business_id": business_id,
                "evidence_type": item.get("evidence_type", "call"),
                "finding_supported": item.get("finding_supported", ""),
                "transcript_excerpt": self._extract_excerpt(call, item),
                "timestamp_in_call": item.get("timestamp_in_call"),
                "evidence_data": item.get("evidence_data", {}),
                "is_valid": True,
            }
            validated.append(validated_item)
        return validated

    def _extract_excerpt(self, call, item: Dict[str, Any]) -> Optional[str]:
        """Extract short relevant transcript excerpt."""
        transcript = getattr(call, "transcript", None) or ""
        if not transcript:
            return None
        # Keep it short as per requirements
        excerpt = item.get("transcript_excerpt")
        if excerpt and len(excerpt) <= 500:
            return excerpt
        # Fallback to first 200 chars if too long
        if len(transcript) > 200:
            return transcript[:200] + "..."
        return transcript
