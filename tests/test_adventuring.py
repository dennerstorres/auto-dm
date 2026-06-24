"""Tests for engine/adventuring.py (rests, falling, suffocation)."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.adventuring import (
    LongRestResult,
    ShortRestResult,
    SuffocationState,
    falling_damage,
    hold_breath_round,
    long_rest,
    short_rest,
    start_suffocation,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Condition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fighter() -> Character:
    return Character(
        id="c1", name="Conan", race="Human", class_="Fighter", level=1,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(strength=16, dexterity=14, constitution=14,
                                 intelligence=10, wisdom=12, charisma=10),
        hp_current=4, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
    )


@pytest.fixture
def wizard() -> Character:
    return Character(
        id="c2", name="Merlin", race="Human", class_="Wizard", level=3,
        background="Sage", alignment="NG",
        abilities=AbilityScores(strength=8, dexterity=14, constitution=12,
                                 intelligence=16, wisdom=12, charisma=10),
        hp_current=8, hp_max=18, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=3,
    )


# ---------------------------------------------------------------------------
# Short rest
# ---------------------------------------------------------------------------


class TestShortRest:
    def test_default_spends_all_dice(self, fighter: Character) -> None:
        result = short_rest(fighter, rng=random.Random(1))
        assert result.hit_dice_spent == 1
        assert result.hit_dice_remaining_after == 0
        assert fighter.hp_current > 4

    def test_explicit_spend_count(self, fighter: Character) -> None:
        # Only 1 die available
        result = short_rest(fighter, hit_dice_to_spend=1, rng=random.Random(1))
        assert result.hit_dice_spent == 1

    def test_zero_dice_no_heal(self, fighter: Character) -> None:
        fighter.hit_dice_remaining = 0
        result = short_rest(fighter, rng=random.Random(1))
        assert result.hp_recovered == 0
        assert result.hit_dice_spent == 0

    def test_caps_at_max_hp(self, fighter: Character) -> None:
        fighter.hp_current = 11  # already nearly full
        result = short_rest(fighter, rng=random.Random(1))
        assert fighter.hp_current <= fighter.hp_max

    def test_does_not_exceed_remaining(self, fighter: Character) -> None:
        result = short_rest(fighter, hit_dice_to_spend=99, rng=random.Random(1))
        assert result.hit_dice_spent == 1  # only 1 available

    def test_wizard_d6_face(self, wizard: Character) -> None:
        wizard.hp_current = 1
        wizard.hit_dice_remaining = 2
        result = short_rest(wizard, hit_dice_to_spend=2, rng=random.Random(1))
        # 2d6 + CON mod (12 CON = +1). Min 2, max 14.
        assert result.hp_recovered >= 2
        assert result.hp_recovered <= 14

    def test_con_mod_applied(self, wizard: Character) -> None:
        # 12 CON = +1 mod. With d6, min per die is max(1, d6 + 1).
        wizard.hp_current = 1
        result = short_rest(wizard, hit_dice_to_spend=3, rng=random.Random(42))
        assert result.hp_recovered >= 3  # at least 1 per die


# ---------------------------------------------------------------------------
# Long rest
# ---------------------------------------------------------------------------


class TestLongRest:
    def test_full_hp(self, fighter: Character) -> None:
        fighter.hp_current = 0
        result = long_rest(fighter)
        assert fighter.hp_current == fighter.hp_max
        assert result.hp_now == fighter.hp_max

    def test_hit_dice_recover_half_min_one(self, fighter: Character) -> None:
        # Level 1, half of 1 = 0, min 1.
        fighter.hit_dice_remaining = 0
        result = long_rest(fighter)
        assert fighter.hit_dice_remaining >= 1
        assert result.hit_dice_recovered >= 1

    def test_hit_dice_recover_half_level_4(self) -> None:
        char = Character(
            id="c", name="X", race="Human", class_="Fighter", level=4,
            background="Soldier", alignment="CN",
            abilities=AbilityScores.all_seven(),
            hp_current=10, hp_max=30, armor_class=16, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=0,
        )
        long_rest(char)
        assert char.hit_dice_remaining == 2  # half of 4

    def test_clears_long_rest_conditions(self, fighter: Character) -> None:
        fighter.conditions.extend([
            Condition.POISONED, Condition.FRIGHTENED, Condition.BLINDED,
        ])
        result = long_rest(fighter)
        assert Condition.POISONED not in fighter.conditions
        assert Condition.FRIGHTENED not in fighter.conditions
        assert Condition.BLINDED not in fighter.conditions
        assert "poisoned" in result.conditions_cleared

    def test_does_not_clear_grappled_restrained(self, fighter: Character) -> None:
        fighter.conditions.extend([Condition.GRAPPLED, Condition.RESTRAINED])
        long_rest(fighter)
        # Grappled/Restrained don't clear on long rest
        assert Condition.GRAPPLED in fighter.conditions
        assert Condition.RESTRAINED in fighter.conditions

    def test_reduces_exhaustion_by_one(self, fighter: Character) -> None:
        fighter.exhaustion_level = 4
        result = long_rest(fighter)
        assert fighter.exhaustion_level == 3
        assert result.exhaustion_reduced == 1

    def test_floors_exhaustion_at_zero(self, fighter: Character) -> None:
        fighter.exhaustion_level = 1
        long_rest(fighter)
        assert fighter.exhaustion_level == 0

    def test_resets_death_saves(self, fighter: Character) -> None:
        fighter.death_save_failures = 2
        fighter.death_save_successes = 1
        long_rest(fighter)
        assert fighter.death_save_failures == 0
        assert fighter.death_save_successes == 0


# ---------------------------------------------------------------------------
# Falling damage
# ---------------------------------------------------------------------------


class TestFallingDamage:
    def test_zero_distance(self) -> None:
        assert falling_damage(0) == 0
        assert falling_damage(-5) == 0

    def test_10_feet(self) -> None:
        # 1d6 — between 1 and 6
        for seed in range(20):
            d = falling_damage(10, rng=random.Random(seed))
            assert 1 <= d <= 6

    def test_50_feet(self) -> None:
        for seed in range(20):
            d = falling_damage(50, rng=random.Random(seed))
            assert 5 <= d <= 30  # 5d6

    def test_capped_at_20d6(self) -> None:
        # 200 ft = 20d6; 1000 ft still capped at 20d6
        for seed in range(10):
            d = falling_damage(1000, rng=random.Random(seed))
            assert 20 <= d <= 120  # 20d6

    def test_intermediate_distances(self) -> None:
        # 15 ft -> 1d6 (PHB: 1d6 per 10 ft, rounded down)
        for seed in range(10):
            d = falling_damage(15, rng=random.Random(seed))
            assert 1 <= d <= 6


# ---------------------------------------------------------------------------
# Suffocation
# ---------------------------------------------------------------------------


class TestSuffocation:
    def test_con_mod_positive(self, fighter: Character) -> None:
        # CON 14 -> mod +2 -> 3 rounds
        state = start_suffocation(fighter)
        assert state.max_rounds == 3
        assert state.is_suffocating is False

    def test_con_mod_zero(self) -> None:
        char = Character(
            id="c", name="X", race="Human", class_="Wizard", level=1,
            background="Sage", alignment="NG",
            abilities=AbilityScores(strength=8, dexterity=14, constitution=10,
                                     intelligence=14, wisdom=12, charisma=10),
            hp_current=4, hp_max=6, armor_class=12, speed=30,
            proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=1,
        )
        state = start_suffocation(char)
        # CON 10 -> mod 0 -> 1 round (PHB minimum)
        assert state.max_rounds == 1

    def test_holding_breath(self, fighter: Character) -> None:
        state = start_suffocation(fighter)
        for _ in range(3):
            hold_breath_round(state)
        assert state.rounds_held == 3
        assert state.is_suffocating is False

    def test_suffocation_starts_after_max(self, fighter: Character) -> None:
        state = start_suffocation(fighter)
        for _ in range(4):  # 3 max + 1 over
            hold_breath_round(state)
        assert state.rounds_held == 4
        assert state.is_suffocating is True

    def test_rounds_remaining(self, fighter: Character) -> None:
        state = start_suffocation(fighter)
        assert state.rounds_remaining == 3
        hold_breath_round(state)
        assert state.rounds_remaining == 2
        hold_breath_round(state)
        hold_breath_round(state)
        assert state.rounds_remaining == 0
