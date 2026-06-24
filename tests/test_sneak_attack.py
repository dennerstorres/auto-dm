"""Tests for engine/sneak_attack.py — Rogue's Sneak Attack."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.sneak_attack import (
    can_sneak_attack,
    reset_turn_flags,
    roll_sneak_attack,
    sneak_attack_dice,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    EquippedSlots,
    Item,
    ItemType,
    NPC,
    WeaponProperties,
)


def make_rogue(level: int = 3) -> Character:
    return Character(
        id="r1", name="Vex", race="Half-Elf", class_="Rogue", level=level,
        background="Criminal", alignment="CN",
        abilities=AbilityScores(
            strength=10, dexterity=16, constitution=12,
            intelligence=13, wisdom=12, charisma=14,
        ),
        hp_current=20, hp_max=20, armor_class=14, speed=30,
        proficiency_bonus=2 + (level - 1) // 4, hit_dice="1d8", hit_dice_remaining=level,
        equipped=EquippedSlots(
            main_hand=Item(
                name="Rapier",
                type=ItemType.WEAPON,
                weapon=WeaponProperties(
                    damage_dice="1d8", damage_type="piercing", finesse=True,
                ),
            ),
        ),
    )


def make_fighter() -> Character:
    return Character(
        id="f1", name="Conan", race="Human", class_="Fighter", level=1,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=12, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
    )


def make_target() -> NPC:
    return NPC(
        id="t1", name="Orc", hp_current=15, hp_max=15,
        armor_class=13, speed=30,
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
    )


class TestSneakAttackDice:
    @pytest.mark.parametrize("level,expected", [
        (1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (6, 3),
        (7, 4), (9, 5), (10, 5), (20, 5),
    ])
    def test_dice_by_level(self, level, expected):
        assert sneak_attack_dice(level) == expected


class TestCanSneakAttack:
    def test_rogue_with_advantage(self, fighter=None):
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=True, has_disadvantage=False,
            ally_adjacent=False, weapon_is_finesse_or_ranged=True,
        ) is True

    def test_rogue_with_ally_adjacent(self):
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=False, has_disadvantage=False,
            ally_adjacent=True, weapon_is_finesse_or_ranged=True,
        ) is True

    def test_rogue_with_only_advantage_no_ally(self):
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=True, has_disadvantage=False,
            ally_adjacent=False, weapon_is_finesse_or_ranged=True,
        ) is True

    def test_no_trigger_without_advantage_or_ally(self):
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=False, has_disadvantage=False,
            ally_adjacent=False, weapon_is_finesse_or_ranged=True,
        ) is False

    def test_disadvantage_blocks(self):
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=False, has_disadvantage=True,
            ally_adjacent=True, weapon_is_finesse_or_ranged=True,
        ) is False

    def test_advantage_with_disadvantage_cancels(self):
        # PHB: any adv + any dis = straight roll, so sneak attack needs
        # ally_adjacent to trigger.
        rogue = make_rogue()
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=True, has_disadvantage=True,
            ally_adjacent=True, weapon_is_finesse_or_ranged=True,
        ) is True  # ally_adjacent saves it

    def test_non_finesse_weapon_blocks(self):
        rogue = make_rogue()
        rogue.equipped.main_hand = Item(
            name="Longsword",
            type=ItemType.WEAPON,
            weapon=WeaponProperties(
                damage_dice="1d8", damage_type="slashing", finesse=False,
            ),
        )
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=True, has_disadvantage=False,
            ally_adjacent=False, weapon_is_finesse_or_ranged=False,
        ) is False

    def test_already_used_this_turn_blocks(self):
        rogue = make_rogue()
        rogue.sneak_attack_used_this_turn = True
        target = make_target()
        assert can_sneak_attack(
            rogue, target, has_advantage=True, has_disadvantage=False,
            ally_adjacent=False, weapon_is_finesse_or_ranged=True,
        ) is False

    def test_non_rogue_cannot(self):
        # Use a fighter as attacker
        target = make_target()
        # Finesse check doesn't matter — class check is first.
        assert can_sneak_attack(
            make_fighter(), target, has_advantage=True, has_disadvantage=False,
            ally_adjacent=True, weapon_is_finesse_or_ranged=True,
        ) is False


class TestRollSneakAttack:
    def test_rolls_correct_number_of_dice(self):
        rogue = make_rogue(level=3)  # 2d6
        # Seed so total is deterministic.
        total = roll_sneak_attack(rogue, rng=random.Random(42))
        assert 2 <= total <= 12

    def test_marks_used(self):
        rogue = make_rogue()
        assert rogue.sneak_attack_used_this_turn is False
        roll_sneak_attack(rogue, rng=random.Random(0))
        assert rogue.sneak_attack_used_this_turn is True

    def test_reset_turn_flags(self):
        rogue = make_rogue()
        rogue.sneak_attack_used_this_turn = True
        reset_turn_flags(rogue)
        assert rogue.sneak_attack_used_this_turn is False

    def test_high_level_caps_at_5d6(self):
        rogue = make_rogue(level=15)
        total = roll_sneak_attack(rogue, rng=random.Random(0))
        # 5d6 = 5-30
        assert 5 <= total <= 30