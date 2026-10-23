"""AI guardrails for live telephone voice conversations.

Enforces:
- Immediate detection and respect of customer refusals and opt-out requests.
- Human escalation triggers.
- Ambiguity detection and natural clarification behavior.
- Factual grounding checks (no invented prices, discounts, or terms).
- Spoken telephone voice formatting (concise, no markdown, no URLs).
"""

import re
from typing import List, Optional, Tuple

OPT_OUT_PATTERNS = [
    r"\b(do\s*not|don'?t)\s+call\b",
    r"\bstop\s+calling\b",
    r"\bremove\s+(my\s+number|me)\b",
    r"\btake\s+me\s+off\s+(your|the)\s+list\b",
    r"\bput\s+me\s+on\s+(the\s+)?(dnd|do\s*not\s*call)\b",
    r"\b(not\s+interested|stop\s+bothering|never\s+call)\b",
    r"\bunsubscribe\b",
    r"\bdo\s*not\s*contact\b",
    r"(कॉल\s*मत\s*करो|फोन\s*मत\s*करो|नंबर\s*हटा\s*दो|बंद\s*करो|नहीं\s*चाहिए|दोबारा\s*फोन\s*मत\s*करना)",
]

HUMAN_ESCALATION_PATTERNS = [
    r"\b(talk|speak|connect)\s+(to|with)\s+(a\s+)?(human|person|agent|representative|manager|supervisor|operator|specialist)\b",
    r"\btransfer\s+(me|my\s+call)\b",
    r"\breal\s+person\b",
    r"\bhuman\s+please\b",
    r"\bcan\s+i\s+talk\s+to\s+someone\b",
    r"\bcall\s+me\s+back(\s+later)?\b",
    r"\b(have\s+a\s+)?(supervisor|manager|specialist|human|someone)\s+call\s+me\s+back\b",
    r"\b(request|schedule|arrange)\s+(a\s+)?callback\b",
    r"\bcallback\b",
    r"(इंसान|एजेंट|मैनेजर|अधिकारी|बात\s*करवाओ|ट्रांसफर\s*करो|कॉल\s*बैक\s*करो)",
]

AMBIGUOUS_PATTERNS = [
    r"\bwhat\s+about\s+(it|that|this|the\s+other\s+one)\b",
    r"\bcan\s+you\s+do\s+better\b",
    r"\btell\s+me\s+more\b",
    r"\bhow\s+much\s+(is\s+it|for\s+that)\b",
    r"\bis\s+it\s+possible\b",
    r"\bwhat\s+else\b",
    r"\bcan\s+i\s+get\s+a\s+discount\b",
]

UNSUPPORTED_DOMAINS_PATTERNS = [
    r"\b(approve|negotiate|custom)\s+.*(discount|lock-in|deal|contract)\b",
    r"\b(sign|lock-in)\s+(a\s+)?\d+[\s-]year\b",
    r"\b(quantum|mainframe|airplane|flying|helicopter)\b",
]


class AIGuardrails:
    """Evaluates guardrails on live customer questions and AI responses."""

    @classmethod
    def is_opt_out(cls, text: str) -> bool:
        """Return True if the text indicates an opt-out / DND request."""
        cleaned = text.strip().lower()
        return any(bool(re.search(pat, cleaned, re.IGNORECASE)) for pat in OPT_OUT_PATTERNS)

    @classmethod
    def is_human_escalation_request(cls, text: str) -> bool:
        """Return True if the customer explicitly requests human transfer/assistance."""
        cleaned = text.strip().lower()
        return any(bool(re.search(pat, cleaned, re.IGNORECASE)) for pat in HUMAN_ESCALATION_PATTERNS)

    @classmethod
    def is_unsupported_domain_request(cls, text: str) -> bool:
        """Return True if question requests contract negotiation or unsupported features."""
        cleaned = text.strip().lower()
        return any(bool(re.search(pat, cleaned, re.IGNORECASE)) for pat in UNSUPPORTED_DOMAINS_PATTERNS)

    @classmethod
    def is_ambiguous(cls, text: str) -> Tuple[bool, Optional[str]]:
        """Detect if question is too vague to answer accurately without clarification."""
        cleaned = text.strip().lower()

        # Extremely short queries without specifics
        words = cleaned.split()
        if len(words) <= 3 and any(w in ["discount", "better", "other", "more", "price"] for w in words):
            return True, "Could you clarify which plan or product you are referring to so I can give you the exact details?"

        for pat in AMBIGUOUS_PATTERNS:
            if re.search(pat, cleaned, re.IGNORECASE):
                return True, "Could you please specify which plan or feature you'd like more information on?"

        return False, None

    @classmethod
    def verify_grounding(cls, answer: str, context_text: str) -> bool:
        """Check if numbers, currency amounts, or discount percentages were invented.

        If numbers or percentages appear in the answer that do not appear in the
        verified context, the answer is flagged as ungrounded.
        """
        # Extract percentages
        answer_pcts = re.findall(r"\b\d+%", answer)
        context_pcts = re.findall(r"\b\d+%", context_text)
        for pct in answer_pcts:
            if pct not in context_pcts:
                return False

        # Extract currency amounts e.g. ₹500, Rs. 500, $50, 50000
        answer_amounts = re.findall(r"(?:₹|\$|rs\.?\s*)\s*(\d[\d,]*)", answer, re.IGNORECASE)
        context_amounts = re.findall(r"(?:₹|\$|rs\.?\s*)\s*(\d[\d,]*)", context_text, re.IGNORECASE)
        for amt in answer_amounts:
            clean_amt = amt.replace(",", "")
            clean_ctx = [c.replace(",", "") for c in context_amounts]
            if clean_amt not in clean_ctx and clean_amt not in context_text:
                return False

        return True

    @classmethod
    def format_voice_response(cls, text: str) -> str:
        """Format an answer to sound natural when spoken aloud over telephony.

        Strips markdown asterisks, backticks, bullet symbols, and URLs,
        and ensures the text is succinct for voice synthesis.
        """
        if not text:
            return ""

        # Remove markdown headers
        cleaned = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        # Remove bold / italic markdown
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
        # Remove backticks
        cleaned = cleaned.replace("`", "")
        # Remove markdown bullet points
        cleaned = re.sub(r"^\s*[-*•]\s*", "", cleaned, flags=re.MULTILINE)
        # Remove URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        # Collapse whitespace
        cleaned = " ".join(cleaned.split()).strip()

        # Limit to first 5 sentences if too lengthy
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        if len(sentences) > 5:
            cleaned = " ".join(sentences[:5])

        return cleaned
