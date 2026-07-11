"""LLM provider abstraction.

Every provider implements the `LLMProvider` protocol from `base.py`. The
central registry in `registry.py` (Phase 51a) is the source of truth for
which providers exist, which models each allows, and how to build their
adapters.

Implemented providers:
    - MinimaxProvider   — MiniMax (OpenAI-compatible)
    - OpenAIProvider    — OpenAI native (GPT-5.x)
    - AnthropicProvider — Anthropic Claude (native Messages API)
    - GeminiProvider    — Google Gemini (OpenAI-compatible endpoint)
    - DeepSeekProvider  — DeepSeek (OpenAI-compatible)

GLM is out of the initial Phase 51 scope and may be added later through
the same registry.
"""
from auto_dm.llm.base import LLMConfig, LLMProvider, Message
from auto_dm.llm.factory import get_provider

__all__ = ["LLMConfig", "LLMProvider", "Message", "get_provider"]
