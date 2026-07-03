"""Base provider for OpenAI-compatible chat completions APIs.

Reused by Minimax, GLM, and (later) OpenAI native. The only differences
between these providers are the base URL, default model, and any
provider-specific `extra_body` (e.g. MiniMax's `thinking` field).
"""
from __future__ import annotations

from typing import Optional

from openai import OpenAI

from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.usage import UsageReport
from auto_dm.llm.utils import strip_thinking


class OpenAICompatibleProvider:
    """Base implementation of an OpenAI-compatible chat completions provider."""

    DEFAULT_BASE_URL: str | None = None
    DEFAULT_MODEL: str | None = None
    DEFAULT_THINKING: str | None = None  # subclass override (e.g. "adaptive")
    name: str = "openai-compatible"

    def __init__(self, config: LLMConfig) -> None:
        if not config.base_url and not self.DEFAULT_BASE_URL:
            raise ValueError(
                f"{type(self).__name__} requires either `base_url` in config "
                f"or a class-level DEFAULT_BASE_URL."
            )
        if not config.api_key:
            raise ValueError(
                f"{type(self).__name__} requires `api_key` in config."
            )

        self.config = config
        self.client = OpenAI(
            base_url=config.base_url or self.DEFAULT_BASE_URL,
            api_key=config.api_key,
        )

    # -- extra_body / thinking --------------------------------------------

    def _build_extra_body(self) -> dict | None:
        """Build the `extra_body` payload passed to the OpenAI SDK.

        For providers that support extended thinking (MiniMax), this injects
        the `thinking` field. Subclasses can override to add more keys.
        """
        thinking = self.config.thinking
        if thinking is None:
            thinking = self.DEFAULT_THINKING
        if thinking:
            return {"thinking": {"type": thinking}}
        return None

    def _request_kwargs(self, messages: list[Message]) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        extra_body = self._build_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    # -- usage helpers ----------------------------------------------------

    def _report_from_usage(self, usage: object) -> Optional[UsageReport]:
        """Build a :class:`UsageReport` from an OpenAI ``usage`` object.

        Returns ``None`` when the payload is absent (the provider didn't
        report usage); callers then fall back to the heuristic.
        """
        if usage is None:
            return None
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or (prompt + completion)
        if not (prompt or completion or total):
            return None
        return UsageReport(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            provider=self.name,
            model=self.config.model,
            source="api",
        )

    def _fallback_report(self, messages: list[Message], completion_chars: int) -> UsageReport:
        return UsageReport(
            prompt_tokens=self.count_tokens(messages),
            completion_tokens=completion_chars // 3,
            provider=self.name,
            model=self.config.model,
            source="fallback",
        )

    # -- public API -------------------------------------------------------

    def chat_with_usage(self, messages: list[Message]) -> tuple[str, UsageReport]:
        """Single-shot completion that also returns real token usage.

        Falls back to the chars//3 heuristic when the provider's response
        carries no ``usage`` payload.
        """
        response = self.client.chat.completions.create(
            **self._request_kwargs(messages)
        )
        content = strip_thinking(response.choices[0].message.content)
        report = self._report_from_usage(getattr(response, "usage", None))
        if report is None:
            report = self._fallback_report(
                messages, len(response.choices[0].message.content or "")
            )
        return content, report

    def chat(self, messages: list[Message]) -> str:
        """Single-shot completion (text only). Thin wrapper over
        :meth:`chat_with_usage`, kept for the base :class:`LLMProvider`
        protocol and the CLI."""
        return self.chat_with_usage(messages)[0]

    def count_tokens(self, messages: list[Message]) -> int:
        # Rough approximation: ~3 chars per token for pt-BR mixed text.
        # Good enough for budget estimation, not exact.
        # Real tokenizers per provider can replace this later.
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 3
