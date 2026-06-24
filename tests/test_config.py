"""Tests for config loading."""
from __future__ import annotations

import json

import pytest

from auto_dm.config import load_app_config


def _write_config(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_minimax_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    _write_config(
        config_file,
        {
            "provider": "minimax",
            "model": "MiniMax-M3",
            "base_url": "https://api.minimax.io/v1",
            "temperature": 0.7,
            "max_tokens": 1024,
        },
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key-123")

    app = load_app_config(config_file, env_path=tmp_path / ".env")

    assert app.llm.name == "minimax"
    assert app.llm.model == "MiniMax-M3"
    assert app.llm.api_key == "test-key-123"
    assert app.llm.base_url == "https://api.minimax.io/v1"
    assert app.llm.temperature == 0.7
    assert app.llm.max_tokens == 1024
    assert app.language == "pt-BR"  # default


def test_missing_config_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_app_config(tmp_path / "nope.json", env_path=tmp_path / ".env")


def test_missing_api_key(tmp_path):
    config_file = tmp_path / "config.json"
    _write_config(config_file, {"provider": "minimax", "model": "x"})
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
            load_app_config(config_file, env_path=tmp_path / ".env")
    finally:
        monkeypatch.undo()


def test_unknown_provider(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    _write_config(config_file, {"provider": "minimax", "model": "x"})
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    # Now mutate the file to an unknown provider
    _write_config(config_file, {"provider": "made-up", "model": "x"})
    with pytest.raises(ValueError, match="Unknown provider"):
        load_app_config(config_file, env_path=tmp_path / ".env")
