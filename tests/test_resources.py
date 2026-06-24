"""Tests for engine/resources.py — class resource pools."""
from __future__ import annotations

import pytest

from auto_dm.engine.resources import (
    action_surge,
    action_surge_max_for,
    bardic_inspiration_die_for,
    bardic_inspiration_max_for,
    can_use_second_wind,
    convert_slot_to_points,
    create_spell_slot_from_points,
    heal_lay_on_hands,
    ki_max_for,
    lay_on_hands_pool_for,
    long_rest_recovery,
    recover_bardic_on_short_rest,
    recover_ki_on_short_rest,
    recover_sorcery_on_long_rest,
    roll_second_wind_heal,
    second_wind_max_for,
    short_rest_recovery,
    sorcery_points_max_for,
    spend_bardic_inspiration,
    spend_ki,
    spend_lay_on_hands,
    spend_sorcery_points,
    use_channel_divinity,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Spellcasting,
)


def make_monk(level: int = 5) -> Character:
    c = Character(
        id="m1", name="Mo", race="Human", class_="Monk", level=level,
        background="Hermit", alignment="LN",
        abilities=AbilityScores(
            strength=12, dexterity=16, constitution=13,
            intelligence=10, wisdom=14, charisma=10,
        ),
        hp_current=30, hp_max=30, armor_class=15, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.ki_max = ki_max_for(level)
    c.ki_points = c.ki_max
    return c


def make_sorcerer(level: int = 5) -> Character:
    c = Character(
        id="s1", name="Aria", race="Half-Elf", class_="Sorcerer", level=level,
        background="Hermit", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=12, wisdom=10, charisma=16,
        ),
        hp_current=24, hp_max=24, armor_class=12, speed=30,
        proficiency_bonus=3, hit_dice="1d6", hit_dice_remaining=level,
    )
    c.sorcery_points_max = sorcery_points_max_for(level)
    c.sorcery_points = c.sorcery_points_max
    c.spellcasting = Spellcasting(
        ability="charisma", save_dc=13, attack_bonus=5,
        spell_slots={1: 4, 2: 2}, known_spells=[],
    )
    return c


def make_paladin(level: int = 5) -> Character:
    c = Character(
        id="p1", name="Lyra", race="Human", class_="Paladin", level=level,
        background="Noble", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=10, constitution=14,
            intelligence=10, wisdom=12, charisma=14,
        ),
        hp_current=35, hp_max=35, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )
    c.lay_on_hands_pool = lay_on_hands_pool_for(level)
    return c


def make_fighter(level: int = 5) -> Character:
    c = Character(
        id="f1", name="Conan", race="Human", class_="Fighter", level=level,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=40, hp_max=40, armor_class=16, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )
    c.action_surges_remaining = action_surge_max_for(level)
    return c


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
    from auto_dm.engine.resources import channel_divinity_max_for
    c.channel_divinity_remaining = channel_divinity_max_for(level)
    return c


def make_bard(level: int = 5) -> Character:
    c = Character(
        id="b1", name="Finn", race="Half-Elf", class_="Bard", level=level,
        background="Entertainer", alignment="CG",
        abilities=AbilityScores(
            strength=10, dexterity=14, constitution=12,
            intelligence=12, wisdom=10, charisma=16,
        ),
        hp_current=28, hp_max=28, armor_class=14, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.bardic_inspiration_die = bardic_inspiration_die_for(level)
    c.bardic_inspiration_max = bardic_inspiration_max_for(3)
    c.bardic_inspiration_uses = c.bardic_inspiration_max
    return c


class TestKi:
    def test_max_scales_with_level(self):
        assert ki_max_for(1) == 1
        assert ki_max_for(5) == 5
        assert ki_max_for(10) == 10
        assert ki_max_for(20) == 20

    def test_spend(self):
        m = make_monk()
        assert spend_ki(m, 2) is True
        assert m.ki_points == 3

    def test_spend_insufficient(self):
        m = make_monk()
        m.ki_points = 0
        assert spend_ki(m, 1) is False

    def test_short_rest_recovery(self):
        m = make_monk()
        m.ki_points = 0
        rec = recover_ki_on_short_rest(m)
        assert rec == 5
        assert m.ki_points == 5


class TestSorceryPoints:
    def test_max_scales_with_level(self):
        assert sorcery_points_max_for(1) == 1
        assert sorcery_points_max_for(10) == 10

    def test_spend(self):
        s = make_sorcerer()
        assert spend_sorcery_points(s, 3) is True
        assert s.sorcery_points == 2

    def test_create_slot_from_points(self):
        s = make_sorcerer()
        before_slots = s.spellcasting.spell_slots[1]
        assert create_spell_slot_from_points(s, 1) is True
        assert s.sorcery_points == 5 - 2
        assert s.spellcasting.spell_slots[1] == before_slots + 1

    def test_create_slot_costs(self):
        s = make_sorcerer()
        s.sorcery_points = 100  # Plenty
        assert create_spell_slot_from_points(s, 1) is True
        assert s.sorcery_points == 98
        assert create_spell_slot_from_points(s, 2) is True
        assert s.sorcery_points == 95  # 100 - 3
        assert create_spell_slot_from_points(s, 5) is True
        assert s.sorcery_points == 88  # 95 - 7

    def test_create_slot_insufficient_points(self):
        s = make_sorcerer()
        s.sorcery_points = 1
        assert create_spell_slot_from_points(s, 1) is False

    def test_convert_slot_to_points(self):
        s = make_sorcerer()
        assert convert_slot_to_points(s, 2) is True
        assert s.spellcasting.spell_slots[2] == 1
        assert s.sorcery_points == 5 + 2

    def test_convert_slot_empty(self):
        s = make_sorcerer()
        assert convert_slot_to_points(s, 5) is False

    def test_long_rest_recovery(self):
        s = make_sorcerer()
        s.sorcery_points = 0
        rec = recover_sorcery_on_long_rest(s)
        assert rec == 5


class TestLayOnHands:
    def test_pool_size(self):
        assert lay_on_hands_pool_for(1) == 5
        assert lay_on_hands_pool_for(5) == 25
        assert lay_on_hands_pool_for(20) == 100

    def test_spend(self):
        p = make_paladin()
        assert spend_lay_on_hands(p, 10) is True
        assert p.lay_on_hands_pool == 15

    def test_spend_insufficient(self):
        p = make_paladin()
        assert spend_lay_on_hands(p, 100) is False

    def test_heal_capped_by_pool(self):
        p = make_paladin()
        healed = heal_lay_on_hands(p, 100)
        assert healed == 25
        assert p.lay_on_hands_pool == 0


class TestSecondWind:
    def test_max(self):
        assert second_wind_max_for(1) == 1
        assert second_wind_max_for(10) == 1
        assert second_wind_max_for(17) == 2
        assert second_wind_max_for(20) == 2

    def test_can_use(self):
        f = make_fighter()
        f.second_wind_used = False
        assert can_use_second_wind(f) is True
        f.second_wind_used = True
        assert can_use_second_wind(f) is False

    def test_heal_roll(self):
        heal = roll_second_wind_heal(5)
        assert 6 <= heal <= 15  # 1d10 + 5

    def test_short_rest_recovery(self):
        f = make_fighter()
        f.second_wind_used = True
        rec = short_rest_recovery(f)
        assert "second_wind" in rec
        assert f.second_wind_used is False


class TestActionSurge:
    def test_max(self):
        assert action_surge_max_for(1) == 1
        assert action_surge_max_for(17) == 2

    def test_use(self):
        f = make_fighter()
        assert action_surge(f) is True
        assert f.action_surges_remaining == 0
        assert action_surge(f) is False

    def test_short_rest_recovery(self):
        f = make_fighter()
        f.action_surges_remaining = 0
        rec = short_rest_recovery(f)
        assert "action_surge" in rec
        assert f.action_surges_remaining == 1


class TestChannelDivinity:
    def test_max(self):
        from auto_dm.engine.resources import channel_divinity_max_for
        assert channel_divinity_max_for(2) == 1
        assert channel_divinity_max_for(18) == 2

    def test_use(self):
        c = make_cleric()
        assert use_channel_divinity(c) is True
        assert c.channel_divinity_remaining == 0
        assert use_channel_divinity(c) is False


class TestBardicInspiration:
    def test_die_progression(self):
        assert bardic_inspiration_die_for(1) == 6
        assert bardic_inspiration_die_for(4) == 6
        assert bardic_inspiration_die_for(5) == 8
        assert bardic_inspiration_die_for(9) == 8
        assert bardic_inspiration_die_for(10) == 10
        assert bardic_inspiration_die_for(15) == 12
        assert bardic_inspiration_die_for(20) == 12

    def test_max(self):
        assert bardic_inspiration_max_for(3) == 3
        assert bardic_inspiration_max_for(0) == 1  # min 1

    def test_spend(self):
        b = make_bard()
        assert spend_bardic_inspiration(b) is True
        assert b.bardic_inspiration_uses == 2

    def test_short_rest_recovery(self):
        b = make_bard()
        b.bardic_inspiration_uses = 0
        rec = short_rest_recovery(b)
        assert "bardic_inspiration" in rec


class TestLongRestRecovery:
    def test_refills_all_pools(self):
        p = make_paladin()
        p.lay_on_hands_pool = 0
        m = make_monk()
        m.ki_points = 0
        # Different characters; check each pool separately
        rec = long_rest_recovery(p)
        assert rec.get("lay_on_hands") == 25
        rec = long_rest_recovery(m)
        assert rec.get("ki") == 5
