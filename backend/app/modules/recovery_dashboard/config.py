"""Recovery Dashboard — content generation config and helpers.

The dashboard uses the *existing* Groq integration (``app.config.Settings``
and the same ``groq`` dependency) but its content shape is different from
the recovery plan: it produces a full outreach pack (voice-call script,
opening lines, responses for interested/busy/callback, WhatsApp/SMS/email,
recovery rationale, preferred-language content). Everything below must
never fabricate lead details — only verified lead/case data is injected
into prompts as delimited, untrusted data.

This module owns three cross-cutting concerns used by both the LLM
path and the deterministic fallback so the two stay consistent:

- **language**: resolved from the lead's configured language through an
  alias table, with an explicit, configurable fallback (never a
  hardcoded "Hindi for everyone");
- **agent identity**: one configurable, gender-neutral display name used
  for signatures and placeholder substitution;
- **channel plan**: preferred channel vs. alternative channels, always
  marked as drafts (nothing is ever sent).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterator, Optional, Tuple

from app.config import Settings


ALLOWED_EMAIL_CHANNELS: FrozenSet[str] = frozenset({"email"})
ALLOWED_DIRECT_CHANNELS: FrozenSet[str] = frozenset({"sms", "whatsapp", "email", "phone"})
VALID_SCRIPT_TONES: FrozenSet[str] = frozenset({"professional", "empathetic", "direct"})

# Channels for which drafts are generated (voice-call script is separate).
DRAFT_CHANNELS: Tuple[str, ...] = ("whatsapp", "sms", "email")

# Languages the deterministic templates can produce with guaranteed
# quality. Everything else falls back to ``default_outreach_language``.
SUPPORTED_OUTREACH_LANGUAGES: FrozenSet[str] = frozenset({"hindi", "english"})
DEFAULT_OUTREACH_LANGUAGE = "english"

# Canonical spellings + common aliases for the lead "language" column
# (the import normalizer lower-cases and strips; "hi"/"en" still occur).
LANGUAGE_ALIASES: Dict[str, str] = {
    "hi": "hindi",
    "hin": "hindi",
    "hindi": "hindi",
    "हिंदी": "hindi",
    "हिन्दी": "hindi",
    "en": "english",
    "eng": "english",
    "english": "english",
}

# Neutral default signer — a team label, never a gendered pronoun.
DEFAULT_AGENT_IDENTITY = "Callora Team"

# Placeholders a model may emit instead of a real signer. All are
# replaced with the configured agent identity before content is served.
_AGENT_PLACEHOLDER_CORE = (
    r"(?:agent(?:[\s_]*display)?[\s_]*name|your[\s_]*name|representative|sender)"
)
_AGENT_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*" + _AGENT_PLACEHOLDER_CORE + r"\s*\}\}"
    r"|[\[{<]\s*" + _AGENT_PLACEHOLDER_CORE + r"\s*[\]}>]",
    re.IGNORECASE,
)


def dashboard_groq_available(settings: Any) -> bool:
    """True when the same Groq configuration is usable.

    Delegates to the strategist's notion of availability so behaviour
    stays consistent with the sweep (package present + key configured).
    """
    try:
        from app.modules.recovery_agent.strategist import llm_available

        return llm_available(settings)
    except Exception:
        return bool(getattr(settings, "groq_api_key", None))


def dashboard_model(settings: Any) -> str:
    return getattr(settings, "groq_model", "openai/gpt-oss-120b")


@dataclass(frozen=True)
class OutreachTemplate:
    language: Optional[str] = None


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------


def resolve_language(raw_language: Any, settings: Optional[Settings] = None) -> str:
    """Resolve a lead's configured language to a supported outreach language.

    Alias-normalised (``"hi"`` -> ``"hindi"``), then checked against
    ``SUPPORTED_OUTREACH_LANGUAGES``. Missing or unsupported values fall
    back to the configured ``default_outreach_language``; an unusable
    configuration falls back to English. Hindi is never assumed for all
    leads — only an explicit Hindi preference produces Hindi content.
    """
    canonical = None
    if raw_language is not None and str(raw_language).strip():
        canonical = LANGUAGE_ALIASES.get(str(raw_language).strip().lower())
    if canonical in SUPPORTED_OUTREACH_LANGUAGES:
        return canonical

    configured = getattr(settings, "default_outreach_language", None)
    if configured is not None and str(configured).strip():
        fallback = LANGUAGE_ALIASES.get(str(configured).strip().lower())
        if fallback in SUPPORTED_OUTREACH_LANGUAGES:
            return fallback
    return DEFAULT_OUTREACH_LANGUAGE


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------


def agent_identity(settings: Optional[Settings] = None) -> str:
    """The configured, gender-neutral signer for drafted outreach.

    Falls back to a neutral team label when no name is configured, so
    drafts never carry an unresolved "[Agent Name]" placeholder and
    never assume the agent's gender.
    """
    name = getattr(settings, "agent_display_name", None)
    if name is not None and str(name).strip():
        return str(name).strip()
    return DEFAULT_AGENT_IDENTITY


def substitute_agent_placeholders(value: Any, identity: str) -> Any:
    """Recursively replace signer placeholders with the agent identity."""
    if isinstance(value, str):
        return _AGENT_PLACEHOLDER_RE.sub(identity, value)
    if isinstance(value, dict):
        return {k: substitute_agent_placeholders(v, identity) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_agent_placeholders(v, identity) for v in value]
    return value


def iter_agent_placeholders(value: Any) -> Iterator[str]:
    """Yield every unresolved signer placeholder inside a structure."""
    if isinstance(value, str):
        for match in _AGENT_PLACEHOLDER_RE.finditer(value):
            yield match.group(0)
    elif isinstance(value, dict):
        for v in value.values():
            yield from iter_agent_placeholders(v)
    elif isinstance(value, list):
        for v in value:
            yield from iter_agent_placeholders(v)


# ---------------------------------------------------------------------------
# Channel plan (preferred vs. alternative — always drafts)
# ---------------------------------------------------------------------------


def preferred_channel(case: Dict[str, Any], lead: Dict[str, Any], language: str) -> Optional[str]:
    """Best-supported lead-facing channel from verified data.

    The case's routed channel wins when it is a supported draft channel
    (or ``phone``, mapped to the voice script). Otherwise the fallback
    mirrors the strategist heuristic: WhatsApp for Hindi leads with a
    phone, SMS for other languages with a phone, email as last resort.
    """
    routed = str((case or {}).get("channel") or "").strip().lower()
    if routed in DRAFT_CHANNELS:
        return routed
    if routed == "phone":
        return "voice_call"
    lead = lead or {}
    if lead.get("phone"):
        return "whatsapp" if language == "hindi" else "sms"
    if lead.get("email"):
        return "email"
    return None


def channel_plan(case: Dict[str, Any], lead: Dict[str, Any], language: str) -> Dict[str, Any]:
    """Preferred channel + alternatives, explicitly draft-only."""
    pref = preferred_channel(case, lead, language)
    return {
        "preferred_channel": pref,
        "alternative_channels": [c for c in DRAFT_CHANNELS if c != pref],
        "draft_channels": list(DRAFT_CHANNELS),
        "delivery_status": "draft_only",
        "note": (
            "All channels are drafts for agent review. Nothing has been "
            "sent and no call was placed by this system."
        ),
    }


# ---------------------------------------------------------------------------
# Deterministic (rules-only) outreach pack — verified data only
# ---------------------------------------------------------------------------


def _first_name(name: Optional[str]) -> str:
    if not name:
        return "there"
    part = str(name).strip().split()
    return part[0] if part else "there"


def _english_pack(
    case: Dict[str, Any],
    lead: Dict[str, Any],
    identity: str,
    language: str,
) -> Dict[str, Any]:
    """English lead-facing drafts (also the safe fallback language)."""
    has_name = bool(str(lead.get("name") or case.get("lead_name") or "").strip())
    first = _first_name(lead.get("name") or case.get("lead_name"))
    lead_id = lead.get("lead_id") or case.get("lead_id")

    opening_line = (
        f"Hi {first}, this is regarding your enquiry with us (ref {lead_id}). "
        "May I check a convenient time for a quick conversation?"
    )
    script = {
        "type": "voice_call_script",
        "language": language,
        "objective": "Re-establish contact and understand current status (only confirmed facts).",
        "opening_line": opening_line,
        "conversation_flow": [
            {
                "step": 1,
                "purpose": "Confirm identity/context",
                "prompt": opening_line,
                "notes": (
                    "Only reference the verified enquiry reference; do not "
                    "guess property preferences."
                ),
            },
            {
                "step": 2,
                "purpose": "Understand current status",
                "prompt": (
                    "Are you still looking to proceed, or has anything "
                    "changed in your plans?"
                ),
                "notes": "Ask open-ended; accept 'not now' gracefully.",
            },
            {
                "step": 3,
                "purpose": "Next step",
                "prompt": (
                    "If it's convenient, when would you prefer we follow "
                    "up or share next steps?"
                ),
                "notes": (
                    "Offer a concrete option only if already confirmed by "
                    "process; otherwise ask."
                ),
            },
        ],
        "responses": {
            "interested": (
                "Great. May I note the best time to connect or share the "
                "next step as per process?"
            ),
            "busy": (
                "I understand. When would be a better time to call you "
                "back briefly?"
            ),
            "callback_requested": (
                "Noted. We will reach out at the time you suggest — may I "
                "confirm the number is still reachable?"
            ),
            "not_interested": (
                "Thanks for letting us know. We'll update our records "
                "accordingly. Have a good day."
            ),
            "no_answer": (
                "No answer this time. Log the attempt and retry later "
                "per outreach policy; do not fabricate a message."
            ),
        },
        "do_not_do": [
            "Do not fabricate prior conversations, site visits, quotes, budgets or timelines.",
            "Do not guess budget/property preferences.",
            "Do not offer discounts/credits unless explicitly approved (check needs_human_approval).",
            "Do not claim listings are ready/curated/available/discounted/guaranteed unless the data explicitly confirms it.",
            "Do not claim the lead 'was missed' in accusatory terms.",
            "Do not say a message was sent or a call was placed — these are drafts only.",
        ],
        "compliance_notes": (
            "Treat as a draft for agent review. No call was placed and no "
            "message was sent by this system."
        ),
    }

    whatsapp = (
        f"Hi {first}, regarding your enquiry (ref {lead_id}). "
        "Are you still exploring this? If yes, may I share next steps when convenient."
    )
    sms = (
        f"Hi {first}, ref {lead_id} — still looking? "
        "Reply YES and we can follow up at a convenient time."
    )
    email_subject = f"Follow-up on your enquiry ({lead_id})"
    email_body = (
        f"Hi {first},\n\n"
        f"This is a gentle follow-up regarding your enquiry (ref {lead_id}).\n\n"
        "If you're still exploring or have questions, we're happy to help "
        "with the next steps as they stand.\n\n"
        "If you'd prefer not to be contacted about this, please let us know.\n\n"
        f"Regards,\n{identity}\n"
    )
    return {
        "opening_line": opening_line,
        "script": script,
        "whatsapp": whatsapp,
        "sms": sms,
        "email_subject": email_subject,
        "email_body": email_body,
    }


def _hindi_pack(
    case: Dict[str, Any],
    lead: Dict[str, Any],
    identity: str,
    language: str,
) -> Dict[str, Any]:
    """Natural, respectful conversational Hindi (Devanagari script).

    Gender-neutral by construction: the honorific "you" for the lead and
    impersonal/passive constructions ("संपर्क किया जाएगा", "हम ... साझा कर
    सकते हैं") for the agent — never gendered first-person verbs like
    "मैं ... कर सकता/सकती हूँ". Names and reference numbers are copied
    verbatim from the verified data.
    """
    has_name = bool(str(lead.get("name") or case.get("lead_name") or "").strip())
    first = _first_name(lead.get("name") or case.get("lead_name"))
    lead_id = lead.get("lead_id") or case.get("lead_id")
    hi_name = f" {first}" if has_name else ""

    opening_line = (
        f"नमस्ते{hi_name}, आपकी पूछताछ (संदर्भ {lead_id}) के संबंध में "
        "बात करनी थी। क्या एक संक्षिप्त बातचीत के लिए सुविधाजनक "
        "समय बता सकते हैं?"
    )
    script = {
        "type": "voice_call_script",
        "language": language,
        "objective": "Re-establish contact and understand current status (only confirmed facts).",
        "opening_line": opening_line,
        "conversation_flow": [
            {
                "step": 1,
                "purpose": "पहचान/संदर्भ पुष्टि",
                "prompt": (
                    f"नमस्ते, आपकी पूछताछ (संदर्भ {lead_id}) के संबंध "
                    "में बात कर रहे हैं।"
                ),
                "notes": (
                    "Only reference the verified enquiry reference; do not "
                    "guess property preferences."
                ),
            },
            {
                "step": 2,
                "purpose": "वर्तमान स्थिति समझना",
                "prompt": (
                    "क्या आप अब भी आगे बढ़ना चाहते हैं, या आपकी योजना "
                    "में कुछ बदलाव आया है?"
                ),
                "notes": "Ask open-ended; accept 'not now' gracefully.",
            },
            {
                "step": 3,
                "purpose": "अगला कदम",
                "prompt": (
                    "सुविधा हो तो, अनुवर्ती संपर्क के लिए समय क्या रखें?"
                ),
                "notes": (
                    "Offer a concrete option only if already confirmed by "
                    "process; otherwise ask."
                ),
            },
        ],
        "responses": {
            "interested": (
                "बहुत बढ़िया। क्या अगला कदम तय करने के लिए सुविधाजनक "
                "समय बता सकते हैं?"
            ),
            "busy": (
                "समझ गया। किस और समय पर संक्षेप में बात करना "
                "सुविधाजनक रहेगा?"
            ),
            "callback_requested": (
                "ठीक है। आपके बताए समय पर संपर्क किया जाएगा। क्या यही "
                "नंबर अब भी उपलब्ध है?"
            ),
            "not_interested": (
                "जानकारी देने के लिए धन्यवाद। आपकी पसंद के अनुसार "
                "रिकॉर्ड अपडेट किया जाएगा। शुभ दिन।"
            ),
            "no_answer": (
                "इस बार उत्तर नहीं मिला। प्रयास दर्ज करें और नीति के "
                "अनुसार बाद में फिर से प्रयास करें।"
            ),
        },
        "do_not_do": [
            "पूर्व वार्तालाप, साइट विज़िट, कोटेशन या समय-सीमा न बनाएँ।",
            "बजट/प्रॉपर्टी पसंद अनुमान से न बताएँ।",
            "सूची/प्रॉपर्टी तैयार, क्यूरेटेड, उपलब्ध, छूट या गारंटेड तभी कहें जब डेटा स्पष्ट रूप से पुष्टि करे।",
            "संदेश भेजे जाने या कॉल किए जाने का दावा न करें — ये केवल ड्राफ्ट हैं।",
            "Do not offer discounts/credits unless explicitly approved (check needs_human_approval).",
        ],
        "compliance_notes": (
            "एक समीक्षा-हेतु ड्राफ्ट मानें। इस सिस्टम ने कोई कॉल नहीं की "
            "और कोई संदेश नहीं भेजा।"
        ),
    }

    whatsapp = (
        f"नमस्ते{hi_name}, आपकी पूछताछ (संदर्भ {lead_id}) के संबंध में। "
        "क्या आप इसे अब भी जारी रखना चाहेंगे? सुविधा हो तो अगले कदम "
        "बता सकते हैं।"
    )
    sms = (
        f"नमस्ते{hi_name}, संदर्भ {lead_id} — क्या अब भी विचार कर रहे "
        "हैं? हाँ/नहीं में उत्तर दें।"
    )
    email_subject = f"आपकी पूछताछ (संदर्भ {lead_id}) के संबंध में"
    email_body = (
        f"नमस्ते{hi_name},\n\n"
        f"आपकी पूछताछ (संदर्भ {lead_id}) के संबंध में यह संक्षिप्त "
        "अनुवर्ती संदेश।\n\n"
        "यदि आप अब भी विचार कर रहे हैं या कोई प्रश्न हो, तो मौजूदा "
        "प्रक्रिया के अनुसार अगले कदमों की जानकारी दी जा सकती है।\n\n"
        "यदि आप इस विषय पर संपर्क नहीं चाहते, तो कृपया बता दें।\n\n"
        f"सादर,\n{identity}\n"
    )
    return {
        "opening_line": opening_line,
        "script": script,
        "whatsapp": whatsapp,
        "sms": sms,
        "email_subject": email_subject,
        "email_body": email_body,
    }


def deterministic_outreach_pack(
    case: Dict[str, Any],
    lead: Dict[str, Any],
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Generate a safe, non-fabricated outreach pack without calling the LLM.

    Used as fallback on any Groq failure/timeout/malformed response and
    when no key is configured. Every field is derived from verified data
    only; the language follows the lead's configured preference with a
    safe fallback, and the signer is the configured agent identity.
    """
    case = case or {}
    lead = lead or {}
    language_setting = lead.get("language")
    language = resolve_language(language_setting, settings)
    identity = agent_identity(settings)
    plan = channel_plan(case, lead, language)

    pack = (
        _hindi_pack(case, lead, identity, language)
        if language == "hindi"
        else _english_pack(case, lead, identity, language)
    )

    lead_id = lead.get("lead_id") or case.get("lead_id")
    case_status = case.get("status")
    priority_level = case.get("priority_level")
    priority_score = case.get("priority_score")
    signals = case.get("signals") or []
    signal_summary = ", ".join(s.get("signal") for s in signals if s.get("signal")) or "none"
    rationale = case.get("rationale") or "Recovery opportunity identified from verified signals."

    return {
        "version": 1,
        "generated_by": {"engine": "rules-fallback", "model": None},
        "language": language,
        "language_setting": language_setting,
        "lead": {
            "lead_id": lead_id,
            "name": lead.get("name"),
            "phone": lead.get("phone"),
            "email": lead.get("email"),
            "language": lead.get("language"),
            "deal_value": lead.get("deal_value"),
            "source": lead.get("source"),
        },
        "case": {
            "case_id": case.get("case_id"),
            "status": case_status,
            "priority_level": priority_level,
            "priority_score": priority_score,
            "needs_human_approval": case.get("needs_human_approval"),
            "signals": signals,
        },
        "recovery_rationale": rationale,
        "context_used": {
            "signals": signal_summary,
            "priority_level": priority_level,
            "priority_score": priority_score,
            "case_status": case_status,
            "notes": (
                "Only verified fields from the recovery case and agent lead "
                "were used. No history/calls/messages/property details were "
                "fabricated; missing fields are treated as unknown."
            ),
        },
        "voice_call_script": pack["script"],
        "messages": {
            "whatsapp": {"channel": "whatsapp", "body": pack["whatsapp"]},
            "sms": {"channel": "sms", "body": pack["sms"]},
            "email": {
                "channel": "email",
                "subject": pack["email_subject"],
                "body": pack["email_body"],
            },
        },
        "quick_copy": {
            "opening_line": pack["opening_line"],
            "whatsapp": pack["whatsapp"],
            "sms": pack["sms"],
        },
        "channel_plan": plan,
        "disclaimer": (
            "This is AI-generated draft content for agent review only. It "
            "does not indicate a call was made or a message was sent."
        ),
    }
