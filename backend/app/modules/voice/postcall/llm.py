"""LLM access for the post-call workflow.

The workflow only needs one capability: "given a system instruction and a
user message, answer with a JSON object". That is deliberately narrow so
the provider can be swapped without touching the graph, and so tests can
inject a deterministic fake.

When no LLM is configured the client reports itself as unavailable and the
graph degrades to a deterministic, provider-data-only extraction instead of
failing the call.
"""

import abc
import json
import logging
import re
from typing import Any, Dict, Optional

from app.config import Settings
from app.modules.voice.errors import LLMUnavailableError

logger = logging.getLogger("callora.voice.postcall.llm")


class LLMClient(abc.ABC):
    """Minimal structured-output chat client."""

    #: Model identifier reported in the persisted outcome.
    model: str = "unknown"

    @abc.abstractmethod
    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        """Return a JSON object for the given prompt."""

    @property
    def is_available(self) -> bool:
        return True


class NullLLMClient(LLMClient):
    """Used when no LLM is configured."""

    model = "none"

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        raise LLMUnavailableError("no LLM is configured for the post-call workflow")

    @property
    def is_available(self) -> bool:
        return False


class LangChainLLMClient(LLMClient):
    """Adapter around a LangChain chat model."""

    def __init__(self, chat_model: Any, model_name: Optional[str] = None) -> None:
        self._chat_model = chat_model
        self.model = model_name or getattr(chat_model, "model_name", None) or type(
            chat_model
        ).__name__

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = self._chat_model.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        return extract_json(_content_to_text(response.content))


def _content_to_text(content: Any) -> str:
    """Flatten a chat message content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Dict[str, Any]:
    """Extract a JSON object from a model answer.

    Models often wrap JSON in prose or code fences; this strips the noise
    and returns the first balanced object. A value that is already a JSON
    object is returned unchanged.
    """
    if text is None:
        raise ValueError("the model returned an empty response")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    match = _JSON_BLOCK.search(stripped)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"the model response was not valid JSON: {exc}") from exc
    raise ValueError("the model response contained no JSON object")


def build_llm_client(settings: Settings) -> LLMClient:
    """Build the configured LLM client.

    Returns a :class:`NullLLMClient` when no usable configuration exists,
    so the workflow can degrade instead of crashing.
    """
    provider = (settings.voice_llm_provider or "groq").strip().lower()
    if provider in ("null", "none", "disabled", "off"):
        return NullLLMClient()

    if provider == "groq":
        api_key = settings.groq_api_key
        if not api_key:
            logger.warning("groq LLM requested but GROQ_API_KEY is not set")
            return NullLLMClient()
        try:
            from langchain_groq import ChatGroq
        except ImportError:  # pragma: no cover - optional dependency
            logger.warning("langchain-groq is not installed; LLM disabled")
            return NullLLMClient()
        model = settings.voice_llm_model or settings.groq_model
        try:
            chat_model = ChatGroq(
                model=model,
                api_key=api_key,
                temperature=settings.voice_llm_temperature,
                timeout=settings.voice_llm_timeout_seconds,
            )
        except Exception as exc:  # pragma: no cover - provider specific
            logger.warning("could not build the Groq chat model: %s", exc)
            return NullLLMClient()
        return LangChainLLMClient(chat_model, model_name=model)

    if provider == "langchain":
        model_name = settings.voice_llm_model
        if not model_name:
            logger.warning("voice_llm_provider='langchain' but no model is configured")
            return NullLLMClient()
        try:
            from langchain.chat_models import init_chat_model

            chat_model = init_chat_model(
                model_name,
                temperature=settings.voice_llm_temperature,
            )
        except Exception as exc:  # pragma: no cover - provider specific
            logger.warning("could not build the LangChain chat model: %s", exc)
            return NullLLMClient()
        return LangChainLLMClient(chat_model, model_name=model_name)

    logger.warning("unknown voice_llm_provider '%s'; LLM disabled", provider)
    return NullLLMClient()
