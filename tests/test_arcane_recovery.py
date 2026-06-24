"""Tests for engine/arcane_recovery.py — Wizard's Arcane Recovery (PHB p. 115)."""
from __future__ import annotations

import pytest

from auto_dm.engine.arcane_recovery import (
    arcane_recovery,
    arcane_recovery_max_slot_value,
    can_arcane_recover,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Spellcasting,
)


def make_wizard(level: int = 5) -> Character:
    c = Character(
        id="w1", name="Elara", race="High Elf", class_="Wizard", level=level,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=16, wisdom=12, charisma=10,
        ),
        hp_current=24, hp_max=24, armor_class=12, speed=30,
        proficiency_bonus=3, hit_dice="1d6", hit_dice_remaining=level,
    )
    c.spellcasting = Spellcasting(
        ability="intelligence", save_dc=13, attack_bonus=5,
        spell_slots={1: 0, 2: 0, 3: 2},
        spells_known=["Magic Missile", "Shield"],
    )
    return c


class TestCap:
    def test_level_1_cap(self):
        assert arcane_recovery_max_slot_value(1) == 1

    def test_level_2_cap(self):
        assert arcane_recovery_max_slot_value(2) == 1

    def test_level_3_cap(self):
        assert arcane_recovery_max_slot_value(3) == 2

    def test_level_5_cap(self):
        assert arcane_recovery_max_slot_value(5) == 3

    def test_level_10_cap(self):
        assert arcane_recovery_max_slot_value(10) == 5

    def test_level_20_cap(self):
        assert arcane_recovery_max_slot_value(20) == 10


class TestCanRecover:
    def test_wizard_can(self):
        assert can_arcane_recover(make_wizard()) is True

    def test_non_wizard_cannot(self):
        c = Character(
            id="f1", name="X", race="Human", class_="Fighter", level=5,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=16, dexterity=14, constitution=14,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=20, hp_max=20, armor_class=16, speed=30,
            proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=5,
        )
        assert can_arcane_recover(c) is False


class TestRecover:
    def test_recover_single_slot(self):
        w = make_wizard(5)
        ok, msg = arcane_recovery(w, [3])
        assert ok is True
        assert msg == ""
        assert w.spellcasting.spell_slots[3] == 3

    def test_recover_multiple_slots_within_cap(self):
        w = make_wizard(5)  # cap = 3
        ok, msg = arcane_recovery(w, [2, 1])  # total = 3 ≤ 3
        assert ok is True
        assert w.spellcasting.spell_slots[2] == 1
        assert w.spellcasting.spell_slots[1] == 1

    def test_recover_exceeds_cap(self):
        w = make_wizard(3)  # cap = 2
        ok, msg = arcane_recovery(w, [2, 1])  # total = 3 > 2
        assert ok is False
        assert "excede" in msg.lower() or "exceeds" in msg.lower()

    def test_recover_slot_above_5_rejected(self):
        w = make_wizard(20)
        ok, msg = arcane_recovery(w, [6])
        assert ok is False
        assert "above" in msg.lower() or "5th" in msg.lower()

    def test_recover_no_spellcasting(self):
        w = make_wizard()
        w.spellcasting = None
        ok, msg = arcane_recovery(w, [1])
        assert ok is False

    def test_recover_non_wizard_rejected(self):
        c = Character(
            id="f1", name="X", race="Human", class_="Fighter", level=5,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=16, dexterity=14, constitution=14,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=20, hp_max=20, armor_class=16, speed=30,
            proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=5,
        )
        ok, msg = arcane_recovery(c, [1])
        assert ok is False
        assert "wizard" in msg.lower()

    def test_recover_empty_list(self):
        w = make_wizard(5)
        ok, msg = arcane_recovery(w, [])
        assert ok is True
