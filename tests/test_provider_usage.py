"""Tests for token-usage capture (Phase 30).

Covers:
- :func:`chat_with_usage` prefers a provider's native ``chat_with_usage``
  (real API usage) and otherwise falls back to the chars//3 heuristic.
- :class:`DMAgent.ask` propagates usage to ``DMResponse``.
- :class:`UsageReport` normalizes a zero ``total_tokens``.
- :func:`compute_cost` derives USD from tokens × configured prices.
"""
from __future__ import annotations

import logging

import pytest

from auto_dm.agents.dm import DMAgent
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.usage import UsageReport, chat_with_usage
from auto_dm.web.config import get_settings
from auto_dm.web.usage import compute_cost


# ============================================================================
# Stubs
# ============================================================================


class _NativeProvider:
    """A provider that reports real usage via ``chat_with_usage``."""

    name = "native"

    def __init__(self, content="hello", report=None):
        self.config = LLMConfig(name="native", api_key="k", model="m")
        self._content = content
        self._report = report or UsageReport(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
            provider="native", model="m", source="api",
        )

    def chat_with_usage(self, messages):
        return self._content, self._report


class _LegacyProvider:
    """A provider with only ``chat``/``count_tokens`` (fallback path)."""

    name = "legacy"

    def __init__(self, content="hello world"):
        self.config = LLMConfig(name="legacy", api_key="k", model="m")
        self._content = content

    def chat(self, messages):
        return self._content

    def count_tokens(self, messages):
        return sum(len(m.content) for m in messages)


# ============================================================================
# chat_with_usage
# ============================================================================


def test_chat_with_usage_uses_native_when_available():
    provider = _NativeProvider(content="hi", report=UsageReport(
        prompt_tokens=7, completion_tokens=3, total_tokens=10,
        provider="native", model="m", source="api",
    ))
    content, report = chat_with_usage(provider, [Message("user", "x")])
    assert content == "hi"
    assert report.source == "api"
    assert report.total_tokens == 10


def test_chat_with_usage_falls_back_to_heuristic():
    provider = _LegacyProvider(content="hello world")  # 11 chars
    content, report = chat_with_usage(provider, [Message("user", "hello")])
    assert content == "hello world"
    assert report.source == "fallback"
    # prompt from count_tokens (5 chars), completion = 11 // 3
    assert report.prompt_tokens == 5
    assert report.completion_tokens == 11 // 3
    assert report.total_tokens == report.prompt_tokens + report.completion_tokens


# ============================================================================
# UsageReport normalization
# ============================================================================


def test_usage_report_normalizes_zero_total():
    report = UsageReport(prompt_tokens=4, completion_tokens=6)
    assert report.total_tokens == 10


# ============================================================================
# DMAgent propagation
# ============================================================================


def _build_dm(provider):
    from datetime import datetime, timezone

    from auto_dm.state.manager import StateManager
    from auto_dm.state.models import (
        AbilityScores,
        Character,
        GameState,
    )

    player = Character(
        id="p1", name="Aragorn", race="Human", **{"class": "Fighter"},
        level=1, background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=15, dexterity=14, constitution=13,
            intelligence=12, wisdom=10, charisma=8,
        ),
        hp_current=10, hp_max=10, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        is_player=True,
    )
    state = GameState(
        campaign_name="c", started_at=datetime.now(timezone.utc).isoformat(),
        current_location="t", party=[player], npcs=[], initiative_order=[],
        in_combat=False, current_turn_index=0, player_character_id="p1",
        active_conditions=[],
    )
    return DMAgent(provider=provider, state_manager=StateManager(state))


def test_dm_agent_ask_propagates_native_usage():
    provider = _NativeProvider(content="Você vê uma porta.")
    agent = _build_dm(provider)
    resp = agent.ask("olhar")
    assert resp.usage is not None
    assert resp.usage.source == "api"


# ============================================================================
# Truncation warning (finish_reason == "length")
# ============================================================================


def _minimax_with_fake_response(finish_reason: str, content: str, max_tokens: int = 8192):
    from types import SimpleNamespace

    from auto_dm.llm.minimax import MinimaxProvider

    provider = MinimaxProvider(
        LLMConfig(name="minimax", api_key="k", model="m", max_tokens=max_tokens)
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )
    )
    return provider


def test_chat_with_usage_warns_when_truncated_by_max_tokens(caplog):
    provider = _minimax_with_fake_response("length", "narração cortada no mei")
    with caplog.at_level(logging.WARNING, logger="auto_dm.llm.openai_compatible"):
        content, report = provider.chat_with_usage([Message("user", "x")])
    assert content == "narração cortada no mei"
    assert "max_tokens" in caplog.text


def test_chat_with_usage_does_not_warn_on_normal_stop(caplog):
    provider = _minimax_with_fake_response("stop", "narração completa.")
    with caplog.at_level(logging.WARNING, logger="auto_dm.llm.openai_compatible"):
        content, _ = provider.chat_with_usage([Message("user", "x")])
    assert content == "narração completa."
    assert "max_tokens" not in caplog.text


def test_chat_with_usage_warns_on_model_ceiling_when_uncapped(caplog):
    provider = _minimax_with_fake_response("length", "cortou mesmo sem ca", max_tokens=0)
    with caplog.at_level(logging.WARNING, logger="auto_dm.llm.openai_compatible"):
        content, _ = provider.chat_with_usage([Message("user", "x")])
    assert content == "cortou mesmo sem ca"
    assert "own output ceiling" in caplog.text


# ============================================================================
# max_tokens = 0 → "sem limite" (field omitted from the request)
# ============================================================================


def _minimax(max_tokens: int):
    from auto_dm.llm.minimax import MinimaxProvider

    return MinimaxProvider(
        LLMConfig(name="minimax", api_key="k", model="m", max_tokens=max_tokens)
    )


def test_request_kwargs_includes_positive_cap():
    kwargs = _minimax(8192)._request_kwargs([Message("user", "x")])
    assert kwargs["max_tokens"] == 8192


def test_request_kwargs_omits_cap_when_zero():
    kwargs = _minimax(0)._request_kwargs([Message("user", "x")])
    assert "max_tokens" not in kwargs


def test_request_kwargs_omits_cap_when_negative():
    kwargs = _minimax(-1)._request_kwargs([Message("user", "x")])
    assert "max_tokens" not in kwargs


# ============================================================================
# compute_cost
# ============================================================================


def test_compute_cost_uses_configured_prices(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TOKEN_PRICE_PER_1K_INPUT_USD", "0.010")
    monkeypatch.setenv("TOKEN_PRICE_PER_1K_OUTPUT_USD", "0.020")
    settings = get_settings()
    report = UsageReport(prompt_tokens=1000, completion_tokens=500)
    cost = compute_cost(report, settings)
    # 1000/1000 * 0.01 + 500/1000 * 0.02 = 0.01 + 0.01 = 0.02
    assert float(cost) == pytest.approx(0.02)
    get_settings.cache_clear()
