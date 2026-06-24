"""Minimax provider.

Backed by MiniMax's OpenAI-compatible API.

Default model: MiniMax-M3
Default base URL: https://api.minimax.io/v1

Override via config.json (`model` and `base_url`).

Notes from the MiniMax API reference (https://platform.minimax.io/docs):
- Authentication: standard OpenAI-style `Authorization: Bearer <key>`
- The M-series supports extended thinking via `extra_body={"thinking": {"type": "adaptive"}}`
- Multi-turn tool use requires appending the full assistant message (including
  `reasoning_details`) to history to preserve reasoning continuity.
"""
from __future__ import annotations

from auto_dm.llm.base import LLMConfig
from auto_dm.llm.openai_compatible import OpenAICompatibleProvider


class MinimaxProvider(OpenAICompatibleProvider):
    """LLM provider for MiniMax."""

    name = "minimax"
    DEFAULT_BASE_URL = "https://api.minimax.io/v1"
    DEFAULT_MODEL = "MiniMax-M3"
    # Enable extended thinking by default. The M3 model wraps its reasoning
    # in <think>...</think> tags, which the provider strips before returning.
    DEFAULT_THINKING = "adaptive"

    def __init__(self, config: LLMConfig) -> None:
        if not config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        if not config.model:
            config.model = self.DEFAULT_MODEL
        super().__init__(config)
