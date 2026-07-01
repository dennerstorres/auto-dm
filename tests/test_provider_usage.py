"""Tests for token-usage capture (Phase 30).

Covers:
- :func:`chat_with_usage` prefers a provider's native ``chat_with_usage``
  (real API usage) and otherwise falls back to the chars//3 heuristic.
- :func:`iter_stream_with_usage` emits a final ``UsageReport``.
- :class:`DMAgent.ask` / :meth:`DMAgent.stream_with_usage` propagate usage.
- :class:`UsageReport` normalizes a zero ``total_tokens``.
- :func:`compute_cost` derives USD from tokens × configured prices.
"""
from __future__ import annotations

import pytest

from auto_dm.agents.dm import DMAgent
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.usage import (
    UsageReport,
    chat_with_usage,
    iter_stream_with_usage,
)
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

    def stream(self, messages):
        for word in self._content.split():
            yield word


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
# iter_stream_with_usage
# ============================================================================


def test_iter_stream_with_usage_emits_final_fallback_report():
    provider = _LegacyProvider(content="one two three")  # 13 chars
    out = list(iter_stream_with_usage(provider, [Message("user", "go")]))
    tokens = [t for t, r in out if t]
    reports = [r for _, r in out if r is not None]
    assert tokens == ["one", "two", "three"]
    assert len(reports) == 1
    report = reports[0]
    assert report.source == "fallback"
    assert report.prompt_tokens == 2  # "go"


def test_iter_stream_with_usage_uses_native_when_available():
    class _StreamNative(_NativeProvider):
        def iter_stream_with_usage(self, messages):
            yield "tok", None
            yield "", UsageReport(
                prompt_tokens=1, completion_tokens=1, total_tokens=2,
                provider="native", model="m", source="api",
            )

    provider = _StreamNative()
    out = list(iter_stream_with_usage(provider, [Message("user", "x")]))
    reports = [r for _, r in out if r is not None]
    assert reports[0].source == "api"


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


def test_dm_agent_stream_with_usage_emits_report():
    provider = _LegacyProvider(content="one two")
    agent = _build_dm(provider)
    out = list(agent.stream_with_usage("olhar"))
    toks = [t for t, _ in out if t]
    reports = [r for _, r in out if r is not None]
    assert toks == ["one", "two"]
    assert len(reports) == 1
    assert reports[0].source == "fallback"


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
