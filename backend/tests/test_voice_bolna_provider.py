"""Tests for the Bolna adapter and the optional caller number.

Bolna's ``/call`` endpoint needs only an agent id and a recipient: the
caller number (``from_phone_number``) is optional. When it is omitted,
Bolna places the call itself, through the telephony provider configured
on the agent (Vobiz, Twilio, Plivo, ...). Callora therefore never
*requires* a default caller number and never sends an empty one.

The tests cover the whole path — configuration, orchestration and the
request payload — with mocked provider responses. Nothing here reaches
the network and no call is ever placed.
"""

import json
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db.session import reset_engine
from app.main import app
from app.modules.voice.errors import ProviderError
from app.modules.voice.orchestrator import CallOrchestrator, CallRequest
from app.modules.voice.providers.base import OutboundCallRequest
from app.modules.voice.providers.bolna import BolnaProvider
from tests.conftest import VOICE_BIZ, VOICE_LEAD_ID, auth_headers
from tests.fakes import FakeProvider

CALLS = "/api/v1/voice/calls"

#: The message Bolna returns when the number it was given is unknown to
#: the telephony provider of the agent.
VOBIZ_REJECTION = (
    "Calling from_number 8071580188 doesn't exist for vobiz. "
    "Please check your agent telephony provider."
)


class _Recorder:
    """A mocked Bolna HTTP endpoint that records the request bodies."""

    def __init__(self, payload=None, status_code: int = 200) -> None:
        self.bodies = []
        self.status_code = status_code
        self.payload = payload or {"execution_id": "exec_1", "status": "queued"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(self.status_code, json=self.payload)

    @property
    def body(self):
        return self.bodies[-1]


def _provider(settings, recorder: _Recorder) -> BolnaProvider:
    """A Bolna adapter whose HTTP client never leaves the process."""
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    return BolnaProvider(settings, client=client)


def _use_bolna(monkeypatch, recorder: _Recorder) -> None:
    """Route the provider of the orchestrator to a mocked endpoint."""
    monkeypatch.setattr(
        "app.modules.voice.orchestrator.build_provider",
        lambda settings: _provider(settings, recorder),
    )


def _request(**kwargs) -> OutboundCallRequest:
    params = dict(to_number="+919876543210", agent_id="agent_test")
    params.update(kwargs)
    return OutboundCallRequest(**params)


# ---------------------------------------------------------------------------
# The request payload
# ---------------------------------------------------------------------------


class TestBolnaCallPayload:
    def test_without_a_caller_number_the_field_is_omitted(self, voice_settings):
        """No caller number: the field is left out of the payload entirely."""
        recorder = _Recorder()
        provider = _provider(voice_settings, recorder)
        provider.start_call(_request())
        assert "from_phone_number" not in recorder.body
        assert recorder.body["agent_id"] == "agent_test"
        assert recorder.body["recipient_phone_number"] == "+919876543210"

    def test_an_explicit_caller_number_is_sent(self, voice_settings):
        """A configured caller number is sent in Bolna's field."""
        recorder = _Recorder()
        provider = _provider(voice_settings, recorder)
        provider.start_call(_request(from_number="+918071580188"))
        assert recorder.body["from_phone_number"] == "+918071580188"

    def test_a_blank_caller_number_is_omitted(self, voice_settings):
        """An empty string is never sent — the provider would reject it."""
        recorder = _Recorder()
        provider = _provider(voice_settings, recorder)
        provider.start_call(_request(from_number="   "))
        assert "from_phone_number" not in recorder.body

    def test_a_null_caller_number_is_omitted(self, voice_settings):
        recorder = _Recorder()
        provider = _provider(voice_settings, recorder)
        provider.start_call(_request(from_number=None))
        assert "from_phone_number" not in recorder.body

    def test_the_call_is_accepted_without_a_caller_number(self, voice_settings):
        """The mocked provider answers the two-field payload as usual."""
        recorder = _Recorder()
        provider = _provider(voice_settings, recorder)
        result = provider.start_call(_request())
        assert result.execution_id == "exec_1"
        assert result.status == "queued"


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


class TestProviderRejection:
    def test_the_provider_message_is_preserved(self, voice_settings):
        """A rejection is reported with the provider's own words."""
        recorder = _Recorder(
            payload={"error": "invalid_request", "message": VOBIZ_REJECTION},
            status_code=400,
        )
        provider = _provider(voice_settings, recorder)
        with pytest.raises(ProviderError) as excinfo:
            provider.start_call(_request(from_number="8071580188"))
        assert VOBIZ_REJECTION in str(excinfo.value)
        assert excinfo.value.status_code == 502

    def test_a_rejected_call_is_not_retried(self, voice_settings):
        """A provider error is never retried with another number."""
        recorder = _Recorder(
            payload={"error": "invalid_request", "message": VOBIZ_REJECTION},
            status_code=400,
        )
        provider = _provider(voice_settings, recorder)
        with pytest.raises(ProviderError):
            provider.start_call(_request(from_number="8071580188"))
        assert len(recorder.bodies) == 1


# ---------------------------------------------------------------------------
# The resolution of the caller number
# ---------------------------------------------------------------------------


@pytest.fixture()
def live(voice_settings, db, voice_business, voice_lead, voice_consent):
    """An orchestrator that is allowed to place calls (fake provider)."""
    voice_settings.voice_enable_outbound_calls = True
    provider = FakeProvider(execution_id="exec_123", status="queued")
    return CallOrchestrator(voice_settings, db, provider=provider), provider


def _initiate(live, **kwargs):
    orchestrator, provider = live
    params = dict(business_id=VOICE_BIZ, lead_id=VOICE_LEAD_ID)
    params.update(kwargs)
    result = orchestrator.initiate(CallRequest(**params))
    return result, provider


class TestCallerNumberResolution:
    def test_the_number_of_the_request_wins(self, live):
        """An explicitly supplied caller number is used."""
        _, provider = _initiate(live, from_number="+919999999999")
        assert provider.start_calls[0].from_number == "+919999999999"

    def test_no_business_level_fallback(self, live, voice_business, db):
        """No hidden business-level fallback injects a caller number."""
        voice_business.bolna_from_number = "+918888888888"
        db.commit()
        result, provider = _initiate(live)
        assert provider.start_calls[0].from_number is None
        assert result.call.from_number is None

    def test_no_deployment_default_fallback(self, live, voice_business, voice_settings, db):
        """No hidden deployment-level fallback injects a caller number."""
        voice_settings.bolna_default_from_number = "+917777777777"
        result, provider = _initiate(live)
        assert provider.start_calls[0].from_number is None
        assert result.call.from_number is None

    def test_nothing_configured_means_no_number(self, live, db):
        """No caller number anywhere: the call carries none."""
        result, provider = _initiate(live)
        assert provider.start_calls[0].from_number is None
        assert result.call.from_number is None

    def test_the_number_is_normalised_to_e164(self, live):
        """Bolna wants E.164 — a national number gets its country code."""
        _, provider = _initiate(live, from_number=" 8071580188 ")
        assert provider.start_calls[0].from_number == "+918071580188"

    def test_an_unusable_number_is_ignored(self, live):
        """A value without digits is not a number and is never sent."""
        _, provider = _initiate(live, from_number="null")
        assert provider.start_calls[0].from_number is None

    def test_the_stored_call_carries_the_number(self, live):
        result, _provider = _initiate(live, from_number="+919999999999")
        assert result.call.from_number == "+919999999999"


# ---------------------------------------------------------------------------
# The whole stack (API -> orchestrator -> Bolna payload)
# ---------------------------------------------------------------------------


class TestCallApi:
    """The API path, with the provider replaced by a mocked endpoint."""

    @pytest.fixture()
    def bolna(self, monkeypatch, voice_settings):
        """Outbound calling is on and the provider is a mocked endpoint."""
        voice_settings.voice_enable_outbound_calls = True
        recorder = _Recorder()
        _use_bolna(monkeypatch, recorder)
        return recorder

    def test_a_call_without_a_caller_number_reaches_bolna(
        self, bolna, voice_business, voice_lead, voice_consent, voice_client, db, voice_settings
    ):
        """No caller number supplied: Bolna is asked to place the call."""
        response = voice_client.post(
            CALLS, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        assert response.status_code == 201, response.text
        assert len(bolna.bodies) == 1
        assert "from_phone_number" not in bolna.body
        assert bolna.body["recipient_phone_number"] == "+919876543210"

    def test_a_call_with_a_caller_number_reaches_bolna(
        self, bolna, voice_business, voice_lead, voice_consent, voice_client, db
    ):
        """An explicitly supplied caller number is sent in Bolna's field."""
        response = voice_client.post(
            CALLS,
            json={"lead_id": VOICE_LEAD_ID, "from_number": "+918071580188"},
            headers=auth_headers(),
        )
        assert response.status_code == 201, response.text
        assert bolna.body["from_phone_number"] == "+918071580188"

    def test_a_rejected_call_is_reported_as_a_provider_error(
        self, monkeypatch, voice_settings, voice_business, voice_lead, voice_consent, voice_client
    ):
        """A rejection surfaces with the provider's own message."""
        voice_settings.voice_enable_outbound_calls = True
        recorder = _Recorder(
            payload={"error": "invalid_request", "message": VOBIZ_REJECTION},
            status_code=400,
        )
        _use_bolna(monkeypatch, recorder)
        response = voice_client.post(
            CALLS, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        assert response.status_code == 502
        assert VOBIZ_REJECTION in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestCallerNumberConfiguration:
    def test_an_unset_default_is_none(self, monkeypatch):
        monkeypatch.delenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", raising=False)
        assert Settings(_env_file=None).bolna_default_from_number is None

    def test_an_empty_default_is_none(self, monkeypatch):
        """An empty environment variable is "not configured", not ""."""
        monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", "")
        assert Settings(_env_file=None).bolna_default_from_number is None

    def test_a_blank_default_is_none(self, monkeypatch):
        monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", "   ")
        assert Settings(_env_file=None).bolna_default_from_number is None

    def test_a_configured_default_is_kept(self, monkeypatch):
        monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", " +918071580188 ")
        assert Settings(_env_file=None).bolna_default_from_number == "+918071580188"

    def test_the_application_starts_without_a_default_caller_number(
        self, schema_template, tmp_path, monkeypatch
    ):
        """An empty configuration must not break the startup."""
        db_path = tmp_path / "startup.db"
        shutil.copyfile(schema_template, db_path)
        monkeypatch.setenv("CALLORA_DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", "")
        get_settings.cache_clear()
        reset_engine()
        try:
            with TestClient(app) as client:
                assert client.get("/health").status_code == 200
                assert get_settings().bolna_default_from_number is None
        finally:
            get_settings.cache_clear()
            reset_engine()
