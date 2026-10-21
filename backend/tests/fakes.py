"""Deterministic test doubles for the voice module.

Nothing in the test suite talks to Bolna, to Redis or to an LLM. The
doubles below implement the same ports the production code uses, so the
tests exercise the real orchestration, compliance and webhook logic
without spending money or reaching the network.
"""

from typing import Any, Dict, List, Mapping, Optional

from app.modules.voice.errors import ProviderConnectionError, ProviderError
from app.modules.voice.postcall.llm import LLMClient
from app.modules.voice.providers.base import (
    OutboundCallRequest,
    ProviderCallResult,
    ProviderEvent,
    VoiceProvider,
    event_fingerprint,
)


class FakeProvider(VoiceProvider):
    """An in-memory voice provider.

    ``fail_with`` makes the next calls raise, which is how the retry and
    failure paths are exercised. ``start_calls`` records every request
    that reached the provider, so a test can assert that a blocked call
    never produced one.
    """

    name = "fake"

    def __init__(
        self,
        configured: bool = True,
        fail_with: Optional[Exception] = None,
        fail_times: int = 0,
        execution_id: str = "exec_fake_1",
        status: str = "queued",
    ) -> None:
        self.configured = configured
        self.start_calls: List[OutboundCallRequest] = []
        self.get_calls: List[str] = []
        self.stop_calls: List[str] = []
        self._fail_with = fail_with
        self._fail_times = fail_times
        self._execution_id = execution_id
        self._status = status
        self.closed = False

    def is_configured(self) -> bool:
        return self.configured

    def _maybe_fail(self) -> None:
        if self._fail_with is None:
            return
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._fail_with
        raise self._fail_with

    def start_call(self, request: OutboundCallRequest) -> ProviderCallResult:
        self.start_calls.append(request)
        self._maybe_fail()
        return ProviderCallResult(
            provider=self.name,
            execution_id=self._execution_id,
            status=self._status,
            raw={"execution_id": self._execution_id, "status": self._status},
        )

    def get_call(self, execution_id: str) -> ProviderCallResult:
        self.get_calls.append(execution_id)
        self._maybe_fail()
        return ProviderCallResult(
            provider=self.name,
            execution_id=execution_id,
            status=self._status,
            raw={"id": execution_id, "status": self._status},
        )

    def stop_call(self, execution_id: str) -> ProviderCallResult:
        self.stop_calls.append(execution_id)
        self._maybe_fail()
        return ProviderCallResult(
            provider=self.name,
            execution_id=execution_id,
            status="stopped",
            raw={"execution_id": execution_id, "status": "stopped"},
        )

    def normalize_event(
        self, payload: Mapping[str, Any], headers: Optional[Mapping[str, str]] = None
    ) -> ProviderEvent:
        data: Dict[str, Any] = dict(payload)
        execution_id = data.get("id") or data.get("execution_id")
        status = data.get("status")
        return ProviderEvent(
            provider=self.name,
            event_key=event_fingerprint(self.name, execution_id, status, data),
            event_type="execution.status",
            execution_id=str(execution_id) if execution_id is not None else None,
            status=str(status) if status is not None else None,
            payload=data,
        )

    def verify_signature(
        self, headers: Optional[Mapping[str, str]], raw_body: bytes
    ) -> Optional[bool]:
        return None

    def close(self) -> None:
        self.closed = True


class ScriptedLLM(LLMClient):
    """An LLM double that answers from a script.

    The script maps a substring of the prompt to the JSON object the
    "model" returns, which keeps the tests deterministic while still
    exercising the real prompt-building and validation code.
    """

    model = "scripted-model"

    def __init__(self, script: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.script = script or {}
        self.calls: List[Dict[str, str]] = []
        self.default: Optional[Dict[str, Any]] = None
        self.raises: Optional[Exception] = None

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if self.raises is not None:
            raise self.raises
        for marker, payload in self.script.items():
            if marker in user or marker in system:
                return dict(payload)
        if self.default is not None:
            return dict(self.default)
        return {}


class FailingLLM(LLMClient):
    """An LLM double that always fails."""

    model = "failing-model"

    def __init__(self, error: Optional[Exception] = None) -> None:
        self.error = error or RuntimeError("the model is unavailable")
        self.calls = 0

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        self.calls += 1
        raise self.error


__all__ = [
    "FailingLLM",
    "FakeProvider",
    "ProviderConnectionError",
    "ProviderError",
    "ScriptedLLM",
]
