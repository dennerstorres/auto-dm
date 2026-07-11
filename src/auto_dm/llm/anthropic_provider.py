"""Anthropic (Claude) provider via the official ``anthropic`` SDK.

Unlike the OpenAI-compatible providers, Claude's Messages API has its own
request/response shape, so this is a standalone adapter implementing the
provider contract directly (``chat``, ``chat_with_usage``, ``count_tokens``).

Key adaptation points:

- System messages are collected and passed as the top-level ``system``
  parameter (the API rejects ``role: "system"`` inside ``messages``).
- ``max_tokens`` is required by the API; when the config cap is disabled
  (``<= 0``) we default to a generous 8192.
- Sampling params (``temperature``) and the thinking toggle are omitted:
  current Claude models reject them, and adaptive thinking is the default.
- ``stop_reason == "max_tokens"`` maps to the same truncation warning the
  OpenAI-compatible base emits for ``finish_reason == "length"``.
- SDK exceptions are normalized to :mod:`auto_dm.llm.errors` with generic
  messages (no payload leakage).

The ``anthropic`` import is lazy so importing this module (and thus the
registry) doesn't require the package; only constructing a provider does.
"""
from __future__ import annotations

import logging
from typing import Optional

from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.usage import UsageReport

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """LLM provider for Anthropic Claude."""

    name = "anthropic"
    DEFAULT_MODEL = "claude-sonnet-5"
    # Cap applied when the caller disabled the limit (max_tokens <= 0).
    DEFAULT_MAX_TOKENS = 8192

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ValueError("AnthropicProvider requires `api_key` in config.")
        self.config = config
        if not config.model:
            config.model = self.DEFAULT_MODEL

        import anthropic  # lazy: registry import must not require the dep

        timeout = config.extra.get("timeout") if config.extra else None
        client_kwargs: dict = {"api_key": config.api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        # base_url is intentionally NOT forwarded: the endpoint is fixed by
        # the SDK to prevent SSRF via user-supplied URLs.
        self.client = anthropic.Anthropic(**client_kwargs)

    # -- request building ------------------------------------------------

    def _split_messages(self, messages: list[Message]) -> tuple[str | None, list[dict]]:
        """Split into a top-level system string + the conversational turns.

        Claude requires the first message to be a ``user`` turn, which the
        agents already satisfy (they send ``[system, user, ...]``).
        """
        system_parts = [m.content for m in messages if m.role == "system"]
        system = "\n\n".join(system_parts) if system_parts else None
        turns = [m.to_dict() for m in messages if m.role != "system"]
        return system, turns

    def _request_kwargs(self, messages: list[Message]) -> dict:
        system, turns = self._split_messages(messages)
        kwargs: dict = {
            "model": self.config.model,
            "messages": turns,
            "max_tokens": self.config.max_tokens if self.config.max_tokens > 0
            else self.DEFAULT_MAX_TOKENS,
        }
        if system:
            kwargs["system"] = system
        return kwargs

    # -- usage helpers ---------------------------------------------------

    def _report_from_usage(self, usage: object) -> Optional[UsageReport]:
        if usage is None:
            return None
        prompt = getattr(usage, "input_tokens", 0) or 0
        completion = getattr(usage, "output_tokens", 0) or 0
        if not (prompt or completion):
            return None
        return UsageReport(
            prompt_tokens=prompt,
            completion_tokens=completion,
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

    # -- public API ------------------------------------------------------

    def chat_with_usage(self, messages: list[Message]) -> tuple[str, UsageReport]:
        with _wrap_anthropic_errors(self.name):
            response = self.client.messages.create(**self._request_kwargs(messages))

        content = "".join(
            getattr(block, "text", "")
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning(
                "%s response truncated by the max_tokens cap — narration may "
                "be cut mid-sentence. Review the provider's output limits.",
                self.name,
            )
        report = self._report_from_usage(getattr(response, "usage", None))
        if report is None:
            report = self._fallback_report(messages, len(content))
        return content, report

    def chat(self, messages: list[Message]) -> str:
        return self.chat_with_usage(messages)[0]

    def count_tokens(self, messages: list[Message]) -> int:
        # Same heuristic as the OpenAI-compatible base (~3 chars/token pt-BR).
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 3


def _wrap_anthropic_errors(provider: str):
    """Map ``anthropic`` SDK exceptions to :mod:`auto_dm.llm.errors`."""
    import anthropic
    from contextlib import contextmanager

    from auto_dm.llm.errors import (
        ProviderAuthError,
        ProviderError,
        ProviderRateLimitError,
        ProviderTimeoutError,
        ProviderUnavailableError,
    )

    @contextmanager
    def _cm():
        try:
            yield
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(provider) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderAuthError(provider) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitError(provider) from exc
        except anthropic.OverloadedError as exc:
            # 529 overloaded is a transient capacity error, not auth.
            raise ProviderUnavailableError(provider) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(provider) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError(provider) from exc
        except anthropic.InternalServerError as exc:
            raise ProviderUnavailableError(provider) from exc
        except anthropic.APIStatusError as exc:
            status = getattr(exc, "status_code", None) or 0
            if status >= 500:
                raise ProviderUnavailableError(provider) from exc
            raise ProviderError(provider) from exc

    return _cm()
