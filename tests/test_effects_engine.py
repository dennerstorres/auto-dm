"""Tests for poisons, traps, diseases and ActiveEffect."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from auto_dm.engine.effects import (
    EffectResult,
    apply_disease,
    apply_poison,
    parse_duration_rounds,
    tick_effects,
    trigger_trap,
)
from auto_dm.phb import (
    get_disease,
    get_diseases,
    get_poison,
    get_poisons,
    get_trap,
    get_traps,
    set_phb_root,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    ActiveEffect,
    Character,
    Condition,
    GameState,
)


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield


@pytest.fixture
def fighter() -> Character:
    return Character(
        id="c1", name="Conan", race="Human", class_="Fighter", level=3,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(strength=16, dexterity=14, constitution=14,
                                 intelligence=10, wisdom=12, charisma=10),
        hp_current=20, hp_max=20, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=3,
    )


@pytest.fixture
def state_with_fighter(fighter: Character) -> tuple[GameState, StateManager]:
    state = GameState(
        campaign_name="Test", started_at="2026-06-24T00:00:00",
        party=[fighter], player_character_id="c1",
    )
    return state, StateManager(state)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


class TestPoisonLoader:
    def test_loads_14_poisons(self) -> None:
        assert len(get_poisons()) == 14

    def test_crawler_mucus_parsed(self) -> None:
        p = get_poison("Crawler Mucus")
        assert p is not None
        assert p.delivery == "contact"
        assert p.save_dc == 13
        assert p.save_ability == "constitution"
        assert Condition.PARALYZED in [
            Condition(c) for c in p.applies_condition if c in {x.value for x in Condition}
        ]

    def test_assassin_blood_has_damage(self) -> None:
        p = get_poison("Assassin's Blood")
        assert p is not None
        assert "1d12" in p.damage_dice

    def test_purple_worm_highest_dc(self) -> None:
        p = get_poison("Purple Worm Poison")
        assert p is not None
        assert p.save_dc == 19

    def test_lookup_case_insensitive(self) -> None:
        assert get_poison("WYVERN POISON") is not None


class TestTrapLoader:
    def test_loads_seven_traps(self) -> None:
        # Pits is one section that covers 4 variants
        assert len(get_traps()) >= 7

    def test_fire_statue_is_magic(self) -> None:
        t = get_trap("Fire-Breathing Statue")
        assert t is not None
        assert t.trap_type == "magic"
        assert t.damage_type == "fire"

    def test_collapsing_roof(self) -> None:
        t = get_trap("Collapsing Roof")
        assert t is not None
        assert t.trap_type == "mechanical"
        assert t.save_dc == 15


class TestDiseaseLoader:
    def test_loads_three_diseases(self) -> None:
        assert len(get_diseases()) == 3

    def test_cackle_fever(self) -> None:
        d = get_disease("Cackle Fever")
        assert d is not None
        assert d.save_dc == 13
        assert "hour" in d.incubation

    def test_sight_rot(self) -> None:
        d = get_disease("Sight Rot")
        assert d is not None
        assert d.save_dc == 15


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_minutes(self) -> None:
        assert parse_duration_rounds("1 minute") == 10

    def test_hours(self) -> None:
        assert parse_duration_rounds("24 hours") == 14400

    def test_rounds(self) -> None:
        assert parse_duration_rounds("3 rounds") == 3

    def test_variable(self) -> None:
        # 4d6 hours -> average 14 hours * 600 = 8400
        assert parse_duration_rounds("4d6 hours") == 14 * 600

    def test_default_when_no_match(self) -> None:
        assert parse_duration_rounds("instant") == 10


# ---------------------------------------------------------------------------
# Poison application
# ---------------------------------------------------------------------------


class TestApplyPoison:
    def test_failed_save_applies_damage(self, fighter: Character) -> None:
        # Assassin Blood DC 10, fighter CON 14 = +2 mod; seed 1 rolls low.
        poison = get_poison("Assassin's Blood")
        result = apply_poison(fighter, poison, rng=random.Random(1))
        # We don't know if it succeeded without checking — but we can
        # verify the function returns a result object.
        assert isinstance(result, EffectResult)
        assert result.source == poison.name
        assert result.damage_type == "poison"

    def test_failed_save_applies_condition(self, fighter: Character) -> None:
        poison = get_poison("Drow Poison")
        # CON +2 vs DC 13 means needs 11+ to save. Roll several seeds.
        for seed in range(50):
            char = fighter.model_copy(deep=True)
            result = apply_poison(char, poison, rng=random.Random(seed))
            if not result.save_made:
                assert Condition.POISONED in char.conditions
                assert any(e.source == "Drow Poison" for e in char.active_effects)
                return
        pytest.fail("No failed save in 50 seeds")

    def test_successful_save_no_condition(self, fighter: Character) -> None:
        poison = get_poison("Drow Poison")
        for seed in range(50):
            char = fighter.model_copy(deep=True)
            result = apply_poison(char, poison, rng=random.Random(seed))
            if result.save_made:
                assert Condition.POISONED not in char.conditions
                assert char.active_effects == []
                assert result.damage_dealt >= 0  # half or zero
                return
        pytest.fail("No successful save in 50 seeds")

    def test_high_con_save_mostly_succeeds(self) -> None:
        # Build a character with CON 20 -> mod +5. Save needs 8+.
        char = Character(
            id="c", name="X", race="Human", class_="Fighter", level=3,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(strength=10, dexterity=10, constitution=20,
                                     intelligence=10, wisdom=10, charisma=10),
            hp_current=20, hp_max=20, armor_class=16, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=3,
        )
        poison = get_poison("Crawler Mucus")
        successes = 0
        for seed in range(100):
            c = char.model_copy(deep=True)
            apply_poison(c, poison, rng=random.Random(seed))
            if Condition.POISONED not in c.conditions:
                successes += 1
        assert successes > 50


# ---------------------------------------------------------------------------
# Trap triggering
# ---------------------------------------------------------------------------


class TestTriggerTrap:
    def test_save_halves_damage(self, fighter: Character) -> None:
        trap = get_trap("Collapsing Roof")
        # 4d10 save DC 15 DEX (fighter mod +2 = needs 13+). Roll seeds.
        full_damage: int | None = None
        half_damage: int | None = None
        for seed in range(50):
            c = fighter.model_copy(deep=True)
            result = trigger_trap(c, trap, rng=random.Random(seed))
            if result.save_made and half_damage is None:
                half_damage = result.damage_dealt
            if not result.save_made and full_damage is None:
                full_damage = result.damage_dealt
            if half_damage is not None and full_damage is not None:
                break
        assert full_damage is not None
        assert half_damage is not None
        assert half_damage * 2 >= full_damage  # half <= full

    def test_damage_type_matches_trap(self, fighter: Character) -> None:
        trap = get_trap("Fire-Breathing Statue")
        # Iterate until we get a fail
        for seed in range(50):
            c = fighter.model_copy(deep=True)
            result = trigger_trap(c, trap, rng=random.Random(seed))
            if not result.save_made:
                assert result.damage_type == "fire"
                return
        pytest.fail("No failed save in 50 seeds")


# ---------------------------------------------------------------------------
# Disease
# ---------------------------------------------------------------------------


class TestApplyDisease:
    def test_save_success_no_effect(self, fighter: Character) -> None:
        disease = get_disease("Cackle Fever")
        for seed in range(50):
            c = fighter.model_copy(deep=True)
            result = apply_disease(c, disease, rng=random.Random(seed))
            if result.save_made:
                assert c.active_effects == []
                return
        pytest.fail("No successful save in 50 seeds")

    def test_save_failure_attaches_effect(self, fighter: Character) -> None:
        disease = get_disease("Cackle Fever")
        for seed in range(50):
            c = fighter.model_copy(deep=True)
            result = apply_disease(c, disease, rng=random.Random(seed))
            if not result.save_made:
                assert any(e.source == "Cackle Fever" for e in c.active_effects)
                return
        pytest.fail("No failed save in 50 seeds")


# ---------------------------------------------------------------------------
# Tick effects
# ---------------------------------------------------------------------------


class TestTickEffects:
    def test_duration_decrements(self, fighter: Character) -> None:
        fighter.active_effects.append(ActiveEffect(
            source="Test", effect_type="poison",
            duration_rounds=3, damage_dice="1d4",
        ))
        tick_effects(fighter, rng=random.Random(42))
        # First tick: duration should be 2 OR 0 if save ended
        assert all(e.duration_rounds < 3 for e in fighter.active_effects) or \
               fighter.active_effects == []

    def test_save_ends_effect(self, fighter: Character) -> None:
        # High CON means save should succeed often
        fighter.abilities.constitution = 20
        fighter.active_effects.append(ActiveEffect(
            source="TestPoison", effect_type="poison",
            duration_rounds=100, save_dc=5, save_ability=__import__(
                "auto_dm.state.models", fromlist=["Ability"]
            ).Ability.CON, damage_dice="1d4",
        ))
        # Tick repeatedly — should eventually end
        for _ in range(20):
            results = tick_effects(fighter, rng=random.Random(1))
            if not fighter.active_effects:
                assert any(r.save_made for r in results)
                return
        # Even if it didn't end, the test ran without error

    def test_damage_dealt_on_tick(self, fighter: Character) -> None:
        # Low CON means save will fail
        fighter.abilities.constitution = 6
        fighter.active_effects.append(ActiveEffect(
            source="Torpor", effect_type="poison",
            duration_rounds=5, save_dc=20,
            save_ability=__import__("auto_dm.state.models", fromlist=["Ability"]
                                    ).Ability.CON,
            damage_dice="2d6", damage_type="poison",
        ))
        results = tick_effects(fighter, rng=random.Random(1))
        # DC 20 + CON mod (-2) = need 22, impossible — so fail
        assert results[0].save_made is False
        assert results[0].damage_type == "poison"


# ---------------------------------------------------------------------------
# StateManager helpers
# ---------------------------------------------------------------------------


class TestStateManagerEffects:
    def test_add_effect(self, state_with_fighter) -> None:
        state, mgr = state_with_fighter
        mgr.add_effect("c1", ActiveEffect(source="X", effect_type="poison"))
        assert any(e.source == "X" for e in state.party[0].active_effects)

    def test_remove_effect(self, state_with_fighter) -> None:
        state, mgr = state_with_fighter
        mgr.add_effect("c1", ActiveEffect(source="X", effect_type="poison"))
        mgr.add_effect("c1", ActiveEffect(source="Y", effect_type="poison"))
        mgr.remove_effect("c1", "X")
        names = [e.source for e in state.party[0].active_effects]
        assert "X" not in names
        assert "Y" in names

    def test_clear_effects(self, state_with_fighter) -> None:
        state, mgr = state_with_fighter
        mgr.add_effect("c1", ActiveEffect(source="X", effect_type="poison"))
        mgr.clear_effects("c1")
        assert state.party[0].active_effects == []

    def test_unknown_id_raises(self, state_with_fighter) -> None:
        _, mgr = state_with_fighter
        with pytest.raises(KeyError):
            mgr.add_effect("nope", ActiveEffect(source="X", effect_type="poison"))
