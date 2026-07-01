"""Token-usage capture for LLM providers.

The base :class:`auto_dm.llm.base.LLMProvider` protocol returns only the
text content of a completion, discarding the ``response.usage`` payload
that OpenAI-compatible APIs attach (prompt/completion/total tokens).
This module adds an *optional* ``chat_with_usage`` capability on top of
that protocol so the web backend can track per-user token cost without
breaking the simpler CLI consumers (which keep calling ``chat()``).

Design:

- :class:`UsageReport` is a frozen value object carrying the token
  counts plus ``source`` — either ``"api"`` (real numbers from the
  provider) or ``"fallback"`` (the ``chars // 3`` heuristic). Cost is
  **not** stored here; it's computed at persistence time from tokens ×
  the configured per-1k-token price (see ``web/usage.py``).
- :func:`chat_with_usage` and :func:`iter_stream_with_usage` are free
  helpers that prefer a provider's native ``*_with_usage`` method when
  it exists, and otherwise fall back to wrapping ``chat``/``stream`` +
  the heuristic. Centralizing that policy here means providers and
  callers don't repeat themselves.

We deliberately avoid a ``provider.last_usage`` attribute: the provider
is a singleton shared across requests, and the SSE producer runs in a
worker thread, so concurrent streams would race on shared mutable state.
Returning usage *by value* is race-free.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional, Protocol

from auto_dm.llm.base import Message


@dataclass(frozen=True)
class UsageReport:
    """Token usage for a single completion.

    Attributes:
        prompt_tokens: Tokens in the input messages.
        completion_tokens: Tokens in the generated output.
        total_tokens: prompt + completion (provider-reported when
            ``source == "api"``).
        provider: Provider key (e.g. ``"minimax"``), for logging.
        model: Model name, for logging.
        source: ``"api"`` when the numbers came from ``response.usage``,
            ``"fallback"`` when estimated via the chars//3 heuristic.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    source: str = "fallback"

    def __post_init__(self) -> None:
        # Normalize: if total wasn't reported, derive it so downstream
        # aggregation never sees a zero total for a real call.
        if self.total_tokens == 0:
            object.__setattr__(
                self, "total_tokens", self.prompt_tokens + self.completion_tokens
            )


class _UsageCapable(Protocol):
    """Structural type for a provider that reports real usage."""

    name: str
    config: object  # LLMConfig; loosely typed to avoid an import cycle

    def chat_with_usage(self, messages: list[Message]) -> tuple[str, UsageReport]: ...


def _provider_identity(provider: object) -> tuple[str, str]:
    """Best-effort (provider name, model name) for logging."""
    name = getattr(provider, "name", "") or ""
    cfg = getattr(provider, "config", None)
    model = getattr(cfg, "model", "") or "" if cfg is not None else ""
    return name, model


def chat_with_usage(provider: object, messages: list[Message]) -> tuple[str, UsageReport]:
    """Return ``(content, UsageReport)`` for a single-shot completion.

    Prefers the provider's native ``chat_with_usage`` (real API usage).
    Otherwise calls ``provider.chat(messages)`` and estimates tokens:
    prompt via ``provider.count_tokens`` (also heuristic today) and
    completion via ``chars // 3`` — marked ``source="fallback"``.
    """
    native = getattr(provider, "chat_with_usage", None)
    if callable(native):
        return native(messages)
    content = provider.chat(messages)  # type: ignore[attr-defined]
    name, model = _provider_identity(provider)
    prompt_tokens = 0
    count = getattr(provider, "count_tokens", None)
    if callable(count):
        prompt_tokens = count(messages)
    completion_tokens = len(content) // 3
    report = UsageReport(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        provider=name,
        model=model,
        source="fallback",
    )
    return content, report


def iter_stream_with_usage(
    provider: object, messages: list[Message]
) -> Iterator[tuple[str, Optional[UsageReport]]]:
    """Stream tokens, yielding a final ``UsageReport``.

    Yields ``(token, None)`` for each text chunk and exactly one
    ``(usage_marker, report)`` near the end (the marker string is empty
    when a real report is attached; callers distinguish by the non-None
    second element). Prefers ``provider.iter_stream_with_usage``; falls
    back to wrapping ``provider.stream`` + the chars//3 heuristic when
    the provider doesn't report usage in-stream.
    """
    native = getattr(provider, "iter_stream_with_usage", None)
    if callable(native):
        yield from native(messages)
        return
    name, model = _provider_identity(provider)
    prompt_tokens = 0
    count = getattr(provider, "count_tokens", None)
    if callable(count):
        prompt_tokens = count(messages)
    completion_chars = 0
    for tok in provider.stream(messages):  # type: ignore[attr-defined]
        completion_chars += len(tok)
        yield tok, None
    report = UsageReport(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_chars // 3,
        provider=name,
        model=model,
        source="fallback",
    )
    yield "", report
