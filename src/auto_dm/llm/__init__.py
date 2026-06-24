"""LLM provider abstraction.

Every provider (Claude, OpenAI, Gemini, GLM, Minimax) implements the
`LLMProvider` protocol defined in `base.py`. The factory in `factory.py`
selects the right implementation based on config.

Currently implemented:
    - MinimaxProvider (MiniMax OpenAI-compatible API)

Coming in Phase 10:
    - ClaudeProvider (Anthropic SDK)
    - GeminiProvider (Google Generative AI SDK)
    - OpenAIProvider (OpenAI native)
    - GLMProvider (Zhipu OpenAI-compatible)
"""
from auto_dm.llm.base import LLMConfig, LLMProvider, Message
from auto_dm.llm.factory import get_provider

__all__ = ["LLMConfig", "LLMProvider", "Message", "get_provider"]
