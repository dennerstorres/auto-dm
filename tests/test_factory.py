"""Tests for the LLM provider factory."""
from __future__ import annotations

import pytest

from auto_dm.llm.base import LLMConfig
from auto_dm.llm.factory import get_provider
from auto_dm.llm.minimax import MinimaxProvider


def test_factory_returns_minimax():
    config = LLMConfig(
        name="minimax",
        api_key="test",
        model="MiniMax-M3",
    )
    provider = get_provider(config)
    assert isinstance(provider, MinimaxProvider)
    assert provider.name == "minimax"
    assert provider.config.base_url == "https://api.minimax.io/v1"


def test_factory_case_insensitive():
    config = LLMConfig(name="Minimax", api_key="test", model="x")
    provider = get_provider(config)
    assert provider.name == "minimax"


def test_factory_custom_base_url():
    config = LLMConfig(
        name="minimax",
        api_key="test",
        model="x",
        base_url="https://custom.example.com/v1",
    )
    provider = get_provider(config)
    assert provider.config.base_url == "https://custom.example.com/v1"


def test_factory_unknown_provider_raises():
    # Phase 51a: claude/openai/gemini/deepseek are now registered providers;
    # use an unregistered id to exercise the unknown path.
    config = LLMConfig(name="glm", api_key="x", model="y")
    with pytest.raises(ValueError, match="Provedor desconhecido"):
        get_provider(config)


def test_minimax_provider_default_model():
    config = LLMConfig(name="minimax", api_key="test", model="")
    provider = MinimaxProvider(config)
    assert provider.config.model == "MiniMax-M3"
