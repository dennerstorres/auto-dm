"""Factory that returns the right LLM provider for a given config."""
from __future__ import annotations

from auto_dm.llm.base import LLMConfig
from auto_dm.llm.minimax import MinimaxProvider


def get_provider(config: LLMConfig) -> MinimaxProvider:
    """Return the LLMProvider implementation matching the config name.

    Currently only Minimax is implemented. Other providers (Claude, OpenAI,
    Gemini, GLM) are coming in Phase 10.
    """
    name = (config.name or "").lower()
    if name == "minimax":
        return MinimaxProvider(config)
    raise ValueError(
        f"Unknown LLM provider: {config.name!r}. "
        f"Implemented in v0.1: 'minimax'. "
        f"Coming in Phase 10: claude, openai, gemini, glm."
    )
