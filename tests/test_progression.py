"""Tests for engine/progression.py (ASI + Inspiration)."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.progression import (
    ASI_LEVELS,
    apply_asi,
    consume_pending_advantage,
    grant_inspiration,
    is_asi_level,
    spend_inspiration,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rogue() -> Character:
    return Character(
        id="c1", name="Lyra", race="Half-Elf", class_="Rogue", level=4,
        background="Criminal", alignment="CN",
        abilities=AbilityScores(strength=10, dexterity=16, constitution=12,
                                 intelligence=13, wisdom=12, charisma=14),
        hp_current=24, hp_max=24, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=4,
    )


@pytest.fixture
def fighter() -> Character:
    return Character(
        id="c2", name="Conan", race="Human", class_="Fighter", level=8,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(strength=16, dexterity=14, constitution=14,
                                 intelligence=10, wisdom=12, charisma=10),
        hp_current=50, hp_max=50, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=8,
    )


# ---------------------------------------------------------------------------
# ASI levels
# ---------------------------------------------------------------------------


class TestIsAsiLevel:
    @pytest.mark.parametrize("level", [4, 8, 12, 16, 19])
    def test_asi_levels_true(self, level: int) -> None:
        assert is_asi_level(level) is True

    @pytest.mark.parametrize("level", [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 20])
    def test_non_asi_levels_false(self, level: int) -> None:
        assert is_asi_level(level) is False

    def test_asi_levels_constant(self) -> None:
        assert ASI_LEVELS == frozenset({4, 8, 12, 16, 19})


# ---------------------------------------------------------------------------
# apply_asi
# ---------------------------------------------------------------------------


class TestApplyAsi:
    def test_plus_two_to_one_ability(self, rogue: Character) -> None:
        # Rogue wants DEX from 16 -> 18.
        apply_asi(rogue, Ability.DEX)
        assert rogue.abilities.dexterity == 18
        # Other abilities unchanged.
        assert rogue.abilities.strength == 10
        assert rogue.abilities.charisma == 14

    def test_plus_one_to_two_abilities(self, rogue: Character) -> None:
        # Rogue wants +1 DEX and +1 INT.
        apply_asi(rogue, Ability.DEX, secondary=Ability.INT)
        assert rogue.abilities.dexterity == 17
        assert rogue.abilities.intelligence == 14

    def test_cap_at_20_single(self, rogue: Character) -> None:
        # Bump CHA to 19, then +2 would exceed 20.
        rogue.abilities.charisma = 19
        with pytest.raises(ValueError, match="exceed 20"):
            apply_asi(rogue, Ability.CHA)

    def test_cap_at_20_split(self, rogue: Character) -> None:
        # STR at 20 cannot get +1.
        rogue.abilities.strength = 20
        with pytest.raises(ValueError, match="exceed 20"):
            apply_asi(rogue, Ability.STR, secondary=Ability.INT)

    def test_cap_at_20_secondary_split(self, rogue: Character) -> None:
        # STR already at 20 cannot take +1.
        rogue.abilities.strength = 20
        with pytest.raises(ValueError, match="exceed 20"):
            apply_asi(rogue, Ability.STR, secondary=Ability.CON)

    def test_rejects_same_ability_twice(self, rogue: Character) -> None:
        with pytest.raises(ValueError, match="two different abilities"):
            apply_asi(rogue, Ability.DEX, secondary=Ability.DEX)

    def test_returns_ability_scores(self, rogue: Character) -> None:
        result = apply_asi(rogue, Ability.DEX)
        assert isinstance(result, AbilityScores)
        assert result.dexterity == 18

    def test_at_cap_no_split(self, fighter: Character) -> None:
        # Fighter with STR 20 already maxed on a stat cannot get +2 to it,
        # but can take +1/+1 split on two different stats.
        fighter.abilities.strength = 20
        with pytest.raises(ValueError):
            apply_asi(fighter, Ability.STR)
        # +1 to two different stats works.
        fighter.abilities.wisdom = 13
        fighter.abilities.charisma = 12
        apply_asi(fighter, Ability.WIS, secondary=Ability.CHA)
        assert fighter.abilities.wisdom == 14
        assert fighter.abilities.charisma == 13


# ---------------------------------------------------------------------------
# Inspiration
# ---------------------------------------------------------------------------


class TestInspiration:
    def test_grant_when_none_returns_true(self, rogue: Character) -> None:
        assert rogue.inspiration is False
        assert grant_inspiration(rogue) is True
        assert rogue.inspiration is True

    def test_grant_when_already_inspired_returns_false(self, rogue: Character) -> None:
        rogue.inspiration = True
        assert grant_inspiration(rogue) is False
        # No stockpile.
        assert rogue.inspiration is True

    def test_spend_when_inspired_returns_true(self, rogue: Character) -> None:
        rogue.inspiration = True
        assert spend_inspiration(rogue) is True
        assert rogue.inspiration is False
        # Stacks one pending advantage.
        assert rogue.pending_advantage == 1

    def test_spend_when_not_inspired_returns_false(self, rogue: Character) -> None:
        assert rogue.inspiration is False
        assert spend_inspiration(rogue) is False
        assert rogue.pending_advantage == 0

    def test_spend_adds_to_pending_stack(self, rogue: Character) -> None:
        # If somehow we have multiple pending advantages, spend adds one.
        rogue.pending_advantage = 2
        rogue.inspiration = True
        spend_inspiration(rogue)
        assert rogue.pending_advantage == 3
        assert rogue.inspiration is False

    def test_consume_pending_when_available(self, rogue: Character) -> None:
        rogue.pending_advantage = 1
        assert consume_pending_advantage(rogue) is True
        assert rogue.pending_advantage == 0

    def test_consume_pending_when_empty(self, rogue: Character) -> None:
        assert rogue.pending_advantage == 0
        assert consume_pending_advantage(rogue) is False
        assert rogue.pending_advantage == 0

    def test_consume_decrements_stack(self, rogue: Character) -> None:
        rogue.pending_advantage = 3
        consume_pending_advantage(rogue)
        assert rogue.pending_advantage == 2

    def test_round_trip_grant_spend_consume(self, rogue: Character) -> None:
        # Full lifecycle: grant -> spend -> consume -> gone.
        grant_inspiration(rogue)
        spend_inspiration(rogue)
        assert consume_pending_advantage(rogue) is True
        # Now nothing left.
        assert consume_pending_advantage(rogue) is False