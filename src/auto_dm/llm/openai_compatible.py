"""Base provider for OpenAI-compatible chat completions APIs.

Reused by Minimax, GLM, and (later) OpenAI native. The only differences
between these providers are the base URL, default model, and any
provider-specific `extra_body` (e.g. MiniMax's `thinking` field).
"""
from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from auto_dm.llm.base import LLMConfig, Message
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

    def _request_kwargs(self, messages: list[Message], *, stream: bool) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        extra_body = self._build_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        if stream:
            kwargs["stream"] = True
        return kwargs

    # -- public API -------------------------------------------------------

    def chat(self, messages: list[Message]) -> str:
        response = self.client.chat.completions.create(
            **self._request_kwargs(messages, stream=False)
        )
        content = response.choices[0].message.content
        return strip_thinking(content)

    def stream(self, messages: list[Message]) -> Iterator[str]:
        # TODO: when extended thinking is on, the <think>...</think> may
        # span chunks. For now we yield raw chunks; the caller can join +
        # strip if it needs clean text.
        stream = self.client.chat.completions.create(
            **self._request_kwargs(messages, stream=True)
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def count_tokens(self, messages: list[Message]) -> int:
        # Rough approximation: ~3 chars per token for pt-BR mixed text.
        # Good enough for budget estimation, not exact.
        # Real tokenizers per provider can replace this later.
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 3
