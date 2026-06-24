"""Tests for engine/cover.py and the opportunity attack handler."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.combat import attack_roll
from auto_dm.engine.cover import (
    COVER_LEVELS,
    cover_ac_bonus,
    cover_dex_save_bonus,
    is_valid_cover,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    EquippedSlots,
    Item,
    ItemType,
    WeaponProperties,
)


@pytest.fixture
def fighter() -> Character:
    return Character(
        id="c1", name="Conan", race="Human", class_="Fighter", level=1,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(strength=16, dexterity=14, constitution=14,
                                 intelligence=10, wisdom=12, charisma=10),
        hp_current=12, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        equipped=EquippedSlots(
            main_hand=Item(
                name="Longsword",
                type=ItemType.WEAPON,
                weapon=WeaponProperties(damage_dice="1d8", damage_type="slashing"),
            ),
        ),
    )


@pytest.fixture
def orc() -> Character:
    return Character(
        id="o1", name="Orc", race="Orc", class_="Warrior", level=1,
        background="Tribal", alignment="CE",
        abilities=AbilityScores(strength=16, dexterity=12, constitution=16,
                                 intelligence=7, wisdom=11, charisma=10),
        hp_current=15, hp_max=15, armor_class=13, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=1,
    )


# ---------------------------------------------------------------------------
# Cover helpers
# ---------------------------------------------------------------------------


class TestCover:
    def test_none_zero_bonus(self) -> None:
        assert cover_ac_bonus("none") == 0
        assert cover_dex_save_bonus("none") == 0

    def test_half_plus_two(self) -> None:
        assert cover_ac_bonus("half") == 2
        assert cover_dex_save_bonus("half") == 2

    def test_three_quarters_plus_five(self) -> None:
        assert cover_ac_bonus("three_quarters") == 5
        assert cover_dex_save_bonus("three_quarters") == 5

    def test_total_unblockable(self) -> None:
        assert cover_ac_bonus("total") > 50

    def test_unknown_returns_zero(self) -> None:
        assert cover_ac_bonus("bogus") == 0

    def test_levels(self) -> None:
        assert "none" in COVER_LEVELS
        assert "half" in COVER_LEVELS
        assert "three_quarters" in COVER_LEVELS
        assert "total" in COVER_LEVELS

    def test_is_valid(self) -> None:
        assert is_valid_cover("half") is True
        assert is_valid_cover("bogus") is False


# ---------------------------------------------------------------------------
# Cover in attack roll
# ---------------------------------------------------------------------------


class TestAttackRollWithCover:
    def test_half_cover_harder_to_hit(
        self, fighter: Character, orc: Character,
    ) -> None:
        # Seed to find an attack that would hit AC 13 without cover
        # but miss with +2 cover.
        orc.cover = "none"
        hits_no_cover = 0
        for seed in range(100):
            r = attack_roll(fighter, orc, rng=random.Random(seed))
            if r.is_hit:
                hits_no_cover += 1
        orc.cover = "half"
        hits_half_cover = 0
        for seed in range(100):
            r = attack_roll(fighter, orc, rng=random.Random(seed))
            if r.is_hit:
                hits_half_cover += 1
        assert hits_no_cover > hits_half_cover

    def test_three_quarters_even_harder(
        self, fighter: Character, orc: Character,
    ) -> None:
        orc.cover = "none"
        hits_none = 0
        for seed in range(100):
            r = attack_roll(fighter, orc, rng=random.Random(seed))
            if r.is_hit:
                hits_none += 1
        orc.cover = "three_quarters"
        hits_3q = 0
        for seed in range(100):
            r = attack_roll(fighter, orc, rng=random.Random(seed))
            if r.is_hit:
                hits_3q += 1
        assert hits_none > hits_3q

    def test_total_cover_raises_target_ac_dramatically(
        self, fighter: Character, orc: Character,
    ) -> None:
        orc.cover = "total"
        r = attack_roll(fighter, orc, rng=random.Random(1))
        # Total cover sets AC to a near-untargetable value; only nat 20s
        # will hit (PHB: nat 20 is always a hit, even on impossible AC).
        assert r.target_ac > 50

    def test_target_ac_reflects_cover(
        self, fighter: Character, orc: Character,
    ) -> None:
        orc.cover = "half"
        r = attack_roll(fighter, orc, rng=random.Random(1))
        assert r.target_ac == orc.armor_class + 2


# ---------------------------------------------------------------------------
# Opportunity attack (handler test via CombatEngine)
# ---------------------------------------------------------------------------


class TestOpportunityAttackHandler:
    def test_handler_registered(self) -> None:
        from auto_dm.engine.combat_engine import _ACTION_HANDLERS
        from auto_dm.state.models import ActionType
        assert ActionType.OPPORTUNITY_ATTACK in _ACTION_HANDLERS
