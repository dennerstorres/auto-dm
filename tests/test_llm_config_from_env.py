"""Tests for ``LLMConfig.from_env`` and the default provider factory
(Phase 26d)."""
from __future__ import annotations

import pytest


def test_from_env_minimal():
    from auto_dm.llm.base import LLMConfig

    env = {
        "AUTO_DM_PROVIDER": "minimax",
        "AUTO_DM_API_KEY": "sk-test-1234567890",
        "AUTO_DM_MODEL": "MiniMax-Text-01",
    }
    for k, v in env.items():
        import os
        os.environ[k] = v
    try:
        cfg = LLMConfig.from_env()
        assert cfg.name == "minimax"
        assert cfg.api_key == "sk-test-1234567890"
        assert cfg.model == "MiniMax-Text-01"
        assert cfg.temperature == pytest.approx(0.8)
        assert cfg.max_tokens == 8192
        assert cfg.base_url is None
        assert cfg.thinking is None
    finally:
        for k in env:
            os.environ.pop(k, None)


def test_from_env_with_optionals():
    from auto_dm.llm.base import LLMConfig
    import os

    env = {
        "AUTO_DM_PROVIDER": "minimax",
        "AUTO_DM_API_KEY": "sk-test",
        "AUTO_DM_MODEL": "minimax",
        "AUTO_DM_BASE_URL": "https://api.example.com/v1",
        "AUTO_DM_TEMPERATURE": "0.3",
        "AUTO_DM_MAX_TOKENS": "1024",
        "AUTO_DM_THINKING": "adaptive",
    }
    for k, v in env.items():
        os.environ[k] = v
    try:
        cfg = LLMConfig.from_env()
        assert cfg.base_url == "https://api.example.com/v1"
        assert cfg.temperature == pytest.approx(0.3)
        assert cfg.max_tokens == 1024
        assert cfg.thinking == "adaptive"
    finally:
        for k in env:
            os.environ.pop(k, None)


def test_from_env_zero_max_tokens_means_unlimited():
    """AUTO_DM_MAX_TOKENS=0 is the admin escape hatch: the provider omits
    max_tokens from the request and the model's own ceiling applies."""
    from auto_dm.llm.base import LLMConfig
    import os

    env = {
        "AUTO_DM_PROVIDER": "minimax",
        "AUTO_DM_API_KEY": "sk-test",
        "AUTO_DM_MODEL": "m",
        "AUTO_DM_MAX_TOKENS": "0",
    }
    for k, v in env.items():
        os.environ[k] = v
    try:
        cfg = LLMConfig.from_env()
        assert cfg.max_tokens == 0
    finally:
        for k in env:
            os.environ.pop(k, None)


def test_from_env_missing_required():
    from auto_dm.llm.base import LLMConfig
    import os

    os.environ["AUTO_DM_PROVIDER"] = "minimax"
    os.environ.pop("AUTO_DM_API_KEY", None)
    os.environ.pop("AUTO_DM_MODEL", None)
    try:
        with pytest.raises(RuntimeError) as exc:
            LLMConfig.from_env()
        msg = str(exc.value).lower()
        assert "api_key" in msg or "model" in msg
    finally:
        os.environ.pop("AUTO_DM_PROVIDER", None)


def test_from_env_invalid_numbers_fall_back_to_defaults():
    from auto_dm.llm.base import LLMConfig
    import os

    os.environ["AUTO_DM_PROVIDER"] = "minimax"
    os.environ["AUTO_DM_API_KEY"] = "sk"
    os.environ["AUTO_DM_MODEL"] = "m"
    os.environ["AUTO_DM_TEMPERATURE"] = "not-a-number"
    os.environ["AUTO_DM_MAX_TOKENS"] = "not-a-number"
    try:
        cfg = LLMConfig.from_env()
        assert cfg.temperature == 0.8
        assert cfg.max_tokens == 8192
    finally:
        for k in ("AUTO_DM_PROVIDER", "AUTO_DM_API_KEY", "AUTO_DM_MODEL",
                  "AUTO_DM_TEMPERATURE", "AUTO_DM_MAX_TOKENS"):
            os.environ.pop(k, None)


def test_default_provider_factory_no_env_raises():
    """Without env vars set, the factory raises."""
    import os
    from auto_dm.web.server import _default_provider_factory

    # Make sure the relevant env vars are unset for this test.
    for k in ("AUTO_DM_PROVIDER", "AUTO_DM_API_KEY", "AUTO_DM_MODEL"):
        os.environ.pop(k, None)
    with pytest.raises(RuntimeError):
        _default_provider_factory()


def test_default_provider_factory_unknown_provider_rejected():
    """Even with env vars set, unregistered providers raise."""
    import os
    from auto_dm.web.server import _default_provider_factory

    # Phase 51a: claude/openai/gemini/deepseek are now registered; use an
    # unregistered id (glm is out of scope) to exercise the unknown path.
    os.environ["AUTO_DM_PROVIDER"] = "glm"
    os.environ["AUTO_DM_API_KEY"] = "sk"
    os.environ["AUTO_DM_MODEL"] = "glm-3"
    try:
        with pytest.raises(ValueError) as exc:
            _default_provider_factory()
        assert "glm" in str(exc.value).lower()
    finally:
        for k in ("AUTO_DM_PROVIDER", "AUTO_DM_API_KEY", "AUTO_DM_MODEL"):
            os.environ.pop(k, None)