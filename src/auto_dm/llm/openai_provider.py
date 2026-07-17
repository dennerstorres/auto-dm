"""OpenAI native provider.

Backed by OpenAI's own API (OpenAI-compatible, naturally). Endpoint is
fixed server-side; users never supply one (anti-SSRF).

GPT-5.x models on the chat completions API reject the legacy sampling
params and renamed the output cap, so this adapter overrides
``_request_kwargs`` to:

- omit ``temperature`` (the API errors if it differs from the default on
  reasoning models), and
- send ``max_completion_tokens`` instead of ``max_tokens``.
"""
from __future__ import annotations

from auto_dm.llm.base import Message
from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """LLM provider for OpenAI."""

    name = "openai"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-5.4-mini"
    # No extended thinking field — OpenAI reasoning models use their own
    # controls; we leave them at provider defaults.
    DEFAULT_THINKING = None

    def _request_kwargs(self, messages: list[Message]) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": [m.to_dict() for m in messages],
        }
        # max_completion_tokens replaced max_tokens on current OpenAI models.
        # max_tokens <= 0 means "sem limite": omit so the API uses the model
        # ceiling.
        if self.config.max_tokens > 0:
            kwargs["max_completion_tokens"] = self.config.max_tokens
        # No temperature: reasoning models reject it unless it equals the
        # default, and gameplay narration doesn't need sampling control.
        return kwargs
