"""Tests for engine/fighting_style.py."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.combat import attack_roll, damage_roll
from auto_dm.engine.fighting_style import (
    FIGHTING_STYLES,
    ac_bonus,
    apply_gwf,
    attack_bonus,
    can_use_protection,
    damage_bonus,
    has_shield,
    is_ranged_weapon,
    is_two_handed_melee,
    off_hand_damage_modifier,
    reroll_damage_die,
)
from auto_dm.state.models import (
    AbilityScores,
    ArmorProperties,
    Character,
    EquippedSlots,
    Item,
    ItemType,
    NPC,
    WeaponProperties,
)


def make_fighter(style: str | None = None) -> Character:
    return Character(
        id="f1", name="Conan", race="Human", class_="Fighter", level=1,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=12, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        fighting_style=style,
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


def longsword() -> Item:
    return Item(
        name="Longsword",
        type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice="1d8", damage_type="slashing"),
    )


def shortbow() -> Item:
    return Item(
        name="Shortbow",
        type=ItemType.WEAPON,
        weapon=WeaponProperties(
            damage_dice="1d6", damage_type="piercing",
            range_normal=80, range_long=320, ammunition=True,
        ),
    )


def great_axe() -> Item:
    return Item(
        name="Greataxe",
        type=ItemType.WEAPON,
        weapon=WeaponProperties(
            damage_dice="1d12", damage_type="slashing", heavy=True,
        ),
    )


def shield() -> Item:
    return Item(
        name="Shield",
        type=ItemType.SHIELD,
        armor=ArmorProperties(base_ac=2, add_dex_modifier=False, is_shield=True),
    )


class TestFightingStyleConstants:
    def test_all_styles(self):
        assert "archery" in FIGHTING_STYLES
        assert "defense" in FIGHTING_STYLES
        assert "dueling" in FIGHTING_STYLES
        assert "great_weapon_fighting" in FIGHTING_STYLES
        assert "protection" in FIGHTING_STYLES
        assert "two_weapon_fighting" in FIGHTING_STYLES


class TestAttackBonus:
    def test_archery_with_ranged(self):
        f = make_fighter("archery")
        f.equipped.main_hand = shortbow()
        assert attack_bonus(f, f.equipped.main_hand) == 2

    def test_archery_with_melee(self):
        f = make_fighter("archery")
        f.equipped.main_hand = longsword()
        assert attack_bonus(f, f.equipped.main_hand) == 0

    def test_no_style(self):
        f = make_fighter(None)
        f.equipped.main_hand = shortbow()
        assert attack_bonus(f, f.equipped.main_hand) == 0

    def test_other_style_no_bonus(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = shortbow()
        assert attack_bonus(f, f.equipped.main_hand) == 0


class TestDamageBonus:
    def test_dueling_with_one_handed_melee(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = longsword()
        f.equipped.off_hand = None
        assert damage_bonus(f, f.equipped.main_hand) == 2

    def test_dueling_with_two_handed_blocks(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = great_axe()
        f.equipped.off_hand = None
        assert damage_bonus(f, f.equipped.main_hand) == 0

    def test_dueling_with_dual_wield_blocks(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = longsword()
        f.equipped.off_hand = Item(
            name="Dagger", type=ItemType.WEAPON,
            weapon=WeaponProperties(damage_dice="1d4", damage_type="piercing",
                                     light=True),
        )
        assert damage_bonus(f, f.equipped.main_hand) == 0

    def test_dueling_with_shield_ok(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = longsword()
        f.equipped.off_hand = shield()
        assert damage_bonus(f, f.equipped.main_hand) == 2

    def test_no_style(self):
        f = make_fighter(None)
        f.equipped.main_hand = longsword()
        assert damage_bonus(f, f.equipped.main_hand) == 0


class TestACBonus:
    def test_defense_with_armor(self):
        f = make_fighter("defense")
        f.equipped.armor = Item(
            name="Chain Mail", type=ItemType.ARMOR,
            armor=ArmorProperties(base_ac=16, add_dex_modifier=False),
        )
        assert ac_bonus(f) == 1

    def test_defense_no_armor(self):
        f = make_fighter("defense")
        f.equipped.armor = None
        assert ac_bonus(f) == 0

    def test_no_style(self):
        f = make_fighter(None)
        f.equipped.armor = Item(
            name="Chain Mail", type=ItemType.ARMOR,
            armor=ArmorProperties(base_ac=16, add_dex_modifier=False),
        )
        assert ac_bonus(f) == 0


class TestGWF:
    def test_reroll_1(self):
        assert reroll_damage_die(1, rng=random.Random(0)) >= 3

    def test_reroll_2(self):
        assert reroll_damage_die(2, rng=random.Random(0)) >= 3

    def test_no_reroll_for_3(self):
        assert reroll_damage_die(3, rng=random.Random(0)) == 3

    def test_apply_gwf(self):
        rolls = [1, 2, 3, 4, 5]
        result = apply_gwf(rolls, rng=random.Random(0))
        # 1 and 2 get rerolled; 3, 4, 5 stay
        assert result[2] == 3
        assert result[3] == 4
        assert result[4] == 5
        # First two are in [3..8]
        assert 3 <= result[0] <= 8
        assert 3 <= result[1] <= 8


class TestTwoWeaponFighting:
    def test_adds_ability_mod(self):
        f = make_fighter("two_weapon_fighting")
        f.abilities.strength = 16  # +3
        assert off_hand_damage_modifier(f) == 3

    def test_no_style_returns_zero(self):
        f = make_fighter(None)
        f.abilities.strength = 16
        assert off_hand_damage_modifier(f) == 0


class TestProtection:
    def test_no_style(self):
        f = make_fighter("archery")
        f.equipped.off_hand = shield()
        assert can_use_protection(f) is False

    def test_with_style_and_shield(self):
        f = make_fighter("protection")
        f.equipped.off_hand = shield()
        assert can_use_protection(f) is True

    def test_with_style_no_shield(self):
        f = make_fighter("protection")
        f.equipped.off_hand = None
        assert can_use_protection(f) is False


class TestWeaponHelpers:
    def test_is_ranged(self):
        assert is_ranged_weapon(shortbow()) is True
        assert is_ranged_weapon(longsword()) is False

    def test_is_two_handed_melee(self):
        assert is_two_handed_melee(great_axe()) is True
        assert is_two_handed_melee(longsword()) is False

    def test_has_shield(self):
        f = make_fighter()
        f.equipped.off_hand = shield()
        assert has_shield(f) is True
        f.equipped.off_hand = None
        assert has_shield(f) is False


class TestFightingStyleIntegration:
    def test_archery_attack_bonus_applied(self):
        f = make_fighter("archery")
        f.equipped.main_hand = shortbow()
        target = make_target()
        # No fighting style
        f.fighting_style = None
        no_style = attack_roll(f, target, rng=random.Random(0))
        # With archery
        f.fighting_style = "archery"
        with_style = attack_roll(f, target, rng=random.Random(0))
        # The attack modifier should differ by 2
        assert with_style.attack_modifier - no_style.attack_modifier == 2

    def test_dueling_damage_applied(self):
        f = make_fighter("dueling")
        f.equipped.main_hand = longsword()
        f.equipped.off_hand = None
        no_style_char = make_fighter(None)
        no_style_char.equipped.main_hand = longsword()
        no_style_dmg = damage_roll(no_style_char, rng=random.Random(0))
        f_dmg = damage_roll(f, rng=random.Random(0))
        # Dueling adds 2 to STR 16 (mod 3 → 5)
        assert f_dmg.modifier - no_style_dmg.modifier == 2