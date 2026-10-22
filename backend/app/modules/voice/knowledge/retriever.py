"""Business-scoped knowledge retriever.

Retrieves verified business information from PostgreSQL, strictly scoped
to the requesting business_id to prevent any cross-tenant data leakage.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.modules.voice import repository as repo
from app.modules.voice.constants import KnowledgeCategory
from app.modules.voice.models import BusinessKnowledgeItem

logger = logging.getLogger("callora.voice.knowledge.retriever")

CONFIDENCE_THRESHOLD = 0.35

CATEGORY_HINTS = {
    KnowledgeCategory.PRICING: [
        "price", "pricing", "cost", "rate", "fee", "plan", "subscription",
        "cheap", "inr", "rupees", "dollar", "payment", "discount",
        "quote", "charge", "billing", "how much", "tier", "starter", "professional", "enterprise"
    ],
    KnowledgeCategory.PRODUCT_FEATURE: [
        "feature", "support", "integrate", "integration", "api", "tool",
        "dashboard", "mobile", "app", "export", "sync", "automation",
        "webhook", "capacity", "platform", "capability", "languages", "hindi", "kannada"
    ],
    KnowledgeCategory.POLICY: [
        "refund", "cancel", "cancellation", "privacy", "security", "sla",
        "guarantee", "terms", "contract", "return", "data protection", "warranty", "maintenance"
    ],
    KnowledgeCategory.ELIGIBILITY: [
        "eligible", "eligibility", "qualify", "requirement", "prerequisite",
        "minimum", "criteria", "limit", "who can", "hardware", "server", "browser", "rooftop", "roof"
    ],
    KnowledgeCategory.OBJECTION_HANDLING: [
        "competitor", "already have", "using", "too high", "why should", "why choose",
        "better than", "difference", "switch", "alternative", "expensive", "compared",
        "other tools", "worth it"
    ],
    KnowledgeCategory.ESCALATION_RULE: [
        "human", "manager", "agent", "supervisor", "representative", "escalate",
        "callback", "transfer", "complaint", "speak to someone"
    ],
}


@dataclass
class RetrievalResult:
    """The outcome of a knowledge search."""

    items: List[BusinessKnowledgeItem] = field(default_factory=list)
    scores: List[float] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    top_confidence: float = 0.0
    is_confident: bool = False

    @property
    def has_knowledge(self) -> bool:
        return bool(self.items) and self.is_confident


class KnowledgeRetriever:
    """Retrieves verified business facts scoped to one business."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(
        self,
        business_id: str,
        question: str,
        category: Optional[str] = None,
        top_k: int = 5,
        threshold: float = CONFIDENCE_THRESHOLD,
    ) -> RetrievalResult:
        """Search knowledge items for business_id matching the customer's question."""
        cleaned_question = question.strip()
        if not cleaned_question:
            return RetrievalResult()

        detected_category = category or self._detect_category(cleaned_question)

        candidates = repo.search_knowledge_items(
            self._session,
            business_id=business_id,
            query=cleaned_question,
            category=category,
            top_k=top_k,
        )

        if not candidates:
            logger.info(
                "knowledge_retrieval_empty",
                extra={"business_id": business_id, "question": cleaned_question},
            )
            return RetrievalResult()

        if detected_category:
            reranked = []
            for item, score in candidates:
                adjusted_score = score
                if item.category == detected_category:
                    adjusted_score = min(score + 0.1, 1.0)
                reranked.append((item, adjusted_score))
            reranked.sort(key=lambda pair: pair[1], reverse=True)
            candidates = reranked

        items: List[BusinessKnowledgeItem] = []
        scores: List[float] = []
        sources: List[str] = []

        for item, score in candidates:
            items.append(item)
            scores.append(score)
            source = item.source_reference or item.title or "Verified Knowledge"
            if source not in sources:
                sources.append(source)

        top_confidence = scores[0] if scores else 0.0
        is_confident = top_confidence >= threshold

        logger.info(
            "knowledge_retrieved",
            extra={
                "business_id": business_id,
                "items_count": len(items),
                "top_confidence": top_confidence,
                "is_confident": is_confident,
            },
        )

        return RetrievalResult(
            items=items,
            scores=scores,
            sources=sources,
            top_confidence=top_confidence,
            is_confident=is_confident,
        )

    def _detect_category(self, question: str) -> Optional[str]:
        q_lower = question.lower()
        words = set(re.findall(r"\w+", q_lower))

        best_category: Optional[str] = None
        max_matches = 0

        for cat, keywords in CATEGORY_HINTS.items():
            matches = sum(1 for kw in keywords if kw in q_lower or kw in words)
            if matches > max_matches:
                max_matches = matches
                best_category = cat

        return best_category if max_matches > 0 else None
