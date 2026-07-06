"""Phase 40b — engine/world.py tests: roll_encounter, compute_loot, resolve_travel.

Uses a scripted RNG stand-in (``FixedRandom``) for the low-level roll
functions (they accept ``rng`` explicitly) and a module-local monkeypatch
of ``auto_dm.engine.world.random`` for ``resolve_travel`` (which builds
its own ``random.Random(seed)`` internally from a seed string). The patch
only rebinds the ``random`` name inside ``world``'s own module namespace,
so it never touches the real stdlib ``random`` module or other modules
(e.g. ``combat_engine.py``) that also do ``import random``.
"""
from __future__ import annotations

from datetime import datetime

import auto_dm.engine.world as world_module
from auto_dm.character import CharacterBuilder
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.world import (
    LootDrop,
    compute_loot,
    resolve_travel,
    roll_encounter,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState, NPC, AbilityScores


class FixedRandom:
    """Deterministic stand-in for ``random.Random``: pops queued values in
    the exact order the code under test calls ``randint``/``random``."""

    def __init__(self, *values):
        self._values = list(values)

    def randint(self, a, b):  # noqa: ARG002 — bounds ignored, values are scripted
        return self._values.pop(0)

    def random(self):
        return self._values.pop(0)


class _StubRandomModule:
    """Fakes the bits of the ``random`` module ``resolve_travel`` uses."""

    def __init__(self, instance: FixedRandom) -> None:
        self._instance = instance

    def Random(self, seed=None):  # noqa: N802 — matches random.Random's name
        return self._instance


def _patch_rng(monkeypatch, *values: float) -> FixedRandom:
    fixed = FixedRandom(*values)
    monkeypatch.setattr(world_module, "random", _StubRandomModule(fixed))
    return fixed


def make_player(level: int = 1, gold_gp: float = 0.0):
    draft = (
        CharacterBuilder()
        .with_name("Hero")
        .with_race("Human")
        .with_class("Fighter")
        .with_background("Soldier")
        .with_alignment("LN")
        .with_level(level)
        .with_standard_array()
        .with_skills(["athletics"])
        .build()
    )
    c = draft.character
    c.is_player = True
    c.gold_gp = gold_gp
    return c


def make_state(*, player=None, npcs=None, **overrides) -> StateManager:
    player = player or make_player()
    state = GameState(
        campaign_name="world-engine-test",
        started_at=datetime.now(),
        party=[player],
        player_character_id=player.id,
        npcs=npcs or [],
        **overrides,
    )
    return StateManager(state)


def make_npc(npc_id: str, name: str = "Existing") -> NPC:
    return NPC(
        id=npc_id, name=name, hp_current=5, hp_max=5,
        armor_class=10, speed=30, abilities=AbilityScores.all_seven(),
    )


# ============================================================================
# roll_encounter
# ============================================================================


class TestRollEncounter:
    def test_no_encounter_row_returns_none(self):
        sm = make_state()
        event = roll_encounter(sm, "road", "day", rng=FixedRandom(1))
        assert event is None
        assert sm.state.npcs == []

    def test_encounter_row_spawns_npcs(self):
        sm = make_state()
        # roll=60 -> Bandit row (56-70, count "2d4"); 2d4 rolls of 3,2 -> 5.
        event = roll_encounter(sm, "road", "day", rng=FixedRandom(60, 3, 2))
        assert event is not None
        assert event.kind == "encounter"
        assert event.description == "Salteadores bloqueiam a estrada exigindo pedágio."
        assert len(event.npc_ids) == 5
        assert len(sm.state.npcs) == 5
        assert all(n.name == "Bandit" for n in sm.state.npcs)
        assert all(n.is_hostile for n in sm.state.npcs)

    def test_multi_monster_row_spawns_all_stacks(self):
        sm = make_state()
        # roll=99 -> Bandit Captain (fixed "1", no rng) + Bandit "2d4" (3,2 -> 5).
        event = roll_encounter(sm, "road", "day", rng=FixedRandom(99, 3, 2))
        assert event is not None
        assert len(event.npc_ids) == 6
        names = sorted(n.name for n in sm.state.npcs)
        assert names == sorted(["Bandit Captain"] + ["Bandit"] * 5)

    def test_unknown_biome_time_of_day_returns_none(self):
        sm = make_state()
        event = roll_encounter(sm, "swamp", "day", rng=FixedRandom(1))
        assert event is None

    def test_npc_id_collisions_get_suffixed(self):
        sm = make_state(npcs=[make_npc("bandit")])
        event = roll_encounter(sm, "road", "day", rng=FixedRandom(60, 1, 1))
        assert event is not None
        assert event.npc_ids == ["bandit_2", "bandit_3"]


# ============================================================================
# compute_loot
# ============================================================================


class TestComputeLoot:
    def test_empty_row_yields_no_gold_no_items(self):
        drop = compute_loot("individual", 10)
        assert drop == LootDrop(gold_gp=0.0, items=[], notes="Nada de interessante.")

    def test_gold_only_row(self):
        drop = compute_loot("individual", 60, rng=FixedRandom(4, 5))
        assert drop.gold_gp == 9.0
        assert drop.items == []

    def test_item_only_row(self):
        drop = compute_loot("individual", 90)
        assert drop.gold_gp == 0.0
        assert drop.items == ["Potion of Healing"]

    def test_gold_and_item_row(self):
        drop = compute_loot("individual", 99, rng=FixedRandom(3))
        assert drop.gold_gp == 3.0
        assert drop.items == ["Dagger"]

    def test_unknown_table_id_returns_empty(self):
        drop = compute_loot("no_such_table", 50)
        assert drop == LootDrop()


# ============================================================================
# resolve_travel
# ============================================================================


class TestResolveTravel:
    def test_zero_hours_is_noop(self):
        sm = make_state()
        result = resolve_travel(sm, 0)
        assert result.events == []
        assert result.elapsed_minutes == 0
        assert sm.state.elapsed_game_minutes == 0

    def test_negative_hours_clamped_to_zero(self):
        sm = make_state()
        result = resolve_travel(sm, -3)
        assert result.events == []
        assert sm.state.elapsed_game_minutes == 0

    def test_seed_is_derived_from_campaign_seed_and_clock(self):
        sm = make_state()
        result = resolve_travel(sm, 0)
        assert result.seed == f"{sm.state.campaign_seed}:0"

    def test_explicit_seed_overrides_default(self):
        sm = make_state()
        result = resolve_travel(sm, 0, rng_seed="my-seed")
        assert result.seed == "my-seed"

    def test_advances_clock_and_weather_when_no_matching_table(self, monkeypatch):
        sm = make_state()
        _patch_rng(monkeypatch, 15)  # weather roll only (no "void_*" table exists)
        result = resolve_travel(sm, 2, biome="void")
        assert result.elapsed_minutes == 120
        assert sm.state.elapsed_game_minutes == 120
        assert sm.state.time_of_day == "madrugada"  # minute 120 == 02:00
        assert sm.state.weather == "ameno"  # weather.json roll 10-16
        assert [e.kind for e in result.events] == ["weather"]
        assert result.combat_started is False

    def test_encounter_starts_combat_and_stops_clock_early(self, monkeypatch):
        sm = make_state()
        combat_engine = CombatEngine()
        # block1 @ clock=240min (night, road_night table): roll=60 -> Bandit
        # (2d4 -> 3,2 -> 5), then a weather roll of 5.
        _patch_rng(monkeypatch, 60, 3, 2, 5)
        result = resolve_travel(sm, 8, combat_engine=combat_engine, biome="road")
        assert result.combat_started is True
        assert result.elapsed_minutes == 240  # stopped at the encounter, not 480
        assert sm.state.elapsed_game_minutes == 240
        assert sm.state.in_combat is True
        assert len(result.encounters) == 1
        assert len(result.encounters[0].npc_ids) == 5

    def test_encounter_without_combat_engine_spawns_but_does_not_start_combat(
        self, monkeypatch
    ):
        sm = make_state()
        _patch_rng(monkeypatch, 60, 3, 2, 5)
        result = resolve_travel(sm, 8, combat_engine=None, biome="road")
        assert result.combat_started is False
        assert sm.state.in_combat is False
        assert len(sm.state.npcs) == 5
        assert result.elapsed_minutes == 240  # travel still halted by the encounter

    def test_no_encounter_consumes_full_requested_duration(self, monkeypatch):
        sm = make_state()
        # Two blocks (240min each), roll=10 is "no encounter" in both the
        # night (1-50) and day (1-55) road tables, then a weather roll.
        _patch_rng(monkeypatch, 10, 10, 5)
        result = resolve_travel(sm, 8, biome="road")
        assert result.elapsed_minutes == 480
        assert sm.state.elapsed_game_minutes == 480
        assert result.encounters == []
        assert sm.state.in_combat is False

    def test_loot_check_after_a_full_day_of_travel(self, monkeypatch):
        sm = make_state()
        # 6 no-encounter blocks (240*6=1440min) + 1 weather roll + a loot
        # hit (chance roll < 0.15) resolving to the Potion of Healing row.
        _patch_rng(monkeypatch, 10, 10, 10, 10, 10, 10, 5, 0.05, 90)
        result = resolve_travel(sm, 24, biome="road")
        assert sm.state.elapsed_game_minutes == 1440
        loot_events = [e for e in result.events if e.kind == "loot"]
        assert len(loot_events) == 1
        assert loot_events[0].loot.items == ["Potion of Healing"]
        player = sm.get_character(sm.state.player_character_id)
        assert any(i.name == "Potion of Healing" for i in player.inventory)

    def test_loot_roll_below_threshold_yields_no_loot_event(self, monkeypatch):
        sm = make_state()
        # Same 6 no-encounter blocks + weather, but the loot chance roll
        # (0.9) misses the 0.15 threshold -> no loot table roll consumed.
        _patch_rng(monkeypatch, 10, 10, 10, 10, 10, 10, 5, 0.9)
        result = resolve_travel(sm, 24, biome="road")
        assert [e for e in result.events if e.kind == "loot"] == []

    def test_cooldown_gates_the_check_across_calls(self):
        sm = make_state()
        resolve_travel(sm, 0.3, biome="void")  # 18min < 30min cooldown -> skipped
        assert sm.state.last_world_event_minute == 0
        assert sm.state.elapsed_game_minutes == 18
        resolve_travel(sm, 0.3, biome="void")  # cumulative 36min since 0 -> proceeds
        assert sm.state.last_world_event_minute == 36
        assert sm.state.elapsed_game_minutes == 36

    def test_unresolved_loot_item_names_do_not_crash(self, monkeypatch):
        sm = make_state()
        monkeypatch.setattr(world_module, "resolve_catalog_item", lambda name: None)
        _patch_rng(monkeypatch, 10, 10, 10, 10, 10, 10, 5, 0.05, 90)
        result = resolve_travel(sm, 24, biome="road")
        loot_events = [e for e in result.events if e.kind == "loot"]
        assert len(loot_events) == 1
        assert loot_events[0].loot.items == []
        assert loot_events[0].loot.unresolved_items == ["Potion of Healing"]

    def test_reproducible_with_explicit_seed(self):
        sm1 = make_state()
        sm2 = make_state()
        r1 = resolve_travel(sm1, 24, rng_seed="fixed-seed-xyz", biome="road")
        r2 = resolve_travel(sm2, 24, rng_seed="fixed-seed-xyz", biome="road")
        assert [(e.kind, e.description) for e in r1.events] == [
            (e.kind, e.description) for e in r2.events
        ]
        assert r1.elapsed_minutes == r2.elapsed_minutes
        assert sm1.state.weather == sm2.state.weather
        assert sorted(n.name for n in sm1.state.npcs) == sorted(
            n.name for n in sm2.state.npcs
        )
