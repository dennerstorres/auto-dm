"""Phase 25e tests: movement checks (climb/swim/grapple/shove) +
mount/vehicle loaders + MOUNT/DISMOUNT combat handlers.
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from auto_dm.engine.combat_engine import (
    CombatEngine,
    _ACTION_HANDLERS,
)
from auto_dm.engine.movement import (
    AbilityCheckResult,
    ContestResult,
    climb_check,
    forced_disadvantage_swim,
    grapple,
    shove,
    swim_check,
)
from auto_dm.phb import (
    Mount,
    Vehicle,
    VehicleType,
    get_mount,
    get_mounts,
    get_vehicle,
    get_vehicles,
    set_phb_root,
)
from auto_dm.phb.loader import load_mounts, load_vehicles
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Action,
    ActionType,
    Character,
    GameState,
    NPC,
    Proficiencies,
    Skill,
)


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    """Each test starts with the real PHB root."""
    from auto_dm.phb import get_phb_root as _gpr

    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_creature(
    cid: str = "hero",
    name: str = "Hero",
    *,
    str_score: int = 14,
    dex_score: int = 10,
    athletics_prof: bool = True,
    level: int = 5,
) -> Character:
    skills = [Skill.ATHLETICS] if athletics_prof else []
    return Character(
        id=cid,
        name=name,
        race="Human",
        **{"class": "Fighter"},
        level=level,
        background="Soldier",
        alignment="N",
        abilities=AbilityScores(
            strength=str_score,
            dexterity=dex_score,
            constitution=12,
            intelligence=10,
            wisdom=12,
            charisma=8,
        ),
        hp_current=30,
        hp_max=30,
        armor_class=14,
        speed=30,
        proficiency_bonus=3,
        hit_dice="1d10",
        hit_dice_remaining=level,
        proficiencies=Proficiencies(skills=skills),
        inventory=[],
    )


def _make_npc(
    nid: str = "goblin",
    name: str = "Goblin",
    *,
    str_score: int = 8,
) -> NPC:
    return NPC(
        id=nid,
        name=name,
        hp_current=7,
        hp_max=7,
        armor_class=12,
        speed=30,
        abilities=AbilityScores(
            strength=str_score,
            dexterity=14,
            constitution=10,
            intelligence=10,
            wisdom=8,
            charisma=8,
        ),
    )


def _make_state(party: list[Character], npcs: list[NPC]) -> tuple[GameState, StateManager]:
    state = GameState(
        campaign_name="test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
        npcs=npcs,
    )
    return state, StateManager(state)


# ===========================================================================
# Mount/Vehicle loader tests
# ===========================================================================


class TestLoadMounts:
    def test_eight_mounts_loaded(self):
        mounts = load_mounts(Path("data/phb"))
        assert len(mounts) == 8

    def test_warhorse_specs(self):
        warhorse = next(
            m for m in load_mounts(Path("data/phb"))
            if m.name == "Warhorse"
        )
        assert warhorse.speed_ft == 60
        assert warhorse.carrying_capacity_lb == 540
        assert warhorse.cost_gp == 400.0

    def test_camel_specs(self):
        camel = next(
            m for m in load_mounts(Path("data/phb"))
            if m.name == "Camel"
        )
        assert camel.speed_ft == 50
        assert camel.carrying_capacity_lb == 480
        assert camel.cost_gp == 50.0

    def test_donkey_or_mule_combined_name(self):
        # PHB table groups "Donkey or mule" as one row.
        donkey = next(
            (m for m in load_mounts(Path("data/phb"))
             if "donkey" in m.name.lower()),
            None,
        )
        assert donkey is not None
        assert donkey.speed_ft == 40


class TestLoadVehicles:
    def test_water_vehicles_have_speed(self):
        vehicles = load_vehicles(Path("data/phb"))
        water = [v for v in vehicles if v.vehicle_type == VehicleType.WATER]
        assert len(water) == 6
        # Rowboat speed includes a Unicode fraction: "1½ mph" -> 1.5
        rowboat = next(v for v in water if v.name == "Rowboat")
        assert rowboat.speed_mph == 1.5
        warship = next(v for v in water if v.name == "Warship")
        assert warship.speed_mph == 2.5

    def test_land_vehicles_have_weight(self):
        vehicles = load_vehicles(Path("data/phb"))
        land = [v for v in vehicles if v.vehicle_type == VehicleType.LAND]
        names = {v.name for v in land}
        # PHB drawn vehicles
        assert "Carriage" in names
        assert "Cart" in names
        assert "Chariot" in names
        assert "Wagon" in names
        # Specifics
        wagon = next(v for v in land if v.name == "Wagon")
        assert wagon.weight_lb == 400.0
        assert wagon.cost_gp == 35.0

    def test_saddle_subgroup_rows_skipped(self):
        # "~ Exotic", "~ Military", "~ Pack", "~ Riding" are subgroups
        # of "Saddle" — they shouldn't load as separate vehicles.
        land = [
            v for v in load_vehicles(Path("data/phb"))
            if v.vehicle_type == VehicleType.LAND
        ]
        names = {v.name for v in land}
        assert not any(n.startswith("~") for n in names)

    def test_multiplier_rows_skipped(self):
        # "Barding: ×4 / ×2" row has × in cells; must not load.
        land = [
            v for v in load_vehicles(Path("data/phb"))
            if v.vehicle_type == VehicleType.LAND
        ]
        assert "Barding" not in {v.name for v in land}


class TestMountVehicleLookups:
    def test_get_mount_case_insensitive(self):
        assert get_mount("warhorse") is not None
        assert get_mount("WARHORSE") is not None

    def test_get_mount_partial(self):
        # "horse" should match "Horse, draft" or "Horse, riding".
        assert get_mount("horse") is not None

    def test_get_mount_unknown(self):
        assert get_mount("Not a Mount") is None

    def test_get_vehicle_water_only(self):
        water = get_vehicles(vehicle_type=VehicleType.WATER)
        assert all(v.vehicle_type == VehicleType.WATER for v in water)
        assert len(water) == 6

    def test_get_vehicle_land_only(self):
        land = get_vehicles(vehicle_type=VehicleType.LAND)
        assert all(v.vehicle_type == VehicleType.LAND for v in land)

    def test_get_vehicle_partial(self):
        assert get_vehicle("long") is not None  # matches "Longship"
        assert get_vehicle("sail") is not None  # matches "Sailing ship"


# ===========================================================================
# Movement checks
# ===========================================================================


class TestClimbCheck:
    def test_basic_climb_check(self):
        hero = _make_creature(str_score=16)  # STR mod +3
        result = climb_check(hero, dc=10, rng=random.Random(7))
        assert isinstance(result, AbilityCheckResult)
        assert result.ability.value == "strength"
        assert result.skill == "athletics"
        # Modifier = STR mod +3 + prof +3 = +6
        assert result.modifier == 6

    def test_climb_check_success_low_dc(self):
        hero = _make_creature(str_score=20)  # STR mod +5
        # DC 5 — almost always succeeds unless nat 1
        # (with advantage you still can't lose to a nat 1).
        result = climb_check(hero, dc=5, rng=random.Random(0))
        assert result.total >= 5 or result.roll == 1  # nat 1 may still lose

    def test_climb_check_failure_high_dc(self):
        hero = _make_creature(str_score=8)  # STR mod -1, no prof
        # DC 30 — only way to succeed is nat 20; we expect failure
        # for most seeds.
        results = [
            climb_check(hero, dc=30, rng=random.Random(seed)).is_success
            for seed in range(20)
        ]
        assert not any(results)  # all 20 seeds fail

    def test_climb_check_proficiency_bonus_applied(self):
        # With athletics proficiency, modifier = STR mod + prof bonus.
        proficient = _make_creature(str_score=14, athletics_prof=True)
        not_proficient = _make_creature(str_score=14, athletics_prof=False)
        a = climb_check(proficient, dc=10, rng=random.Random(0))
        b = climb_check(not_proficient, dc=10, rng=random.Random(0))
        # Same d20, but modifiers differ by proficiency bonus (3).
        assert a.modifier - b.modifier == 3


class TestSwimCheck:
    def test_basic_swim_check(self):
        hero = _make_creature(str_score=14)
        result = swim_check(hero, dc=15, rng=random.Random(0))
        assert result.skill == "athletics"
        # Modifier = STR mod (+2) + prof (+3) = +5
        assert result.modifier == 5

    def test_swim_disadvantage_heavy_armor(self):
        # Heavy armor (plate) → disadvantage on swim (PHB p. 198).
        # Build a Character with a plate-armor item.
        hero = _make_creature()
        from auto_dm.state.models import Item, ItemType, ArmorProperties, EquippedSlots
        hero.equipped = EquippedSlots(
            armor=Item(
                name="Plate",
                type=ItemType.ARMOR,
                armor=ArmorProperties(base_ac=18, add_dex_modifier=False),
            ),
        )
        assert forced_disadvantage_swim(hero) is True

    def test_swim_disadvantage_no_armor(self):
        hero = _make_creature()
        assert forced_disadvantage_swim(hero) is False


class TestGrapple:
    def test_grapple_returns_contest(self):
        hero = _make_creature(str_score=18)  # STR mod +4
        target = _make_npc(str_score=10)  # STR mod +0
        result = grapple(hero, target, rng=random.Random(0))
        assert isinstance(result, ContestResult)
        assert result.action == "grapple"
        assert result.attacker_id == hero.id
        assert result.target_id == target.id

    def test_grapple_modifier_includes_proficiency(self):
        hero = _make_creature(str_score=14)  # STR +2
        result = grapple(hero, _make_npc(), rng=random.Random(0))
        # Hero has athletics proficiency -> +2 STR + 3 prof = +5
        assert result.attacker_modifier == 5

    def test_grapple_success_when_attacker_higher(self):
        # Strong hero + weak target.
        hero = _make_creature(str_score=20)  # STR +5
        target = _make_npc(str_score=4)  # STR -3
        results = [
            grapple(hero, target, rng=random.Random(seed)).is_success
            for seed in range(20)
        ]
        # Almost always wins except when hero rolls very low AND target high.
        assert sum(results) >= 10

    def test_grapple_failure_when_target_higher(self):
        # Weak hero vs strong target.
        hero = _make_creature(str_score=4)  # STR -3
        target = _make_npc(str_score=20)  # STR +5
        results = [
            grapple(hero, target, rng=random.Random(seed)).is_success
            for seed in range(50)
        ]
        # Expected ~26% success rate — well below 50%.
        assert sum(results) < 25


class TestShove:
    def test_shove_returns_contest(self):
        hero = _make_creature()
        target = _make_npc()
        result = shove(hero, target, rng=random.Random(0))
        assert result.action == "shove"

    def test_shove_modifier(self):
        hero = _make_creature(str_score=16)  # STR +3
        result = shove(hero, _make_npc(), rng=random.Random(0))
        # STR +3 + prof +3 = +6
        assert result.attacker_modifier == 6


# ===========================================================================
# State mount fields
# ===========================================================================


class TestMountStateFields:
    def test_character_default_not_mounted(self):
        hero = _make_creature()
        assert hero.is_mounted is False
        assert hero.mount_id is None

    def test_npc_default_no_rider(self):
        goblin = _make_npc()
        assert goblin.is_mount is False
        assert goblin.rider_id is None
        assert goblin.is_vehicle is False


# ===========================================================================
# MOUNT/DISMOUNT handlers
# ===========================================================================


class TestMountHandler:
    def test_mount_rider_and_creature(self):
        hero = _make_creature(cid="hero")
        horse = _make_npc(nid="horse", name="Warhorse")
        state, sm = _make_state([hero], [horse])
        sm.start_combat([hero.id, horse.id])

        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=hero.id,
            action_type=ActionType.MOUNT,
            target_id=horse.id,
        )
        result = engine.execute_action(sm, action)
        assert result.success
        assert hero.is_mounted is True
        assert hero.mount_id == horse.id
        assert horse.rider_id == hero.id

    def test_mount_target_required(self):
        hero = _make_creature()
        horse = _make_npc()
        state, sm = _make_state([hero], [horse])
        sm.start_combat([hero.id, horse.id])
        engine = CombatEngine(rng=random.Random(0))

        action = Action(
            actor_id=hero.id,
            action_type=ActionType.MOUNT,
            target_id=None,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_mount_unknown_target(self):
        hero = _make_creature()
        state, sm = _make_state([hero], [])
        sm.start_combat([hero.id])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=hero.id,
            action_type=ActionType.MOUNT,
            target_id="ghost",
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_cannot_mount_if_already_mounted(self):
        hero = _make_creature()
        horse1 = _make_npc(nid="horse1", name="Horse1")
        horse2 = _make_npc(nid="horse2", name="Horse2")
        state, sm = _make_state([hero], [horse1, horse2])
        sm.start_combat([hero.id, horse1.id, horse2.id])
        engine = CombatEngine(rng=random.Random(0))

        # First mount succeeds
        engine.execute_action(
            sm, Action(
                actor_id=hero.id, action_type=ActionType.MOUNT,
                target_id=horse1.id,
            )
        )
        assert hero.is_mounted

        # Second mount fails
        result = engine.execute_action(
            sm, Action(
                actor_id=hero.id, action_type=ActionType.MOUNT,
                target_id=horse2.id,
            )
        )
        assert result.success is False
        # Still mounted on horse1
        assert hero.mount_id == horse1.id


class TestDismountHandler:
    def test_dismount_clears_state(self):
        hero = _make_creature()
        horse = _make_npc(nid="horse")
        state, sm = _make_state([hero], [horse])
        sm.start_combat([hero.id, horse.id])
        engine = CombatEngine(rng=random.Random(0))

        # Mount first
        engine.execute_action(
            sm, Action(
                actor_id=hero.id, action_type=ActionType.MOUNT,
                target_id=horse.id,
            )
        )
        # Dismount
        result = engine.execute_action(
            sm, Action(
                actor_id=hero.id, action_type=ActionType.DISMOUNT,
            )
        )
        assert result.success
        assert hero.is_mounted is False
        assert hero.mount_id is None
        assert horse.rider_id is None

    def test_dismount_when_not_mounted_fails(self):
        hero = _make_creature()
        state, sm = _make_state([hero], [])
        sm.start_combat([hero.id])
        engine = CombatEngine(rng=random.Random(0))

        result = engine.execute_action(
            sm, Action(
                actor_id=hero.id, action_type=ActionType.DISMOUNT,
            )
        )
        assert result.success is False


class TestHandlersRegistered:
    def test_mount_in_handlers_dict(self):
        assert ActionType.MOUNT in _ACTION_HANDLERS

    def test_dismount_in_handlers_dict(self):
        assert ActionType.DISMOUNT in _ACTION_HANDLERS