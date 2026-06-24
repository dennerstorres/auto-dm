"""Tests for engine/specialists.py — Wild Shape (Druid), Favored Enemy
(Ranger), Eldritch Invocations (Warlock), Divine Sense, Destroy Undead.
"""
from __future__ import annotations

import pytest

from auto_dm.engine.specialists import (
    add_favored_enemy,
    add_invocation,
    available_wild_shapes,
    can_wild_shape,
    destroy_undead_cr_cap,
    divine_sense_range,
    enter_wild_shape,
    has_invocation,
    is_favored_enemy,
    lay_on_hands_disease_cure_spend,
    revert_wild_shape,
    wild_shape_cr_cap,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
)


def make_druid(level: int = 4) -> Character:
    return Character(
        id="d1", name="Ash", race="Human", class_="Druid",
        level=level, background="Hermit", alignment="LN",
        abilities=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=12, wisdom=16, charisma=10,
        ),
        hp_current=24, hp_max=24, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=level,
    )


def make_ranger(level: int = 5) -> Character:
    return Character(
        id="r1", name="Artemis", race="Wood Elf", class_="Ranger",
        level=level, background="Outlander", alignment="CG",
        abilities=AbilityScores(
            strength=12, dexterity=16, constitution=14,
            intelligence=10, wisdom=14, charisma=10,
        ),
        hp_current=30, hp_max=30, armor_class=15, speed=35,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )


def make_warlock(level: int = 5) -> Character:
    return Character(
        id="w1", name="Morrigan", race="Tiefling", class_="Warlock",
        level=level, background="Hermit", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=12, wisdom=10, charisma=16,
        ),
        hp_current=24, hp_max=24, armor_class=12, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )


def make_paladin(level: int = 5) -> Character:
    return Character(
        id="p1", name="Lyra", race="Human", class_="Paladin",
        level=level, background="Noble", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=10, constitution=14,
            intelligence=10, wisdom=12, charisma=14,
        ),
        hp_current=35, hp_max=35, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )


def make_cleric(level: int = 5) -> Character:
    return Character(
        id="c1", name="Mira", race="Hill Dwarf", class_="Cleric",
        level=level, background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=14,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=32, hp_max=32, armor_class=18, speed=25,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )


class TestWildShape:
    def test_l1_druid_cannot(self):
        d = make_druid(level=1)
        assert can_wild_shape(d) is False

    def test_l2_druid_can(self):
        d = make_druid(level=2)
        assert can_wild_shape(d) is True

    def test_non_druid_cannot(self):
        r = make_ranger()
        assert can_wild_shape(r) is False

    def test_cr_cap(self):
        assert wild_shape_cr_cap(2) == 0.25
        assert wild_shape_cr_cap(3) == 0.25
        assert wild_shape_cr_cap(4) == 0.5
        assert wild_shape_cr_cap(7) == 0.5
        assert wild_shape_cr_cap(8) == 1.0

    def test_l2_forms(self):
        forms = available_wild_shapes(2)
        names = [f.name for f in forms]
        assert "Wolf" in names
        assert "Boar" in names

    def test_l4_includes_bear(self):
        forms = available_wild_shapes(4)
        names = [f.name for f in forms]
        assert "Black Bear" in names

    def test_enter_wild_shape(self):
        d = make_druid(level=4)
        ok, msg = enter_wild_shape(d, "Wolf")
        assert ok is True
        assert d.wild_shape_form == "Wolf"
        assert d.hp_max >= 11
        assert d.speed >= 40

    def test_enter_unknown_form_rejected(self):
        d = make_druid(level=2)
        ok, msg = enter_wild_shape(d, "Dragon")
        assert ok is False

    def test_non_druid_enter_rejected(self):
        r = make_ranger()
        ok, msg = enter_wild_shape(r, "Wolf")
        assert ok is False

    def test_revert(self):
        d = make_druid(level=4)
        enter_wild_shape(d, "Wolf")
        assert d.wild_shape_form == "Wolf"
        revert_wild_shape(d)
        assert d.wild_shape_form is None


class TestFavoredEnemy:
    def test_add(self):
        r = make_ranger()
        add_favored_enemy(r, "dragons")
        assert "dragons" in r.favored_enemies

    def test_case_insensitive(self):
        r = make_ranger()
        add_favored_enemy(r, "Dragons")
        assert is_favored_enemy(r, "dragons") is True
        assert is_favored_enemy(r, "DRAGONS") is True

    def test_not_favored(self):
        r = make_ranger()
        assert is_favored_enemy(r, "zombies") is False


class TestEldritchInvocations:
    def test_add(self):
        w = make_warlock()
        add_invocation(w, "Agonizing Blast")
        assert "Agonizing Blast" in w.eldritch_invocations

    def test_add_unique(self):
        w = make_warlock()
        add_invocation(w, "Agonizing Blast")
        add_invocation(w, "Agonizing Blast")
        assert len(w.eldritch_invocations) == 1

    def test_has_invocation(self):
        w = make_warlock()
        add_invocation(w, "Repelling Blast")
        assert has_invocation(w, "Repelling Blast") is True
        assert has_invocation(w, "Devil's Sight") is False


class TestDivineSense:
    def test_paladin_range(self):
        p = make_paladin(level=5)
        assert divine_sense_range(p) == 60 + 10 * 5

    def test_non_paladin_zero(self):
        r = make_ranger()
        assert divine_sense_range(r) == 0


class TestDestroyUndead:
    def test_l5_half_cr(self):
        assert destroy_undead_cr_cap(5) == 0.5

    def test_l8_one_cr(self):
        assert destroy_undead_cr_cap(8) == 1.0

    def test_l11_two_cr(self):
        assert destroy_undead_cr_cap(11) == 2.0

    def test_l14_three_cr(self):
        assert destroy_undead_cr_cap(14) == 3.0

    def test_l17_four_cr(self):
        assert destroy_undead_cr_cap(17) == 4.0

    def test_below_5_zero(self):
        assert destroy_undead_cr_cap(4) == 0.0


class TestLayOnHandsCure:
    def test_cure_disease_costs_5(self):
        assert lay_on_hands_disease_cure_spend(0) == 5
