"""Recovery Dashboard — content generation config and helpers.

The dashboard uses the *existing* Groq integration (``app.config.Settings``
and the same ``groq`` dependency) but its content shape is different from
the recovery plan: it produces a full outreach pack (voice-call script,
opening lines, responses for interested/busy/callback, WhatsApp/SMS/email,
recovery rationale, preferred-language content). Everything below must
never fabricate lead details — only verified lead/case data is injected
into prompts as delimited, untrusted data.
"""

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Optional

from app.config import Settings, get_settings


ALLOWED_EMAIL_CHANNELS: FrozenSet[str] = frozenset({"email"})
ALLOWED_DIRECT_CHANNELS: FrozenSet[str] = frozenset({"sms", "whatsapp", "email", "phone"})
VALID_SCRIPT_TONES: FrozenSet[str] = frozenset({"professional", "empathetic", "direct"})


def dashboard_groq_available(settings: Settings) -> bool:
    """True when the same Groq configuration is usable.

    Delegates to the strategist's notion of availability so behaviour
    stays consistent with the sweep (package present + key configured).
    """
    try:
        from app.modules.recovery_agent.strategist import llm_available

        return llm_available(settings)
    except Exception:
        return bool(getattr(settings, "groq_api_key", None))


def dashboard_model(settings: Settings) -> str:
    return getattr(settings, "groq_model", "openai/gpt-oss-120b")


@dataclass(frozen=True)
class OutreachTemplate:
    """A deterministic template used when Groq is unavailable or fails.

    Templates are language-aware only where trivial (Hindi/sms flavour)
    and must not invent property preferences, history or buying intent.
    """

    language: Optional[str] = None


def _first_name(name: Optional[str]) -> str:
    if not name:
        return "there"
    part = name.strip().split()
    return part[0] if part else "there"


def deterministic_outreach_pack(case: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a safe, non-fabricated outreach pack without calling the LLM.

    Used as fallback on any Groq failure/timeout/malformed response (the
    requirement says handle failures gracefully) and when no key is
    configured. Every field is derived from verified data only.
    """
    first = _first_name(lead.get("name") or case.get("lead_name"))
    lead_id = lead.get("lead_id") or case.get("lead_id")
    language = (lead.get("language") or "").strip().lower() or None
    case_status = case.get("status")
    priority_level = case.get("priority_level")
    priority_score = case.get("priority_score")
    signals = case.get("signals") or []
    signal_summary = ", ".join(s.get("signal") for s in signals if s.get("signal")) or "none"
    rationale = case.get("rationale") or "Recovery opportunity identified from verified signals."

    opening_line = (
        f"Hi {first}, this is regarding your enquiry with us (ref {lead_id}). "
        "May I check a convenient time for a quick conversation?"
    )
    if language == "hindi":
        opening_line = (
            f"Namaste {first}, aapki enquiry ke baare mein baat karni thi "
            f"(ref {lead_id}). Kya main kuch samay mein baat kar sakta/sakti hoon?"
        )

    script = {
        "type": "voice_call_script",
        "language": language or "unknown",
        "objective": "Re-establish contact and understand current status (only confirmed facts).",
        "opening_line": opening_line,
        "conversation_flow": [
            {
                "step": 1,
                "purpose": "Confirm identity/context",
                "prompt": opening_line,
                "notes": "Only reference the verified enquiry reference; do not guess property preferences.",
            },
            {
                "step": 2,
                "purpose": "Understand current status",
                "prompt": "Are you still looking to proceed, or has anything changed in your plans?",
                "notes": "Ask open-ended, accept 'not now' gracefully.",
            },
            {
                "step": 3,
                "purpose": "Next step",
                "prompt": "If it's convenient, when would you prefer I follow up or share next steps?",
                "notes": "Offer a concrete option only if already confirmed by process; otherwise ask.",
            },
        ],
        "responses": {
            "interested": "Great. May I note the best time to connect or share the next step as per process?",
            "busy": "I understand. When would be a better time for me to call you back briefly?",
            "callback_requested": "Noted. I'll call back at the time you suggest, or may I confirm the number is still reachable?",
            "not_interested": "Thanks for letting me know. I'll update our records accordingly. Have a good day.",
            "no_answer": "I'll try again later as per outreach policy; log the attempt outcome separately (no message fabricated).",
        },
        "do_not_do": [
            "Do not fabricate prior conversations, site visits or quotes.",
            "Do not guess budget/property preferences.",
            "Do not offer discounts/credits unless explicitly approved (check needs_human_approval).",
            "Do not claim the lead 'was missed' in accusatory terms.",
        ],
        "compliance_notes": "Treat as a draft for agent review. No call was placed by this system.",
    }

    whatsapp = (
        f"Hi {first}, regarding your enquiry (ref {lead_id}). "
        "Are you still exploring this? If yes, may I share next steps when convenient."
    )
    sms = (
        f"Hi {first}, ref {lead_id} — still looking? "
        "Reply YES and I can follow up at a convenient time."
    )
    if language == "hindi":
        whatsapp = f"Namaste {first}, aapki enquiry (ref {lead_id}) ke baare mein. Kya ab bhi jaari rakhna chahte hain?"
        sms = f"Namaste {first}, ref {lead_id}. Ab bhi interested hain? Haan/Na reply karein."

    email_subject = f"Follow-up on your enquiry ({lead_id})"
    email_body = (
        f"Hi {first},\n\n"
        f"This is a gentle follow-up regarding your enquiry (ref {lead_id}).\n\n"
        "If you're still exploring or have questions, I'm happy to help with the next steps as they stand.\n\n"
        "If you'd prefer not to be contacted about this, please let me know.\n\n"
        "Regards,\nThe Callora Team\n"
    )

    return {
        "version": 1,
        "generated_by": {"engine": "rules-fallback", "model": None},
        "language": language,
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
            "notes": "Only verified fields from the recovery case and agent lead were used. No history/calls/messages were fabricated.",
        },
        "voice_call_script": script,
        "messages": {
            "whatsapp": {"channel": "whatsapp", "body": whatsapp},
            "sms": {"channel": "sms", "body": sms},
            "email": {"channel": "email", "subject": email_subject, "body": email_body},
        },
        "quick_copy": {
            "opening_line": opening_line,
            "whatsapp": whatsapp,
            "sms": sms,
        },
        "disclaimer": "This is AI-generated draft content for agent review only. It does not indicate a call was made or a message was sent.",
    }
