"""Tests for engine/defenses.py — Unarmored Defense, Danger Sense,
Brutal Critical, Evasion, Aura of Protection, plus integration with
attack_roll / damage_roll / saving_throw.
"""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.combat import attack_roll, damage_roll, saving_throw
from auto_dm.engine.defenses import (
    aura_of_protection_radius,
    aura_of_protection_save_bonus,
    brutal_critical_extra_dice,
    can_use_unarmored_defense,
    danger_sense_grants_advantage,
    evasion_damage_multiplier,
    has_aura_of_protection,
    has_brutal_critical,
    has_evasion,
    unarmored_defense_ac,
    unarmored_defense_ability,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    ArmorProperties,
    Character,
    Item,
    ItemType,
    NPC,
    WeaponProperties,
)


def make_barbarian(level: int = 5) -> Character:
    return Character(
        id="b1", name="Grog", race="Half-Orc", class_="Barbarian",
        level=level, background="Outlander", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=13, constitution=16,
            intelligence=8, wisdom=12, charisma=10,
        ),
        hp_current=40, hp_max=40, armor_class=14, speed=30,
        proficiency_bonus=3, hit_dice="1d12", hit_dice_remaining=level,
        has_danger_sense=(level >= 2),
        brutal_critical_dice=brutal_critical_extra_dice(level),
    )


def make_monk(level: int = 5) -> Character:
    return Character(
        id="m1", name="Mo", race="Human", class_="Monk",
        level=level, background="Hermit", alignment="LN",
        abilities=AbilityScores(
            strength=12, dexterity=16, constitution=13,
            intelligence=10, wisdom=14, charisma=10,
        ),
        hp_current=30, hp_max=30, armor_class=15, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
        has_evasion=(level >= 7),
    )


def make_paladin(level: int = 6) -> Character:
    return Character(
        id="p1", name="Lyra", race="Human", class_="Paladin",
        level=level, background="Noble", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=10, constitution=14,
            intelligence=10, wisdom=12, charisma=14,
        ),
        hp_current=35, hp_max=35, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
        has_aura_of_protection=(level >= 6),
    )


def make_rogue(level: int = 7) -> Character:
    return Character(
        id="r1", name="Vex", race="Half-Elf", class_="Rogue",
        level=level, background="Criminal", alignment="CN",
        abilities=AbilityScores(
            strength=10, dexterity=16, constitution=12,
            intelligence=13, wisdom=12, charisma=14,
        ),
        hp_current=24, hp_max=24, armor_class=14, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
        has_evasion=(level >= 7),
    )


def make_npc() -> NPC:
    return NPC(
        id="t1", name="Orc", hp_current=20, hp_max=20,
        armor_class=12, speed=30,
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=12,
            intelligence=7, wisdom=10, charisma=8,
        ),
    )


def greataxe() -> Item:
    return Item(
        name="Greataxe", type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice="1d12", damage_type="slashing", heavy=True),
    )


def chain_mail() -> Item:
    return Item(
        name="Chain Mail", type=ItemType.ARMOR,
        armor=ArmorProperties(base_ac=16, add_dex_modifier=False),
    )


class TestUnarmoredDefense:
    def test_barbarian_ability_is_con(self):
        b = make_barbarian()
        assert unarmored_defense_ability(b) == Ability.CON

    def test_monk_ability_is_wis(self):
        m = make_monk()
        assert unarmored_defense_ability(m) == Ability.WIS

    def test_non_class_returns_none(self):
        c = Character(
            id="f1", name="X", race="Human", class_="Fighter", level=1,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=16, dexterity=14, constitution=14,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=10, hp_max=10, armor_class=16, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        )
        assert unarmored_defense_ability(c) is None

    def test_can_use_when_no_armor(self):
        b = make_barbarian()
        b.equipped.armor = None
        assert can_use_unarmored_defense(b) is True

    def test_cannot_use_with_armor(self):
        b = make_barbarian()
        b.equipped.armor = chain_mail()
        assert can_use_unarmored_defense(b) is False

    def test_ac_no_armor(self):
        # DEX 13 mod +1, CON 16 mod +3, total 14
        b = make_barbarian()
        b.equipped.armor = None
        assert unarmored_defense_ac(b) == 14

    def test_monk_ac_no_armor(self):
        # DEX 16 mod +3, WIS 14 mod +2, total 15
        m = make_monk()
        m.equipped.armor = None
        assert unarmored_defense_ac(m) == 15


class TestDangerSense:
    def test_l2_barbarian_has(self):
        b = make_barbarian(level=2)
        assert danger_sense_grants_advantage(b) is True

    def test_l1_barbarian_no(self):
        b = make_barbarian(level=1)
        assert danger_sense_grants_advantage(b) is False

    def test_non_barbarian_no(self):
        m = make_monk()
        assert danger_sense_grants_advantage(m) is False


class TestEvasion:
    def test_l7_rogue_has(self):
        assert has_evasion(make_rogue(level=7)) is True

    def test_l6_rogue_no(self):
        assert has_evasion(make_rogue(level=6)) is False

    def test_success_zero_damage(self):
        assert evasion_damage_multiplier(True) == 0.0

    def test_failure_half_damage(self):
        assert evasion_damage_multiplier(False) == 0.5


class TestBrutalCritical:
    def test_l9_one_die(self):
        assert brutal_critical_extra_dice(9) == 1

    def test_l13_two_dice(self):
        assert brutal_critical_extra_dice(13) == 2

    def test_l17_three_dice(self):
        assert brutal_critical_extra_dice(17) == 3

    def test_l8_no_dice(self):
        assert brutal_critical_extra_dice(8) == 0

    def test_has_brutal_critical(self):
        assert has_brutal_critical(make_barbarian(level=9)) is True
        assert has_brutal_critical(make_barbarian(level=5)) is False


class TestAuraOfProtection:
    def test_l6_paladin_has(self):
        assert has_aura_of_protection(make_paladin(level=6)) is True

    def test_l5_paladin_no(self):
        assert has_aura_of_protection(make_paladin(level=5)) is False

    def test_radius_l6(self):
        assert aura_of_protection_radius(make_paladin(level=6)) == 10

    def test_radius_l18(self):
        assert aura_of_protection_radius(make_paladin(level=18)) == 30

    def test_save_bonus(self):
        p = make_paladin(level=6)  # CHA 14 mod +2
        assert aura_of_protection_save_bonus(p) == 2


class TestSavingThrowIntegration:
    def test_aura_adds_to_modifier(self):
        p = make_paladin(level=6)  # CHA mod +2
        result = saving_throw(p, Ability.STR, dc=20, rng=random.Random(0))
        # The character's STR mod (+3) + aura (+2) = +5
        assert result.modifier == 5

    def test_no_aura_no_bonus(self):
        p = make_paladin(level=5)
        p.has_aura_of_protection = False
        result = saving_throw(p, Ability.STR, dc=20, rng=random.Random(0))
        assert result.modifier == 3  # STR mod only

    def test_danger_sense_applied(self):
        b = make_barbarian(level=5)
        # With danger sense, the save should not error out and use dex mod
        result = saving_throw(b, Ability.DEX, dc=20, rng=random.Random(0))
        # DEX mod +1
        assert result.modifier == 1

    def test_danger_sense_uses_str(self):
        b = make_barbarian(level=5)
        result = saving_throw(b, Ability.STR, dc=20, rng=random.Random(0))
        # STR mod +3 (and rage adv applied, but we just check it runs)
        assert result.modifier == 3


class TestBrutalCriticalIntegration:
    def test_crit_extra_damage_dice(self):
        b = make_barbarian(level=9)
        b.equipped.main_hand = greataxe()
        target = make_npc()
        # Force a crit via is_crit=True
        roll = damage_roll(b, is_crit=True, rng=random.Random(0))
        # Greataxe 1d12 -> on crit becomes 2d12 + brutal 1d12 = 3d12
        # So total = 3 to 36
        assert 3 <= roll.total <= 36
        # Verify dice count
        assert len(roll.individual_rolls) == 3
