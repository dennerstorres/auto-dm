"""Base provider for OpenAI-compatible chat completions APIs.

Reused by Minimax, GLM, and (later) OpenAI native. The only differences
between these providers are the base URL, default model, and any
provider-specific `extra_body` (e.g. MiniMax's `thinking` field).
"""
from __future__ import annotations

import logging
from typing import Optional

import openai
from openai import OpenAI

from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from auto_dm.llm.usage import UsageReport
from auto_dm.llm.utils import strip_thinking


logger = logging.getLogger(__name__)


def _wrap_openai_errors(provider: str):
    """Context manager that maps ``openai`` SDK exceptions to our hierarchy.

    The mapped errors carry only a generic message (never the SDK payload),
    while the original exception is preserved as ``__cause__`` for debugging.
    Non-matching exceptions pass through unchanged.
    """
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            yield
        except openai.AuthenticationError as exc:
            raise ProviderAuthError(provider) from exc
        except openai.PermissionDeniedError as exc:
            raise ProviderAuthError(provider) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(provider) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(provider) from exc
        except openai.APIConnectionError as exc:
            raise ProviderUnavailableError(provider) from exc
        except openai.InternalServerError as exc:
            raise ProviderUnavailableError(provider) from exc
        except openai.APIStatusError as exc:
            # Remaining status errors: 5xx are transient, anything else is
            # surfaced as a generic provider error (the web layer maps
            # unknown failures to 502). Auth/rate-limit/timeout were caught
            # above by their specific subclasses.
            status = getattr(exc, "status_code", None) or 0
            if status >= 500:
                raise ProviderUnavailableError(provider) from exc
            raise ProviderError(provider) from exc

    return _cm()


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
        client_kwargs: dict = {
            "base_url": config.base_url or self.DEFAULT_BASE_URL,
            "api_key": config.api_key,
        }
        # Per-request timeout (seconds) is optional; only forwarded when the
        # caller set it via config.extra["timeout"]. Key validation uses a
        # short timeout; gameplay calls leave it unset (SDK default).
        timeout = config.extra.get("timeout") if config.extra else None
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        self.client = OpenAI(**client_kwargs)

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
        }
        # max_tokens <= 0 means "sem limite": omit the field so the API
        # falls back to the model's own output ceiling.
        if self.config.max_tokens > 0:
            kwargs["max_tokens"] = self.config.max_tokens
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
        carries no ``usage`` payload. SDK errors are normalized to
        :class:`auto_dm.llm.errors.ProviderError` subclasses (auth,
        rate-limit, timeout, unavailable) with generic messages.
        """
        with _wrap_openai_errors(self.name):
            response = self.client.chat.completions.create(
                **self._request_kwargs(messages)
            )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            if self.config.max_tokens > 0:
                logger.warning(
                    "%s response truncated by max_tokens=%s (thinking tokens "
                    "share this budget) — narration may be cut mid-sentence. "
                    "Raise AUTO_DM_MAX_TOKENS, or set it to 0 for no cap.",
                    self.name,
                    self.config.max_tokens,
                )
            else:
                logger.warning(
                    "%s response truncated at the model's own output ceiling "
                    "(no max_tokens cap was sent) — narration may be cut "
                    "mid-sentence.",
                    self.name,
                )
        content = strip_thinking(choice.message.content)
        report = self._report_from_usage(getattr(response, "usage", None))
        if report is None:
            report = self._fallback_report(
                messages, len(choice.message.content or "")
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
