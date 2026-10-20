"""Regression tests for outreach-content generation quality.

Covers the generation pipeline behind
``POST /api/v1/agent/recovery/cases/{case_id}/outreach-content/generate``:

- natural, gender-neutral Hindi generation and language-preference
  handling (aliases, missing and unsupported values, configurable
  fallback — Hindi is never hardcoded);
- configurable agent identity and unresolved "[Agent Name]" placeholders;
- no invented property/offer/prior-conversation/delivery claims,
  verified-vs-missing field separation, CRM-evidence gating;
- preferred vs. alternative channels and quick-copy/draft consistency;
- structured-output validation and safe handling of malformed model
  responses (never blended into fabricated lead-specific content);
- drafts only — no sending or call execution is ever implied or recorded;
- backward compatibility of the existing API response shapes.

All Groq calls are mocked; no API key or network access is required.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.conftest import BIZ_1, auth_headers

from app.modules.agent_storage.service import upsert_agent_lead
from app.modules.recovery_agent import strategist as strategist_module
from app.modules.recovery_dashboard import config as dash_config
from app.modules.recovery_dashboard import outreach


BACKDATED = datetime.now(timezone.utc) - timedelta(days=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def base_case(**overrides):
    case = {
        "case_id": "case_test00000000001",
        "lead_id": "lead_test00000000001",
        "lead_name": "Priya Patil",
        "business_id": BIZ_1,
        "score": 0.9,
        "priority_score": 77.5,
        "priority_rank": 1,
        "priority_level": "high",
        "score_breakdown": None,
        "scored_at": None,
        "signals": [
            {
                "signal": "never_contacted",
                "severity": "high",
                "detail": "no contact 43200 min after creation (SLA 120 min)",
            }
        ],
        "route": "llm",
        "action": "re_contact_lead",
        "channel": None,
        "message": "",
        "rationale": "Never contacted past SLA; deal value 1.25 Cr.",
        "engine": "rules-fallback",
        "needs_human_approval": False,
        "status": "recommended",
        "created_at": None,
        "updated_at": None,
    }
    case.update(overrides)
    return case


def base_lead(**overrides):
    lead = {
        "lead_id": "lead_test00000000001",
        "name": "Priya Patil",
        "phone": "9876543211",
        "email": "priya.test@example.com",
        "source": "magicbricks",
        "status": "New",
        "language": "english",
        "deal_value": 12_500_000.0,
        "last_contact_at": None,
        "external_crm_id": None,
        "custom_fields": {},
        "created_at": BACKDATED.isoformat(),
        "updated_at": BACKDATED.isoformat(),
    }
    lead.update(overrides)
    return lead


def valid_llm_pack(**overrides):
    """A model response that passes schema validation and grounding."""
    pack = {
        "voice_call_script": {
            "opening_line": "Hello Priya, regarding your enquiry (ref lead_test00000000001).",
            "objective": "Re-establish contact.",
            "conversation_flow": [
                {"step": 1, "purpose": "Context", "prompt": "Opening", "notes": "Verified ref only."}
            ],
            "responses": {
                "interested": "Great — may we agree a convenient time for the next step?",
                "busy": "Understood — when is a better time to speak briefly?",
                "callback_requested": "Noted — we will reach out at the time you suggest.",
                "not_interested": "Thanks for letting us know; we will update our records.",
                "no_answer": "No answer — log the attempt and retry later per policy.",
            },
            "do_not_do": ["Do not fabricate history."],
        },
        "messages": {
            "whatsapp": {"body": "Hello Priya, regarding your enquiry (ref lead_test00000000001)."},
            "sms": {"body": "Hello Priya, ref lead_test00000000001 — still exploring?"},
            "email": {
                "subject": "Follow-up on your enquiry (lead_test00000000001)",
                "body": "Hello Priya,\n\nRegarding your enquiry (ref lead_test00000000001).\n\nRegards,\nCallora Team\n",
            },
        },
        "quick_copy": {"opening_line": "x", "whatsapp": "y", "sms": "z"},
        "recovery_rationale": "Never contacted past SLA; deal value 1.25 Cr.",
        "context_used": {"notes": "Verified fields only."},
    }
    pack.update(overrides)
    return pack


def install_fake_groq(monkeypatch, *responses):
    """Replace the strategist's Groq client with an offline fake.

    ``responses`` is consumed in order (the last entry repeats); entries
    that are exceptions are raised. All created clients/calls are
    recorded on the returned state dict.
    """
    state = {"calls": [], "clients": []}

    class _FakeCompletions:
        def create(self, **kwargs):
            state["calls"].append(kwargs)
            index = min(len(state["calls"]) - 1, len(responses) - 1)
            result = responses[index]
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
            )

    class _FakeGroq:
        def __init__(self, api_key=None):
            state["clients"].append(api_key)
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(strategist_module, "Groq", _FakeGroq)
    return state


def all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from all_strings(v)


def combined_text(pack):
    return "\n".join(all_strings(pack)).lower()


def lead_facing_text(pack):
    """Only the lead-facing text of a pack (excludes operator guidance)."""
    return "\n".join(outreach._iter_lead_facing_strings(pack)).lower()


def enable_llm(settings):
    settings.groq_api_key = "gsk_test_offline"


@pytest.fixture()
def swept_case(client, db):
    """Run the recovery sweep for one neglected lead; return its case id."""
    upsert_agent_lead(
        db,
        lead_id="lead_test00000000001",
        business_id=BIZ_1,
        name="Priya Patil",
        phone="9876543211",
        email="priya.test@example.com",
        source="magicbricks",
        language="english",
        deal_value=12_500_000,
        last_contact_at=None,
        custom_fields={},
        external_crm_id=None,
        status="New",
        created_at=BACKDATED,
    )
    db.commit()
    response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
    assert response.status_code == 200
    cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
    assert len(cases) == 1
    return cases[0]["case_id"]


# ---------------------------------------------------------------------------
# Language handling / Hindi quality
# ---------------------------------------------------------------------------


class TestLanguageHandling:
    def test_hindi_fallback_pack_is_devanagari(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="hindi"), settings
        )
        assert pack["language"] == "hindi"
        opening = pack["voice_call_script"]["opening_line"]
        assert "नमस्ते" in opening
        assert "संदर्भ" in opening
        # No Latin transliteration of Hindi.
        assert "aapki" not in combined_text(pack)
        assert "namaste" not in combined_text(pack)
        # All five scripted responses present, in Hindi.
        responses = pack["voice_call_script"]["responses"]
        assert set(responses) == {
            "interested",
            "busy",
            "callback_requested",
            "not_interested",
            "no_answer",
        }
        for text in responses.values():
            assert any("\u0900" <= ch <= "\u097f" for ch in text)

    def test_hindi_content_is_gender_neutral(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="hindi"), settings
        )
        lead_facing = [
            pack["voice_call_script"]["opening_line"],
            *pack["voice_call_script"]["responses"].values(),
            *[s["prompt"] for s in pack["voice_call_script"]["conversation_flow"]],
            pack["messages"]["whatsapp"]["body"],
            pack["messages"]["sms"]["body"],
            pack["messages"]["email"]["body"],
        ]
        for text in lead_facing:
            # No gendered first-person verbs, no "sakta/sakti" hybrids.
            assert "मैं" not in text
            assert "sakta" not in text.lower()
            assert "sakti" not in text.lower()
        # Honorific, neutral request form instead of gendered verbs.
        assert "बता सकते हैं" in pack["voice_call_script"]["opening_line"]

    @pytest.mark.parametrize("raw", ["hi", "Hindi", " हिंदी ", "HINDI"])
    def test_hindi_aliases_resolve(self, settings, raw):
        assert dash_config.resolve_language(raw, settings) == "hindi"

    @pytest.mark.parametrize("raw", ["en", "EN", "English", "eng"])
    def test_english_aliases_resolve(self, settings, raw):
        assert dash_config.resolve_language(raw, settings) == "english"

    def test_missing_language_falls_back_to_english(self, settings):
        settings.default_outreach_language = "english"
        assert dash_config.resolve_language(None, settings) == "english"
        assert dash_config.resolve_language("", settings) == "english"

    def test_unsupported_language_falls_back(self, settings):
        settings.default_outreach_language = "english"
        assert dash_config.resolve_language("kannada", settings) == "english"
        # The fallback is configurable — not hardcoded English or Hindi.
        settings.default_outreach_language = "hindi"
        assert dash_config.resolve_language("kannada", settings) == "hindi"
        assert dash_config.resolve_language(None, settings) == "hindi"

    def test_unsupported_language_generates_english_pack(self, settings):
        settings.groq_api_key = None
        settings.default_outreach_language = "english"
        pack = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="kannada"), settings
        )
        assert pack["language"] == "english"
        assert pack["language_setting"] == "kannada"
        assert pack["voice_call_script"]["opening_line"].startswith("Hi ")

    def test_english_lead_never_gets_hindi(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="english"), settings
        )
        assert pack["language"] == "english"
        assert "नमस्ते" not in combined_text(pack)

    def test_llm_prompt_targets_lead_language(self, settings):
        hindi_prompt = outreach.build_prompt(base_case(), base_lead(language="hindi"), settings)
        assert "<language>\nhindi\n</language>" in hindi_prompt
        assert "Devanagari" in hindi_prompt
        english_prompt = outreach.build_prompt(base_case(), base_lead(language="en"), settings)
        assert "<language>\nenglish\n</language>" in english_prompt

    def test_names_and_reference_values_preserved_verbatim(self, settings):
        settings.groq_api_key = None
        lead = base_lead(name="श्रुति देसाई", language="hi")
        pack = dash_config.deterministic_outreach_pack(base_case(), lead, settings)
        opening = pack["voice_call_script"]["opening_line"]
        assert "श्रुति" in opening
        assert "देसाई" not in opening  # first name only, unaltered
        assert "lead_test00000000001" in opening

    def test_generated_hindi_draft_kept_and_aligned(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["voice_call_script"]["opening_line"] = "नमस्ते Priya, पूछताछ के संबंध में बात करनी थी।"
        model_pack["messages"]["whatsapp"]["body"] = "नमस्ते Priya, पूछताछ के संबंध में।"
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(
            base_case(), base_lead(language="hindi"), settings
        )
        assert pack["generated_by"]["engine"].startswith("groq:")
        assert pack["voice_call_script"]["opening_line"].startswith("नमस्ते")
        assert pack["quick_copy"]["opening_line"] == pack["voice_call_script"]["opening_line"]
        assert pack["quick_copy"]["whatsapp"] == pack["messages"]["whatsapp"]["body"]


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------


class TestAgentIdentity:
    def test_default_identity_is_neutral_team_label(self, settings):
        settings.groq_api_key = None
        assert dash_config.agent_identity(settings) == "Callora Team"
        pack = dash_config.deterministic_outreach_pack(base_case(), base_lead(), settings)
        assert "Callora Team" in pack["messages"]["email"]["body"]
        assert list(dash_config.iter_agent_placeholders(pack)) == []

    def test_configured_identity_used_in_signatures(self, settings):
        settings.groq_api_key = None
        settings.agent_display_name = "Aarav Sharma"
        pack_en = dash_config.deterministic_outreach_pack(base_case(), base_lead(), settings)
        assert "Aarav Sharma" in pack_en["messages"]["email"]["body"]
        pack_hi = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="hindi"), settings
        )
        assert "Aarav Sharma" in pack_hi["messages"]["email"]["body"]

    def test_missing_agent_identity_leaves_no_placeholder(self, settings, monkeypatch):
        enable_llm(settings)
        settings.agent_display_name = None
        model_pack = valid_llm_pack()
        model_pack["voice_call_script"]["opening_line"] = "Hello Priya, [Agent Name] here."
        model_pack["messages"]["email"]["body"] = "Regards,\n[Agent Name]\n"
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert list(dash_config.iter_agent_placeholders(pack)) == []
        assert "[Agent Name]" not in "\n".join(all_strings(pack))
        # Neutral fallback signer used instead.
        assert "Callora Team" in pack["messages"]["email"]["body"]

    def test_model_placeholders_replaced_with_configured_identity(self, settings, monkeypatch):
        enable_llm(settings)
        settings.agent_display_name = "Aarav Sharma"
        model_pack = valid_llm_pack()
        model_pack["messages"]["email"]["body"] = "Regards,\n{{agent_name}}\n"
        model_pack["messages"]["whatsapp"]["body"] = "Hello Priya — [Your Name]"
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        combined = "\n".join(all_strings(pack))
        assert "[Your Name]" not in combined
        assert "{{agent_name}}" not in combined
        assert "{Aarav Sharma}" not in combined
        assert "Aarav Sharma" in pack["messages"]["email"]["body"]


# ---------------------------------------------------------------------------
# Factual grounding
# ---------------------------------------------------------------------------


class TestFactualGrounding:
    def test_property_and_offer_claims_rejected(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["messages"]["whatsapp"]["body"] = (
            "Hello Priya — our curated listings are ready and a 10% discount is guaranteed!"
        )
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        text = lead_facing_text(pack)
        assert "discount" not in text
        assert "curated" not in text
        assert "guaranteed" not in text
        assert "ready" not in text

    def test_prior_conversation_claims_rejected(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["voice_call_script"]["opening_line"] = (
            "Hi Priya, as we discussed earlier — we called you yesterday about the flat."
        )
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        text = lead_facing_text(pack)
        assert "as we discussed" not in text
        assert "called you" not in text

    def test_delivery_claims_rejected(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["messages"]["sms"]["body"] = "Hi Priya, we have sent you the details."
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        assert "we have sent" not in lead_facing_text(pack)

    def test_claim_allowed_when_source_data_explicitly_confirms(self, settings, monkeypatch):
        enable_llm(settings)
        lead = base_lead(custom_fields={"discount": "10% festive discount confirmed by sales"})
        model_pack = valid_llm_pack()
        model_pack["messages"]["whatsapp"]["body"] = (
            "Hello Priya — a 10% discount is confirmed for this enquiry."
        )
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), lead, settings)
        assert pack["generated_by"]["engine"].startswith("groq:")
        assert "10% discount" in pack["messages"]["whatsapp"]["body"]

    def test_valid_pack_accepted(self, settings, monkeypatch):
        enable_llm(settings)
        install_fake_groq(monkeypatch, json.dumps(valid_llm_pack()))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        model = settings.groq_model
        assert pack["generated_by"] == {"engine": f"groq:{model}", "model": model}
        assert (
            pack["voice_call_script"]["opening_line"]
            == "Hello Priya, regarding your enquiry (ref lead_test00000000001)."
        )
        assert pack["messages"]["sms"]["body"].startswith("Hello Priya")

    def test_prompt_separates_verified_and_missing_fields(self, settings):
        lead = base_lead(email=None, last_contact_at=None, deal_value=None)
        prompt = outreach.build_prompt(base_case(), lead, settings)
        assert "<verified_lead_fields>" in prompt
        assert "<missing_or_unverified_fields>" in prompt
        missing = json.loads(
            prompt.split("<missing_or_unverified_fields>")[1].split("</missing_or_unverified_fields>")[0]
        )
        assert "email" in missing
        assert "last_contact_at" in missing
        assert "deal_value" in missing
        verified = json.loads(
            prompt.split("<verified_lead_fields>")[1].split("</verified_lead_fields>")[0]
        )
        assert "email" not in verified
        assert verified["phone"] == "9876543211"

    def test_prompt_contact_evidence_gates_contact_claims(self, settings):
        prompt = outreach.build_prompt(base_case(), base_lead(), settings)
        assert "<contact_evidence>" in prompt
        assert "NO evidence of prior contact" in prompt
        assert "'never_contacted' is present" in prompt

    def test_prompt_crm_evidence_requires_confirmed_lookup(self, settings):
        no_signal = outreach.build_prompt(base_case(), base_lead(), settings)
        assert "No confirmed CRM lookup result" in no_signal
        drift_case = base_case(
            signals=[
                {
                    "signal": "crm_sync_drift",
                    "severity": "medium",
                    "detail": "lead not present in external CRM",
                }
            ]
        )
        with_drift = outreach.build_prompt(drift_case, base_lead(), settings)
        assert "confirmed this lead is absent from the external CRM" in with_drift

    def test_deterministic_pack_never_asserts_crm_state(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(base_case(), base_lead(), settings)
        assert "crm" not in combined_text(pack)

    def test_unverified_fields_are_never_invented(self, settings):
        settings.groq_api_key = None
        lead = base_lead(email=None, deal_value=None)
        pack = dash_config.deterministic_outreach_pack(base_case(), lead, settings)
        assert pack["lead"]["email"] is None
        combined = combined_text(pack)
        assert "@example.com" not in combined
        # A missing name degrades to a neutral salutation, not an invented one.
        nameless = dash_config.deterministic_outreach_pack(
            base_case(lead_name=None), base_lead(name=None), settings
        )
        assert "there" in nameless["voice_call_script"]["opening_line"]


# ---------------------------------------------------------------------------
# Channel behaviour
# ---------------------------------------------------------------------------


class TestChannelBehaviour:
    def test_routed_channel_wins_as_preferred(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(
            base_case(channel="whatsapp"), base_lead(language="english"), settings
        )
        plan = pack["channel_plan"]
        assert plan["preferred_channel"] == "whatsapp"
        assert plan["alternative_channels"] == ["sms", "email"]
        assert plan["delivery_status"] == "draft_only"

    def test_phone_route_maps_to_voice_script(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(base_case(channel="phone"), base_lead(), settings)
        assert pack["channel_plan"]["preferred_channel"] == "voice_call"

    def test_default_preferred_channel_by_language(self, settings):
        settings.groq_api_key = None
        hi = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="hindi"), settings
        )
        en = dash_config.deterministic_outreach_pack(
            base_case(), base_lead(language="english"), settings
        )
        assert hi["channel_plan"]["preferred_channel"] == "whatsapp"
        assert en["channel_plan"]["preferred_channel"] == "sms"

    def test_all_draft_channels_always_generated(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(base_case(), base_lead(), settings)
        for channel in ("whatsapp", "sms", "email"):
            assert pack["messages"][channel]["body"]

    def test_quick_copy_mirrors_drafts(self, settings):
        settings.groq_api_key = None
        pack = dash_config.deterministic_outreach_pack(base_case(), base_lead(), settings)
        assert pack["quick_copy"]["opening_line"] == pack["voice_call_script"]["opening_line"]
        assert pack["quick_copy"]["whatsapp"] == pack["messages"]["whatsapp"]["body"]
        assert pack["quick_copy"]["sms"] == pack["messages"]["sms"]["body"]

    def test_quick_copy_rederived_from_generated_drafts(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        # Deliberately inconsistent model quick-copy: drafts must win.
        model_pack["quick_copy"] = {"whatsapp": "DRIFTED", "sms": "DRIFTED", "opening_line": "DRIFTED"}
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["quick_copy"]["whatsapp"] == pack["messages"]["whatsapp"]["body"]
        assert pack["quick_copy"]["sms"] == pack["messages"]["sms"]["body"]
        assert pack["quick_copy"]["opening_line"] == pack["voice_call_script"]["opening_line"]

    def test_channels_share_same_verified_context(self, settings, monkeypatch):
        enable_llm(settings)
        install_fake_groq(monkeypatch, json.dumps(valid_llm_pack()))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        for channel in ("whatsapp", "sms", "email"):
            assert "lead_test00000000001" in pack["messages"][channel]["body"]


# ---------------------------------------------------------------------------
# Structured output validation and malformed responses
# ---------------------------------------------------------------------------


class TestOutputValidation:
    def test_malformed_json_falls_back(self, settings, monkeypatch):
        enable_llm(settings)
        state = install_fake_groq(monkeypatch, "sorry, no json here")
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        assert len(state["calls"]) == 2  # JSON-mode attempt + plain retry

    def test_missing_script_falls_back(self, settings, monkeypatch):
        enable_llm(settings)
        install_fake_groq(monkeypatch, json.dumps({"messages": {"sms": {"body": "hi"}}}))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"

    def test_incomplete_responses_fall_back(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        del model_pack["voice_call_script"]["responses"]["no_answer"]
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"

    def test_non_string_message_body_falls_back(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["messages"]["sms"]["body"] = 12345
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"

    def test_no_usable_channel_falls_back(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack()
        model_pack["messages"] = {"email": {"subject": "s", "body": "   "}}
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"

    def test_client_exception_falls_back_after_retries(self, settings, monkeypatch):
        enable_llm(settings)
        state = install_fake_groq(monkeypatch, RuntimeError("groq down"))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        assert len(state["calls"]) == 2

    def test_failed_output_is_not_blended_into_content(self, settings, monkeypatch):
        enable_llm(settings)
        install_fake_groq(monkeypatch, "{not valid json")
        case, lead = base_case(), base_lead()
        pack = outreach.generate_outreach_pack(case, lead, settings)
        template = dash_config.deterministic_outreach_pack(case, lead, settings)
        assert pack == template  # pure verified-data fallback, no model residue

    def test_no_llm_call_without_key(self, settings, monkeypatch):
        settings.groq_api_key = None
        state = install_fake_groq(monkeypatch, json.dumps(valid_llm_pack()))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"
        assert state["clients"] == []

    def test_no_llm_call_when_package_unavailable(self, settings, monkeypatch):
        settings.groq_api_key = "gsk_test_offline"
        monkeypatch.setattr(strategist_module, "Groq", None)
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        assert pack["generated_by"]["engine"] == "rules-fallback"


# ---------------------------------------------------------------------------
# Drafts only — no sending, no calling
# ---------------------------------------------------------------------------


class TestNoSendingOrCalling:
    def test_generate_records_only_content_generated(self, client, db, swept_case):
        response = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        )
        assert response.status_code == 200
        outcomes = client.get(
            f"/api/v1/agent/recovery/cases/{swept_case}/outcomes", headers=auth_headers()
        ).json()
        assert outcomes["has_execution_data"] is False
        assert outcomes["events"]
        for event in outcomes["events"]:
            assert event["event_type"] == "content_generated"

    def test_generated_content_is_labeled_draft(self, client, db, swept_case):
        response = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        )
        content = response.json()["content"]
        assert "draft" in content["disclaimer"].lower()
        assert "does not indicate a call was made or a message was sent" in content["disclaimer"]
        assert content["channel_plan"]["delivery_status"] == "draft_only"

    def test_llm_generation_also_records_no_execution(self, client, db, settings, monkeypatch, swept_case):
        enable_llm(settings)
        install_fake_groq(monkeypatch, json.dumps(valid_llm_pack()))
        response = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        )
        assert response.status_code == 200
        assert response.json()["content"]["generated_by"]["engine"].startswith("groq:")
        outcomes = client.get(
            f"/api/v1/agent/recovery/cases/{swept_case}/outcomes", headers=auth_headers()
        ).json()
        assert outcomes["has_execution_data"] is False
        assert all(e["event_type"] == "content_generated" for e in outcomes["events"])


# ---------------------------------------------------------------------------
# Backward compatibility of the API responses
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_response_envelope_unchanged(self, client, db, swept_case):
        response = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        )
        body = response.json()
        assert body["case_id"] == swept_case
        assert body["source"] == "generated"
        for key in (
            "generated_at",
            "regenerated_at",
            "generation_count",
            "engine",
            "model",
            "content",
        ):
            assert key in body
        assert body["engine"] == "rules-fallback"  # no key in tests

    def test_content_superset_of_previous_shape(self, client, db, swept_case):
        content = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        ).json()["content"]
        for key in (
            "version",
            "generated_by",
            "language",
            "lead",
            "case",
            "recovery_rationale",
            "context_used",
            "voice_call_script",
            "messages",
            "quick_copy",
            "disclaimer",
            # backward-compatible extensions
            "channel_plan",
            "language_setting",
        ):
            assert key in content
        script = content["voice_call_script"]
        assert script["opening_line"]
        assert set(script["responses"]) == {
            "interested",
            "busy",
            "callback_requested",
            "not_interested",
            "no_answer",
        }
        for channel in ("whatsapp", "sms", "email"):
            assert isinstance(content["messages"][channel]["body"], str)
        assert isinstance(content["messages"]["email"]["subject"], str)
        assert set(content["quick_copy"]) == {"opening_line", "whatsapp", "sms"}

    def test_cached_read_after_generate(self, client, db, swept_case):
        generated = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        ).json()
        cached = client.get(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content", headers=auth_headers()
        ).json()
        assert cached["source"] == "cached"
        assert cached["content"] == generated["content"]
        assert cached["generation_count"] == 1

    def test_regeneration_increments_generation_count(self, client, db, swept_case):
        url = f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate"
        first = client.post(url, headers=auth_headers()).json()
        second = client.post(url, headers=auth_headers()).json()
        assert first["generation_count"] == 1
        assert first["regenerated_at"] is None
        assert second["generation_count"] == 2
        assert second["regenerated_at"] is not None

    def test_verified_case_rationale_is_preserved(self, client, db, swept_case):
        case = client.get(
            f"/api/v1/agent/recovery/cases/{swept_case}", headers=auth_headers()
        ).json()
        content = client.post(
            f"/api/v1/agent/recovery/cases/{swept_case}/outreach-content/generate",
            headers=auth_headers(),
        ).json()["content"]
        assert content["recovery_rationale"] == case["rationale"]

    def test_model_rationale_never_replaces_verified_rationale(self, settings, monkeypatch):
        enable_llm(settings)
        model_pack = valid_llm_pack(
            recovery_rationale="The customer promised to buy two flats tomorrow."
        )
        install_fake_groq(monkeypatch, json.dumps(model_pack))
        pack = outreach.generate_outreach_pack(base_case(), base_lead(), settings)
        # Verified rationale stays authoritative; the model's text is kept
        # separately for transparency only.
        assert pack["recovery_rationale"] == base_case()["rationale"]
        assert pack["context_used"]["model_rationale"].startswith("The customer promised")
