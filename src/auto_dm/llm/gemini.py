"""Google Gemini provider.

Google publishes an OpenAI-compatible endpoint for Gemini, so this adapter
reuses :class:`OpenAICompatibleProvider` with a fixed base URL. This avoids
adding the (deprecated) ``google-generativeai`` SDK or the heavier
``google-genai`` package purely for chat — usage, finish_reason and errors
flow through the same normalized paths as the other OpenAI-compatible
providers.

Known limitation: Google's compatibility layer occasionally lags the native
API. If it breaks in production, swap the body of this file for a
``google-genai``-backed adapter — the provider *contract* (chat, usage,
normalized errors) stays the same, so no caller changes.
"""
from __future__ import annotations

from auto_dm.llm.base import Message
from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    """LLM provider for Google Gemini via the OpenAI-compatible endpoint."""

    name = "gemini"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-3.5-flash"
    DEFAULT_THINKING = None

    def _request_kwargs(self, messages: list[Message]) -> dict:
        """Build Gemini 3.x-compatible Chat Completions arguments.

        Gemini 3.5 no longer recommends sampling parameters such as
        ``temperature``. Keep the compatible endpoint while letting Gemini
        use its native thinking default.
        """
        kwargs: dict = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
        }
        if self.config.max_tokens > 0:
            kwargs["max_tokens"] = self.config.max_tokens
        extra_body = self._build_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs
