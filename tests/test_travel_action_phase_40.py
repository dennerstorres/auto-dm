"""Phase 40c — travel action dispatch + narrative integration tests.

Covers the wiring between a `move` action carrying `params.travel_hours`
and ``engine/world.py::resolve_travel`` (see ``agents/narrative.py``):
extraction of ``travel_hours``/``biome``, the plain-move fallback when
absent, the mechanical payload shape, ``current_location`` updates, and
the ``NarrativeEntry.world_seed`` tagging on the follow-up narration
entry. ``resolve_travel`` itself is stubbed via monkeypatch here — its
actual encounter/loot/weather logic is covered by
``tests/test_world_engine_phase_40.py``; this file only tests that
``narrative.py`` calls it correctly and maps its result faithfully.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import auto_dm.agents.narrative as narrative_module
from auto_dm.agents import DMAgent, get_action_json_schema_description, process_player_action
from auto_dm.agents.prompts import DM_SYSTEM_PROMPT
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.world import WorldEvent, WorldEventList
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.state.manager import StateManager
from auto_dm.state.models import AbilityScores, Character, GameState


# ============================================================================
# Shared fixtures (mirrors tests/test_dm_agent.py's FakeProvider pattern)
# ============================================================================


def dm_response(narration: str, action_json: dict | None = None) -> str:
    if action_json is None:
        return narration
    import json

    return f"{narration}\n```action\n{json.dumps(action_json)}\n```"


class FakeProvider:
    def __init__(self, scripted: list[str] | None = None) -> None:
        self.scripted: list[str] = list(scripted or [""])
        self.calls: list[list[Message]] = []
        self.config = LLMConfig(name="fake", api_key="test", model="fake-model")
        self.name = "fake"

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        if not self.scripted:
            return ""
        if len(self.scripted) == 1:
            return self.scripted[0]
        return self.scripted.pop(0)

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages)


def _make_character() -> Character:
    return Character(
        id="p1", name="Hero", race="Human", **{"class": "Fighter"},
        level=3, background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=14, dexterity=12, constitution=13,
            intelligence=10, wisdom=11, charisma=8,
        ),
        hp_current=28, hp_max=28, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=3,
        is_player=True,
    )


@pytest.fixture
def state() -> StateManager:
    player = _make_character()
    return StateManager(GameState(
        campaign_name="travel-dispatch-test",
        started_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        current_location="Vila Inicial",
        party=[player],
        player_character_id=player.id,
    ))


def _stub_events(**overrides) -> WorldEventList:
    defaults = dict(events=[], seed="fake-seed", elapsed_minutes=0, combat_started=False)
    defaults.update(overrides)
    return WorldEventList(**defaults)


# ============================================================================
# travel_hours extraction / plain-move fallback
# ============================================================================


class TestPlainMoveFallback:
    def test_move_without_travel_hours_never_calls_resolve_travel(self, state, monkeypatch):
        def fail(*a, **k):
            raise AssertionError("resolve_travel should not be called")

        monkeypatch.setattr(narrative_module, "resolve_travel", fail)
        provider = FakeProvider(scripted=[
            dm_response("Você anda até a esquina.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "esquina"},
            }),
            "Você chega à esquina.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "ando até a esquina", agent)
        assert result.action_result.mechanical == {"destination": "esquina"}

    @pytest.mark.parametrize("travel_hours", [0, -3, "abc", None])
    def test_invalid_travel_hours_falls_back_to_plain_move(
        self, state, monkeypatch, travel_hours
    ):
        def fail(*a, **k):
            raise AssertionError("resolve_travel should not be called")

        monkeypatch.setattr(narrative_module, "resolve_travel", fail)
        provider = FakeProvider(scripted=[
            dm_response("Você segue em frente.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "adiante", "travel_hours": travel_hours},
            }),
            "Você segue.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "sigo", agent)
        assert "world_seed" not in result.action_result.mechanical


# ============================================================================
# Travel dispatch: params extraction, mechanical payload, state mutation
# ============================================================================


class TestTravelDispatch:
    def test_forwards_hours_and_biome_to_resolve_travel(self, state, monkeypatch):
        captured = {}

        def fake_resolve_travel(sm, hours, *, combat_engine=None, rng_seed=None, biome="road"):
            captured["state_manager"] = sm
            captured["hours"] = hours
            captured["biome"] = biome
            captured["combat_engine"] = combat_engine
            return _stub_events()

        monkeypatch.setattr(narrative_module, "resolve_travel", fake_resolve_travel)
        provider = FakeProvider(scripted=[
            dm_response("Vocês partem estrada afora.", {
                "action_type": "move", "actor_id": "p1",
                "params": {
                    "destination": "Vila do Rio",
                    "travel_hours": 6,
                    "biome": "forest",
                },
            }),
            "Depois de horas de caminhada, vocês chegam.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajamos 6 horas até a vila", agent)

        assert captured["state_manager"] is state
        assert captured["hours"] == 6.0
        assert captured["biome"] == "forest"
        assert captured["combat_engine"] is None

    def test_defaults_biome_to_road_when_omitted(self, state, monkeypatch):
        captured = {}

        def fake_resolve_travel(sm, hours, *, combat_engine=None, rng_seed=None, biome="road"):
            captured["biome"] = biome
            return _stub_events()

        monkeypatch.setattr(narrative_module, "resolve_travel", fake_resolve_travel)
        provider = FakeProvider(scripted=[
            dm_response("Vocês viajam.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"travel_hours": 2},
            }),
            "...",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajo", agent)
        assert captured["biome"] == "road"

    def test_forwards_combat_engine(self, state, monkeypatch):
        engine = CombatEngine()
        received = {}

        def fake_resolve_travel(sm, hours, *, combat_engine=None, rng_seed=None, biome="road"):
            received["combat_engine"] = combat_engine
            return _stub_events()

        monkeypatch.setattr(narrative_module, "resolve_travel", fake_resolve_travel)
        provider = FakeProvider(scripted=[
            dm_response("Vocês viajam.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"travel_hours": 4},
            }),
            "...",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajo", agent, combat_engine=engine)
        assert received["combat_engine"] is engine

    def test_mechanical_payload_shape(self, state, monkeypatch):
        monkeypatch.setattr(
            narrative_module,
            "resolve_travel",
            lambda *a, **k: _stub_events(
                events=[
                    WorldEvent(kind="weather", description="O clima muda para: chuva.", weather="chuva"),
                ],
                seed="fake-seed-123",
                elapsed_minutes=360,
                combat_started=False,
            ),
        )
        provider = FakeProvider(scripted=[
            dm_response("Vocês partem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "Vila do Rio", "travel_hours": 6},
            }),
            "Chove no caminho.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "viajamos 6 horas", agent)

        mech = result.action_result.mechanical
        assert mech["destination"] == "Vila do Rio"
        assert mech["travel_hours"] == 6.0
        assert mech["world_seed"] == "fake-seed-123"
        assert mech["elapsed_minutes"] == 360
        assert mech["combat_started"] is False
        assert mech["world_events"] == [
            {"kind": "weather", "description": "O clima muda para: chuva."}
        ]

    def test_updates_current_location_when_destination_given(self, state, monkeypatch):
        monkeypatch.setattr(narrative_module, "resolve_travel", lambda *a, **k: _stub_events())
        provider = FakeProvider(scripted=[
            dm_response("Vocês partem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "Torre Distante", "travel_hours": 4},
            }),
            "...",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajo até a torre", agent)
        assert state.state.current_location == "Torre Distante"

    def test_leaves_location_unchanged_without_destination(self, state, monkeypatch):
        monkeypatch.setattr(narrative_module, "resolve_travel", lambda *a, **k: _stub_events())
        original = state.state.current_location
        provider = FakeProvider(scripted=[
            dm_response("Vocês seguem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"travel_hours": 4},
            }),
            "...",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajo", agent)
        assert state.state.current_location == original

    def test_message_includes_hours_destination_and_event_descriptions(
        self, state, monkeypatch
    ):
        monkeypatch.setattr(
            narrative_module,
            "resolve_travel",
            lambda *a, **k: _stub_events(
                events=[WorldEvent(kind="weather", description="Chove forte.", weather="chuva")],
                elapsed_minutes=240,
            ),
        )
        provider = FakeProvider(scripted=[
            dm_response("Partem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "Torre", "travel_hours": 4},
            }),
            "Segue.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "viajo", agent)
        assert "4 horas" in result.action_result.message
        assert "Torre" in result.action_result.message
        assert "Chove forte." in result.action_result.message


# ============================================================================
# world_seed tagging on the narrative log
# ============================================================================


class TestWorldSeedTagging:
    def test_follow_up_entry_gets_tagged_with_seed(self, state, monkeypatch):
        monkeypatch.setattr(
            narrative_module,
            "resolve_travel",
            lambda *a, **k: _stub_events(seed="seed-abc-123"),
        )
        provider = FakeProvider(scripted=[
            dm_response("Partem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"travel_hours": 4},
            }),
            "A jornada segue tranquila.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "viajo", agent)

        last_entry = state.state.narrative_log[-1]
        assert last_entry.content == "A jornada segue tranquila."
        assert last_entry.content == result.follow_up_narration
        assert last_entry.world_seed == "seed-abc-123"

    def test_earlier_entries_are_not_tagged(self, state, monkeypatch):
        monkeypatch.setattr(
            narrative_module,
            "resolve_travel",
            lambda *a, **k: _stub_events(seed="seed-xyz"),
        )
        provider = FakeProvider(scripted=[
            dm_response("Partem.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"travel_hours": 4},
            }),
            "Chegam ao destino.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "viajo", agent)

        # player entry + initial DM narration should be untouched.
        for entry in state.state.narrative_log[:-1]:
            assert entry.world_seed is None

    def test_plain_move_never_gets_a_world_seed(self, state):
        provider = FakeProvider(scripted=[
            dm_response("Você anda até a esquina.", {
                "action_type": "move", "actor_id": "p1",
                "params": {"destination": "esquina"},
            }),
            "Você chega.",
        ])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "ando", agent)
        assert all(e.world_seed is None for e in state.state.narrative_log)


# ============================================================================
# Prompt documentation (Phase 40c)
# ============================================================================


class TestTravelPromptDocs:
    def test_system_prompt_documents_travel_params(self):
        assert "travel_hours" in DM_SYSTEM_PROMPT
        assert "biome" in DM_SYSTEM_PROMPT

    def test_action_schema_documents_travel_hours(self):
        assert "travel_hours" in get_action_json_schema_description()
