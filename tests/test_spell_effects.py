"""Tests for critical spell mechanics: Magic Missile, Healing Word,
Fireball, Shield. Validates damage formulas, save behavior, slot usage.
"""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.combat import attack_roll
from auto_dm.engine.spell_effects import (
    apply_shield,
    cast_fireball,
    cast_healing_word,
    cast_magic_missile,
    magic_missile_dart_count,
    roll_healing_word,
    roll_magic_missile,
    roll_fireball,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    NPC,
    Spellcasting,
)


def make_wizard(level: int = 5) -> Character:
    w = Character(
        id="w1", name="Elara", race="High Elf", class_="Wizard", level=level,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=16, wisdom=12, charisma=10,
        ),
        hp_current=24, hp_max=24, armor_class=12, speed=30,
        proficiency_bonus=3, hit_dice="1d6", hit_dice_remaining=level,
    )
    w.spellcasting = Spellcasting(
        ability="intelligence", save_dc=15, attack_bonus=7,
        cantrips_known=["Fire Bolt"],
        spells_known=["Magic Missile", "Shield", "Fireball", "Healing Word"],
        spells_prepared=["Magic Missile", "Shield", "Fireball", "Healing Word"],
        spell_slots={1: 4, 2: 3, 3: 2},
        spell_slots_max={1: 4, 2: 3, 3: 2},
    )
    return w


def make_cleric(level: int = 5) -> Character:
    c = Character(
        id="c1", name="Mira", race="Hill Dwarf", class_="Cleric", level=level,
        background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=14,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=32, hp_max=32, armor_class=18, speed=25,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.spellcasting = Spellcasting(
        ability="wisdom", save_dc=15, attack_bonus=7,
        cantrips_known=["Sacred Flame"],
        spells_known=["Healing Word", "Cure Wounds"],
        spells_prepared=["Healing Word", "Cure Wounds"],
        spell_slots={1: 4, 2: 3, 3: 2},
        spell_slots_max={1: 4, 2: 3, 3: 2},
    )
    return c


def make_target() -> NPC:
    return NPC(
        id="t1", name="Orc", hp_current=30, hp_max=30,
        armor_class=12, speed=30,
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=12,
            intelligence=7, wisdom=10, charisma=8,
        ),
    )


class TestMagicMissile:
    def test_dart_count_l1(self):
        assert magic_missile_dart_count(1) == 3

    def test_dart_count_l2(self):
        assert magic_missile_dart_count(2) == 4

    def test_dart_count_l3(self):
        assert magic_missile_dart_count(3) == 5

    def test_dart_count_l7(self):
        # PHB: max 11 darts at L9+
        assert magic_missile_dart_count(7) == 9

    def test_roll_min_max(self):
        # Each dart 1d4+1 = 2-5
        rolls = roll_magic_missile(1, rng=random.Random(0))
        assert len(rolls) == 3
        for r in rolls:
            assert 2 <= r <= 5

    def test_cast_auto_hit(self):
        w = make_wizard()
        target = make_target()
        result = cast_magic_missile(w, target, slot_level=1, rng=random.Random(0))
        assert result.success is True
        # 3 darts, each 2-5, total 6-15
        assert 6 <= result.damage <= 15
        assert target.hp_current == 30 - result.damage
        # Slot consumed
        assert w.spellcasting.spell_slots[1] == 3

    def test_upcast_consumes_higher_slot(self):
        w = make_wizard()
        target = make_target()
        result = cast_magic_missile(w, target, slot_level=2, rng=random.Random(0))
        assert result.success is True
        # Upcast: 4 darts, 2-5 each = 8-20
        assert 8 <= result.damage <= 20
        # L2 slot consumed (since L1 spell with L2 slot)
        assert w.spellcasting.spell_slots[2] == 2

    def test_no_slot_fails(self):
        w = make_wizard()
        w.spellcasting.spell_slots = {1: 0, 2: 0, 3: 0}
        target = make_target()
        result = cast_magic_missile(w, target, slot_level=1, rng=random.Random(0))
        assert result.success is False
        assert "no slot" in result.error.lower()


class TestHealingWord:
    def test_roll_min_max(self):
        w = make_wizard()  # INT 16 mod +3
        # 1d4 + 3 = 4-7
        h = roll_healing_word(w, slot_level=1, rng=random.Random(0))
        assert 4 <= h <= 7

    def test_cast_heals(self):
        c = make_cleric()  # WIS 16 mod +3
        c.hp_current = 5
        result = cast_healing_word(c, c, slot_level=1, rng=random.Random(0))
        assert result.success is True
        assert result.healing >= 4
        assert c.hp_current == 5 + result.healing
        # Slot consumed
        assert c.spellcasting.spell_slots[1] == 3

    def test_caps_at_max_hp(self):
        c = make_cleric()
        c.hp_current = c.hp_max - 1
        result = cast_healing_word(c, c, slot_level=1, rng=random.Random(0))
        assert c.hp_current == c.hp_max
        # result.healing is actual amount healed (may be > 1)
        assert result.healing >= 1

    def test_upcast_more_dice(self):
        c = make_cleric()
        c.hp_current = 1
        result = cast_healing_word(c, c, slot_level=2, rng=random.Random(0))
        assert result.success is True
        # 2d4 + 3 = 5-11
        assert 5 <= result.healing <= 11


class TestFireball:
    def test_roll_l3(self):
        # 8d6 = 8-48
        dmg = roll_fireball(3, is_save_success=False, rng=random.Random(0))
        assert 8 <= dmg <= 48

    def test_roll_l4(self):
        # 9d6 = 9-54
        dmg = roll_fireball(4, is_save_success=False, rng=random.Random(0))
        assert 9 <= dmg <= 54

    def test_save_half(self):
        # Force a high damage roll
        full = roll_fireball(3, is_save_success=False, rng=random.Random(42))
        half = roll_fireball(3, is_save_success=True, rng=random.Random(42))
        assert half == full // 2

    def test_cast_applies_damage(self):
        w = make_wizard()
        target = make_target()
        result = cast_fireball(w, target, slot_level=3, rng=random.Random(0))
        assert result.success is True
        # 8-48 fire, possibly halved
        assert 0 <= result.damage <= 48
        # Target HP reduced
        assert target.hp_current == 30 - result.damage
        # L3 slot consumed
        assert w.spellcasting.spell_slots[3] == 1

    def test_cast_upcast(self):
        w = make_wizard()
        w.spellcasting.spell_slots = {1: 4, 2: 3, 3: 2, 4: 1}
        w.spellcasting.spell_slots_max = {1: 4, 2: 3, 3: 2, 4: 1}
        target = make_target()
        result = cast_fireball(w, target, slot_level=4, rng=random.Random(0))
        assert result.success is True
        # L4 slot consumed
        assert w.spellcasting.spell_slots[4] == 0


class TestShield:
    def test_applies_ac_bonus(self):
        w = make_wizard()
        # w.ac starts at 12
        apply_shield(w)
        # pending_ac_bonus is +5
        assert w.pending_ac_bonus == 5

    def test_shield_increases_effective_ac(self):
        w = make_wizard()
        w.armor_class = 12
        attacker = Character(
            id="a1", name="X", race="Human", class_="Fighter", level=1,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=16, dexterity=12, constitution=14,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=10, hp_max=10, armor_class=14, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        )
        # Without shield
        no_shield = attack_roll(
            attacker, w, rng=random.Random(0),
        )
        # With shield
        apply_shield(w)
        with_shield = attack_roll(
            attacker, w, rng=random.Random(0),
        )
        # The target_ac must differ by 5
        assert with_shield.target_ac - no_shield.target_ac == 5


class TestSpellSlotConsumption:
    def test_consume_l1(self):
        w = make_wizard()
        target = make_target()
        before = w.spellcasting.spell_slots[1]
        cast_magic_missile(w, target, slot_level=1, rng=random.Random(0))
        assert w.spellcasting.spell_slots[1] == before - 1

    def test_consume_upcasts_when_lower_unavailable(self):
        w = make_wizard()
        w.spellcasting.spell_slots = {1: 0, 2: 2, 3: 1}
        target = make_target()
        # Cast MM at L1 but no L1 slot — should use L2
        result = cast_magic_missile(w, target, slot_level=1, rng=random.Random(0))
        assert result.success is True
        assert w.spellcasting.spell_slots[2] == 1
