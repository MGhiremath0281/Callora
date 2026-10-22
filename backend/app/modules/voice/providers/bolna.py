"""Bolna adapter — the initial (and only active) voice provider.

Bolna exposes a small REST API:

    POST /call                  place an outbound call      -> execution_id
    GET  /executions/{id}       read the execution state
    POST /call/{id}/stop        cancel a queued/scheduled call

and pushes the execution payload to the agent's webhook URL whenever the
call status changes.

The adapter is deliberately thin: it translates between Callora's
provider-agnostic types and Bolna's JSON, and nothing else. All business
rules live in the orchestrator and the compliance gate.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

import httpx

from app.config import Settings
from app.modules.voice.constants import CallStatus
from app.modules.voice.errors import (
    ProviderConnectionError,
    ProviderError,
    ProviderExecutionNotFoundError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
)
from app.modules.voice.providers.base import (
    OutboundCallRequest,
    ProviderCallResult,
    ProviderEvent,
    VoiceProvider,
    event_fingerprint,
)

logger = logging.getLogger("callora.voice.providers.bolna")

#: Header names that may carry a webhook signature. Bolna does not
#: document an HMAC scheme for execution webhooks, but a shared secret can
#: be attached as a custom header on the agent's webhook configuration.
SIGNATURE_HEADERS = (
    "x-bolna-signature",
    "x-webhook-signature",
    "x-hub-signature-256",
    "x-signature",
)

#: Keys that may carry the execution id, in the order they are trusted.
#: Bolna uses ``execution_id`` in the ``POST /call`` answer and ``id`` in
#: the execution document it pushes to the webhook.
EXECUTION_ID_KEYS = ("execution_id", "id", "executionId", "call_id")

#: Keys that may wrap the execution document in a larger payload.
NESTED_PAYLOAD_KEYS = ("data", "execution", "payload", "body")


def extract_execution_id(payload: Mapping[str, Any]) -> Optional[str]:
    """Find the execution id in a Bolna document.

    Bolna is not consistent about the field name: the ``POST /call``
    answer carries ``execution_id`` while the execution document (the
    answer of ``GET /executions/{id}`` and the webhook payload) carries
    ``id``. Some payloads wrap the document in ``data``. Every known
    spelling is accepted so that a document is never dropped because of
    a field name.
    """
    for key in EXECUTION_ID_KEYS:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    for key in NESTED_PAYLOAD_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            found = extract_execution_id(nested)
            if found:
                return found
    return None


class BolnaProvider(VoiceProvider):
    """Talks to the Bolna voice API."""

    name = "bolna"

    def __init__(
        self,
        settings: Settings,
        client: Optional[httpx.Client] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._api_key = api_key if api_key is not None else settings.bolna_api_key
        self._base_url = settings.bolna_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=settings.bolna_timeout_seconds,
            headers={"Accept": "application/json"},
        )

    # -- configuration -----------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _require_configured(self) -> None:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "the Bolna provider is not configured; set CALLORA_BOLNA_API_KEY"
            )

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # -- HTTP --------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        self._require_configured()
        url = f"{self._base_url}{path}"
        try:
            response = self._client.request(
                method, url, headers=self._headers, **kwargs
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Bolna did not answer in time ({path})"
            ) from exc
        except httpx.ConnectError as exc:
            # The request never reached Bolna, so no call was placed and
            # the caller may safely retry.
            raise ProviderConnectionError(
                f"could not reach Bolna ({path}): {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Bolna request failed ({path}): {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:500]
            raise ProviderError(
                f"Bolna returned HTTP {response.status_code} for {path}: {detail}",
                provider_status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Bolna returned a non-JSON response for {path}") from exc

    # -- VoiceProvider -----------------------------------------------------

    def start_call(self, request: OutboundCallRequest) -> ProviderCallResult:
        """Place an outbound call.

        The ``user_data`` map carries the lead context and the business'
        qualification questions into the agent prompt, and ``agent_data``
        selects the language the conversation starts in.
        """
        body: Dict[str, Any] = {
            "agent_id": request.agent_id,
            "recipient_phone_number": request.to_number,
        }
        # ``from_phone_number`` is optional in Bolna's API: only the agent
        # and the recipient are required. When it is omitted Bolna uses
        # the account's default number and places the call through the
        # telephony provider configured on the agent (Vobiz, Twilio,
        # Plivo, ...). An empty value is never sent: the provider would
        # reject it. A number that is sent must be E.164 and must exist
        # on the provider account, otherwise Bolna answers with
        # "Calling from_number ... doesn't exist for <provider>".
        from_number = (request.from_number or "").strip()
        if from_number:
            body["from_phone_number"] = from_number
        if request.scheduled_at:
            body["scheduled_at"] = _iso_offset(request.scheduled_at)
        if request.user_data:
            body["user_data"] = request.user_data
        agent_data = dict(request.agent_data or {})
        if request.language and "language" not in agent_data:
            agent_data["language"] = request.language
        if agent_data:
            body["agent_data"] = agent_data

        raw = self._request("POST", "/call", json=body)
        execution_id = extract_execution_id(raw)
        if not execution_id:
            raise ProviderError("Bolna accepted the call but returned no execution_id")
        status = self.to_internal_status(raw.get("status") or "queued")
        if status == CallStatus.UNKNOWN:
            # The provider accepted the call but reported a status
            # Callora does not know. "Queued" is the safe assumption: the
            # call exists and has not been dialled yet.
            status = CallStatus.QUEUED
        logger.info(
            "bolna_call_placed",
            extra={"execution_id": execution_id, "status": status},
        )
        return ProviderCallResult(
            provider=self.name,
            execution_id=str(execution_id),
            status=status,
            raw=raw,
        )

    def get_call(self, execution_id: str) -> ProviderCallResult:
        """Read the authoritative state of one execution.

        A ``404`` means Bolna does not know the execution any more. That
        is a confirmation, not a timeout, so it is reported as such.
        """
        try:
            raw = self._request("GET", f"/executions/{execution_id}")
        except ProviderError as exc:
            if exc.provider_status_code == 404:
                raise ProviderExecutionNotFoundError(
                    f"Bolna does not know the execution {execution_id}",
                    provider_status_code=404,
                ) from exc
            raise
        return ProviderCallResult(
            provider=self.name,
            execution_id=str(raw.get("id") or execution_id),
            status=self.to_internal_status(raw.get("status")),
            raw=raw,
            error_message=raw.get("error_message"),
        )

    def stop_call(self, execution_id: str) -> ProviderCallResult:
        raw = self._request("POST", f"/call/{execution_id}/stop")
        return ProviderCallResult(
            provider=self.name,
            execution_id=str(raw.get("execution_id") or execution_id),
            status=self.to_internal_status(raw.get("status") or "stopped"),
            raw=raw,
        )

    def normalize_event(
        self, payload: Mapping[str, Any], headers: Optional[Mapping[str, str]] = None
    ) -> ProviderEvent:
        """Normalize a Bolna execution payload into a provider event."""
        data: Dict[str, Any] = dict(payload)
        execution_id = extract_execution_id(data)
        provider_status = data.get("status")
        key = event_fingerprint(self.name, execution_id, provider_status, data)
        return ProviderEvent(
            provider=self.name,
            event_key=key,
            event_type="execution.status",
            execution_id=str(execution_id) if execution_id is not None else None,
            status=str(provider_status) if provider_status is not None else None,
            payload=data,
        )

    def verify_signature(
        self, headers: Optional[Mapping[str, str]], raw_body: bytes
    ) -> Optional[bool]:
        """Verify an HMAC-SHA256 signature when a shared secret is set.

        Bolna does not document a signature scheme for execution webhooks.
        When ``CALLORA_BOLNA_WEBHOOK_SECRET`` is configured, a signature
        header is expected and verified; without a secret the provider
        cannot be verified cryptographically and ``None`` is returned.
        """
        secret = self._settings.bolna_webhook_secret
        if not secret:
            return None
        if not headers:
            return False
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        provided = None
        for header in SIGNATURE_HEADERS:
            if header in lowered:
                provided = lowered[header]
                break
        if not provided:
            return False
        provided = provided.strip()
        if provided.lower().startswith("sha256="):
            provided = provided[7:]
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, provided.lower())

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def __enter__(self) -> "BolnaProvider":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def _iso_offset(value: datetime) -> str:
    """Format a datetime the way Bolna expects it.

    Bolna rejects a trailing ``Z``; it wants an explicit numeric offset, so
    UTC is rendered as ``+00:00``.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")
