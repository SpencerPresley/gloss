"""Single seam between the build pipeline and any LLM provider.

The pipeline depends only on ``StructuredExtractor``; provider specifics live in
adapters. This module has no top-level langchain/ollama import, so importing the
protocol never drags in a provider.
"""
from __future__ import annotations
from typing import Callable, Protocol, runtime_checkable
from pydantic import BaseModel


@runtime_checkable
class StructuredExtractor(Protocol):
    """Turn a prompt + Pydantic schema into a validated instance.

    The one method the build pipeline depends on. Implementations hide all
    provider/SDK details (method selection, retries, context sizing).
    """

    def extract(self, prompt: str, schema: type[BaseModel], *, system: str | None = None) -> BaseModel: ...


class StubExtractor:
    """Deterministic test double; provider-agnostic, for testing the pipeline without a model."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def extract(self, prompt: str, schema: type[BaseModel], *, system: str | None = None) -> BaseModel:
        return schema(**self._payload)


class OllamaExtractor:
    """``StructuredExtractor`` backed by Ollama via langchain-ollama.

    Auto-discovers which structured-output method the model honors and pins it.
    All Ollama/LangChain specifics are encapsulated here.

    Args:
        model: Ollama model tag (e.g. ``"minimax-m3:cloud"``, ``"gpt-oss:20b"``).
        num_ctx: context window for local models (measured by the caller; cloud ignores it).
        method: optional override to skip auto-discovery when the method is already known.
        chat_factory: injection point for tests; builds the chat model when omitted.
    """

    # Ollama-specific, private to this adapter: grammar-constrained json_schema
    # first, tool-calling fallback for models that ignore format= (e.g. minimax).
    _METHODS = ("json_schema", "function_calling")

    def __init__(self, model: str, num_ctx: int = 16384, method: str | None = None,
                 chat_factory: Callable[[], object] | None = None) -> None:
        self.model = model
        self._num_ctx = num_ctx
        self._method = method            # if set, auto-discovery is skipped
        self._chat = None
        self._chat_factory = chat_factory

    def _chat_model(self):
        """Build (once) and cache the chat model; lazy import keeps the dep build-only."""
        if self._chat is None:
            if self._chat_factory is not None:
                self._chat = self._chat_factory()
            else:
                from langchain_ollama import ChatOllama
                self._chat = ChatOllama(model=self.model, temperature=0, num_ctx=self._num_ctx)
        return self._chat

    def extract(self, prompt: str, schema: type[BaseModel], *, system: str | None = None) -> BaseModel:
        """Return a schema instance, discovering + pinning the working method.

        Raises:
            ValueError: if every candidate method fails to produce valid output.
        """
        messages = ([("system", system)] if system else []) + [("human", prompt)]
        chat, last = self._chat_model(), None
        for method in ((self._method,) if self._method else self._METHODS):
            try:
                runnable = chat.with_structured_output(schema, method=method, include_raw=True)
                result = runnable.invoke(messages)
                if result.get("parsed") is not None and not result.get("parsing_error"):
                    self._method = method        # pin what worked
                    return result["parsed"]
                last = result.get("parsing_error")
            except Exception as exc:             # method unsupported / hard failure -> try next
                last = exc
        raise ValueError(f"extraction failed for {self.model}: {last}")
