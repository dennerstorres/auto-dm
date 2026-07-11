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

from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    """LLM provider for Google Gemini via the OpenAI-compatible endpoint."""

    name = "gemini"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-2.5-flash"
    DEFAULT_THINKING = None
