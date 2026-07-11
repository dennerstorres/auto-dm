"""DeepSeek provider.

DeepSeek exposes an OpenAI-compatible chat completions API, so this is a
trivial subclass that only fixes the endpoint and default model. Usage
metadata comes back in the standard ``response.usage`` shape, so the base
``chat_with_usage`` reports real tokens.

Note: ``deepseek-reasoner`` returns its reasoning in a separate
``reasoning_content`` field rather than inline ``<think>`` tags, so
``strip_thinking`` is a harmless no-op for it.
"""
from __future__ import annotations

from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """LLM provider for DeepSeek."""

    name = "deepseek"
    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"
    DEFAULT_THINKING = None
