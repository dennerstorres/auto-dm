"""Tests for engine/smite.py — Paladin's Divine Smite."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.smite import (
    SMITE_DAMAGE_TYPE,
    SmiteResult,
    divine_smite,
    is_undead_or_fiend,
    smite_dice_for_slot,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    EquippedSlots,
    NPC,
    Spellcasting,
)


def make_paladin(level: int = 3, slots: dict | None = None) -> Character:
    if slots is None:
        slots = {1: 4, 2: 2}
    return Character(
        id="p1", name="Lathander", race="Human", class_="Paladin", level=level,
        background="Noble", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=10, constitution=14,
            intelligence=10, wisdom=12, charisma=16,
        ),
        hp_current=24, hp_max=24, armor_class=18, speed=30,
        proficiency_bonus=2 + (level - 1) // 4, hit_dice="1d10", hit_dice_remaining=level,
        spellcasting=Spellcasting(
            ability=Ability.CHA,
            save_dc=13, attack_bonus=5,
            spells_known=[],
            spells_prepared=[],
            spell_slots=dict(slots),
            spell_slots_max=dict(slots),
            concentration=None,
            ritual_casting=False,
        ),
    )


def make_target(name: str = "Orc") -> NPC:
    return NPC(
        id="t1", name=name, hp_current=20, hp_max=20,
        armor_class=13, speed=30,
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
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


class TestSmiteDice:
    @pytest.mark.parametrize("slot,dice", [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 5), (9, 5),
    ])
    def test_dice_by_slot(self, slot, dice):
        assert smite_dice_for_slot(slot) == dice

    def test_damage_type(self):
        assert SMITE_DAMAGE_TYPE == "radiant"


class TestIsUndeadOrFiend:
    @pytest.mark.parametrize("name", [
        "Zombie", "Skeleton", "Vampire", "Lich", "Wraith",
        "Demon", "Devil", "Imp", "Abyssal Horror",
    ])
    def test_recognized(self, name):
        target = make_target(name)
        assert is_undead_or_fiend(target) is True

    @pytest.mark.parametrize("name", ["Orc", "Goblin", "Wolf", "Bandit"])
    def test_not_recognized(self, name):
        target = make_target(name)
        assert is_undead_or_fiend(target) is False


class TestDivineSmite:
    def test_basic_smite_at_slot_1(self):
        p = make_paladin()
        result = divine_smite(p, make_target("Orc"), 1, rng=random.Random(0))
        assert isinstance(result, SmiteResult)
        assert result.success is True
        assert result.slot_level_used == 1
        assert result.smite_dice == 2
        assert result.target_creature_type == "other"
        assert result.bonus_dice_undead_fiend == 0
        assert 2 <= result.damage <= 16  # 2d8

    def test_smite_consumes_slot(self):
        p = make_paladin()
        before = dict(p.spellcasting.spell_slots)
        divine_smite(p, make_target(), 1, rng=random.Random(0))
        assert p.spellcasting.spell_slots[1] == before[1] - 1

    def test_smite_at_slot_2_3d8(self):
        p = make_paladin()
        result = divine_smite(p, make_target(), 2, rng=random.Random(0))
        assert result.success is True
        assert result.slot_level_used == 2
        assert result.smite_dice == 3
        assert 3 <= result.damage <= 24

    def test_smite_against_undead_adds_die(self):
        p = make_paladin()
        result = divine_smite(p, make_target("Zombie"), 1, rng=random.Random(0))
        assert result.success is True
        assert result.bonus_dice_undead_fiend == 1
        assert result.target_creature_type == "undead"
        # 2 base + 1 bonus = 3d8
        assert 3 <= result.damage <= 24

    def test_smite_against_fiend_adds_die(self):
        p = make_paladin()
        result = divine_smite(p, make_target("Demon"), 1, rng=random.Random(0))
        assert result.bonus_dice_undead_fiend == 1
        assert result.target_creature_type == "fiend"

    def test_no_slot_fails(self):
        p = make_paladin(slots={})
        result = divine_smite(p, make_target(), 1, rng=random.Random(0))
        assert result.success is False
        assert "No spell slot" in result.reason

    def test_non_paladin_fails(self):
        f = make_fighter()
        result = divine_smite(f, make_target(), 1, rng=random.Random(0))
        assert result.success is False
        assert "paladin" in result.reason.lower()

    def test_paladin_no_spellcasting_fails(self):
        p = make_paladin()
        p.spellcasting = None
        result = divine_smite(p, make_target(), 1, rng=random.Random(0))
        assert result.success is False

    def test_zero_slot_fails(self):
        p = make_paladin()
        result = divine_smite(p, make_target(), 0, rng=random.Random(0))
        assert result.success is False

    def test_upcast_smite(self):
        # Use slot 3 -> 4d8 base.
        p = make_paladin(slots={1: 4, 2: 2, 3: 2})
        result = divine_smite(p, make_target(), 3, rng=random.Random(0))
        assert result.success is True
        assert result.smite_dice == 4
        assert 4 <= result.damage <= 32

    def test_smite_caps_at_5d8(self):
        # Slot 5 still caps at 5d8.
        p = make_paladin(slots={1: 4, 2: 2, 3: 2, 4: 1, 5: 1})
        result = divine_smite(p, make_target(), 5, rng=random.Random(0))
        assert result.success is True
        assert result.smite_dice == 5  # capped
        # 5d8 vs undead 6d8 — but undead bonus still +1.
        assert 5 <= result.damage <= 48