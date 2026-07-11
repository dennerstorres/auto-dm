"""Phase 51d-lite — SessionManager provider_signature rebuild tests.

Confirms that calling ``sm.get`` with a different ``provider_signature``
triggers a fresh agent build (with the new factory), and that the same
signature (or no signature) preserves the cached session untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


from auto_dm.state.models import GameState
from auto_dm.web.sessions import SessionManager


def _empty_state() -> dict[str, Any]:
    return {
        "campaign_name": "SigTest",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "",
        "party": [],
        "npcs": [],
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "p1",
        "active_conditions": [],
        "narrative_log": [],
    }


@dataclass
class _StubAgent:
    provider: Any = None
    provider_factory_name: str = ""

    def __repr__(self) -> str:
        return f"_StubAgent(factory={self.provider_factory_name!r})"


def _make_factory(name: str, calls: list[str]):
    def _factory() -> Any:
        calls.append(name)
        return _StubAgent(provider_factory_name=name)
    return _factory


async def test_get_without_signature_keeps_cache(app_instance):
    """The legacy call shape (no signature) returns the cached session unchanged."""
    sm: SessionManager = app_instance.state.session_manager
    state = GameState.model_validate(_empty_state())
    initial_calls: list[str] = []
    initial_factory = _make_factory("initial", initial_calls)
    sess = await sm.create(1, state, provider_factory=initial_factory, provider_signature=None)
    initial_dm = sess.dm_agent
    # Get without a signature → cache hit, NO rebuild.
    again = await sm.get(1, sess.session_id)
    assert again is sess  # same object — no rebuild
    assert again.dm_agent is initial_dm


async def test_get_with_matching_signature_keeps_cache(app_instance):
    """Same signature → cache hit, no rebuild."""
    sm: SessionManager = app_instance.state.session_manager
    state = GameState.model_validate(_empty_state())
    create_calls: list[str] = []
    factory = _make_factory("A", create_calls)
    sig = ("legacy", "minimax", "M3")
    sess = await sm.create(2, state, provider_factory=factory, provider_signature=sig)
    assert len(create_calls) == 1
    second = await sm.get(
        2, sess.session_id, provider_factory=factory, provider_signature=sig,
    )
    # Same factory function reference; rebuilt only if signature differs.
    assert second.dm_agent is sess.dm_agent
    assert len(create_calls) == 1


async def test_get_with_different_signature_rebuilds(app_instance):
    """Signature change → fresh DM + companion agents with the new factory."""
    sm: SessionManager = app_instance.state.session_manager
    state = GameState.model_validate(_empty_state())
    initial_calls: list[str] = []
    new_calls: list[str] = []
    initial_factory = _make_factory("A-initial", initial_calls)
    new_factory = _make_factory("B-new", new_calls)
    sig_a = ("byok", "openai", "gpt-5-mini")
    sig_b = ("byok", "anthropic", "claude-sonnet-5")
    sess = await sm.create(
        3, state, provider_factory=initial_factory, provider_signature=sig_a,
    )
    assert len(initial_calls) == 1
    # Provider switch in the next call: rebuild.
    again = await sm.get(
        3, sess.session_id, provider_factory=new_factory, provider_signature=sig_b,
    )
    assert again.dm_agent is not sess.dm_agent
    # Inspect the provider the DMAgent wraps — it should be the new factory's output.
    assert getattr(again.dm_agent.provider, "provider_factory_name", None) == "B-new"
    assert len(new_calls) == 1


async def test_signature_none_matches_cached_none(app_instance):
    """Both stored and requested signatures are None → no rebuild (back-compat)."""
    sm: SessionManager = app_instance.state.session_manager
    state = GameState.model_validate(_empty_state())
    factory = _make_factory("legacy-stub", [])
    sess = await sm.create(
        4, state, provider_factory=factory, provider_signature=None,
    )
    again = await sm.get(
        4, sess.session_id, provider_factory=factory, provider_signature=None,
    )
    assert again.dm_agent is sess.dm_agent
