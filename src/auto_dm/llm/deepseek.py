"""DeepSeek provider.

DeepSeek exposes an OpenAI-compatible chat completions API, so this is a
trivial subclass that only fixes the endpoint and default model. Usage
metadata comes back in the standard ``response.usage`` shape, so the base
``chat_with_usage`` reports real tokens.

DeepSeek V4 supports thinking and non-thinking modes on both current model
ids. Thinking is enabled explicitly so gameplay behaviour does not depend
on a provider-side default.
"""
from __future__ import annotations

from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """LLM provider for DeepSeek."""

    name = "deepseek"
    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-v4-flash"
    DEFAULT_THINKING = "enabled"
