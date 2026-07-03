"""Phase 25g tests: spell slot tables L1-L20 + capstone mechanics.

Covers:
- Full caster slot tables (Wizard/Cleric/Druid/Bard/Sorcerer) L1-L20.
- Half caster slot tables (Paladin/Ranger) L1-L20.
- Warlock Pact Magic (1/2/3/4 slots, all at the same level).
- Cantrips known L1-L20 (clamping thresholds).
- Spells known L1-L20 for Bard / Sorcerer / Warlock.
- Wizard spellbook size (6 + 2/level, 44 at L20).
- Prepared count formula (mod + level for full casters,
  mod + level/2 for Paladin, min 1).
- Capstone flag wiring for each class at L20.
- Primal Champion +4 STR/CON (Barbarian L20).
- Brutal Critical 1/2/3 dice at L9/13/17 (Barbarian).
- Signature Spells, Arcane Apotheosis, Archdruid, Perfect Self,
  Foe Slayer, Stroke of Luck, Eldritch Master, Divine Intervention
  Improvement, Mystic Arcanum.
- /level-up narration includes the right capstone names.
"""
from __future__ import annotations

from typing import Optional

import pytest

from auto_dm.character.level_up import (
    apply_class_features,
    features_gained_at_class_level,
)
from auto_dm.character.spells import (
    _FULL_CASTER_SLOTS,
    _HALF_CASTER_SLOTS,
    _WARLOCK_PACT_MAGIC,
    get_cantrips_known,
    get_prepared_count,
    get_spell_slots,
    get_spellbook_size,
    get_spells_known_max,
)
from auto_dm.engine.class_features import (
    WARLOCK_MYSTIC_ARCANUM_LEVELS,
    arcane_apotheosis_active,
    arcane_apotheosis_sorcery_cap,
    apply_foe_slayer_bonus,
    can_cast_in_wild_shape,
    can_learn_mystic_arcanum,
    capstone_summary,
    cast_mystic_arcanum,
    cast_signature_spell,
    choose_signature_spells,
    divine_intervention_no_consume,
    eldritch_master_active,
    foe_slayer_active,
    has_signature_spell,
    learn_mystic_arcanum,
    primal_champion_damage_bonus,
    reset_mystic_arcanum,
    reset_signature_spells,
    reset_stroke_of_luck,
    trigger_eldritch_master,
    trigger_perfect_self,
    trigger_stroke_of_luck,
)
from auto_dm.engine.progression import (
    XP_THRESHOLDS,
    proficiency_bonus_for,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    Spellcasting,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_character(
    class_name: str = "Fighter",
    level: int = 1,
    subclass: Optional[str] = None,
    *,
    con: int = 14,
    str_score: int = 14,
    int_score: int = 10,
    wis_score: int = 12,
    cha_score: int = 10,
    hit_dice: str = "1d10",
    hp_max: int = 10,
    hp_current: Optional[int] = None,
    spellcasting: Optional[Spellcasting] = None,
    ki_points: int = 0,
    favored_enemies: Optional[list[str]] = None,
) -> Character:
    return Character(
        id=f"{class_name.lower()}_{level}",
        name=f"Test {class_name}",
        race="Human",
        **{"class": class_name},
        level=level,
        subclass=subclass,
        background="Soldier",
        alignment="LG",
        abilities=AbilityScores(
            strength=str_score,
            dexterity=10,
            constitution=con,
            intelligence=int_score,
            wisdom=wis_score,
            charisma=cha_score,
        ),
        hp_current=hp_current if hp_current is not None else hp_max,
        hp_max=hp_max,
        armor_class=14,
        speed=30,
        proficiency_bonus=proficiency_bonus_for(level),
        hit_dice=hit_dice,
        hit_dice_remaining=level,
        inventory=[],
        xp=XP_THRESHOLDS[level - 1] if level <= 20 else 0,
        spellcasting=spellcasting,
        ki_points=ki_points,
        favored_enemies=favored_enemies or [],
    )


# ===========================================================================
# Spell slot tables — full casters
# ===========================================================================


class TestFullCasterSlots:
    """Per PHB p. 113."""

    def test_table_covers_levels_1_to_20(self):
        assert len(_FULL_CASTER_SLOTS) == 20

    def test_wizard_l1_two_first_level(self):
        assert get_spell_slots("Wizard", 1) == {1: 2}

    def test_wizard_l5_first_three_levels(self):
        assert get_spell_slots("Wizard", 5) == {1: 4, 2: 3, 3: 2}

    def test_wizard_l9_fifth_level_unlocks(self):
        slots = get_spell_slots("Wizard", 9)
        assert slots[5] == 1

    def test_wizard_l11_sixth_level(self):
        slots = get_spell_slots("Wizard", 11)
        assert slots[6] == 1

    def test_wizard_l17_ninth_level_unlocks(self):
        slots = get_spell_slots("Wizard", 17)
        assert slots[9] == 1

    def test_wizard_l20_full_progression(self):
        # PHB Wizard L20: 4/3/3/3/3/2/2/1/1
        slots = get_spell_slots("Wizard", 20)
        assert slots == {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1}

    def test_cleric_uses_full_caster_table(self):
        # Cleric, Druid, Bard, Sorcerer all use the same progression.
        for cls in ("Cleric", "Druid", "Bard", "Sorcerer", "Wizard"):
            assert get_spell_slots(cls, 17) == get_spell_slots("Wizard", 17)
            assert get_spell_slots(cls, 20) == get_spell_slots("Wizard", 20)


# ===========================================================================
# Spell slot tables — half casters
# ===========================================================================


class TestHalfCasterSlots:
    """Per PHB p. 113 — Paladin, Ranger."""

    def test_table_covers_levels_1_to_20(self):
        assert len(_HALF_CASTER_SLOTS) == 20

    def test_paladin_l1_no_slots(self):
        assert get_spell_slots("Paladin", 1) == {}

    def test_paladin_l2_first_slots(self):
        assert get_spell_slots("Paladin", 2) == {1: 2}

    def test_paladin_l5_two_levels(self):
        assert get_spell_slots("Paladin", 5) == {1: 4, 2: 2}

    def test_paladin_l17_fifth_level_unlocks(self):
        slots = get_spell_slots("Paladin", 17)
        assert slots[5] == 1

    def test_paladin_l20_full_progression(self):
        # PHB Paladin L20: 4/3/3/3/2
        assert get_spell_slots("Paladin", 20) == {1: 4, 2: 3, 3: 3, 4: 3, 5: 2}

    def test_ranger_matches_paladin(self):
        # Ranger and Paladin share the same progression.
        for lvl in range(1, 21):
            assert get_spell_slots("Ranger", lvl) == get_spell_slots("Paladin", lvl)


# ===========================================================================
# Spell slot tables — Warlock Pact Magic
# ===========================================================================


class TestWarlockPactMagic:
    """Per PHB p. 107 — fixed slot count, all at the same level."""

    def test_pact_magic_table_covers_1_to_20(self):
        assert len(_WARLOCK_PACT_MAGIC) == 20

    def test_warlock_l1_one_first_level_slot(self):
        assert get_spell_slots("Warlock", 1) == {1: 1}

    def test_warlock_l2_two_first_level_slots(self):
        assert get_spell_slots("Warlock", 2) == {1: 2}

    def test_warlock_l11_three_fifth_level_slots(self):
        assert get_spell_slots("Warlock", 11) == {5: 3}

    def test_warlock_l17_four_fifth_level_slots(self):
        assert get_spell_slots("Warlock", 17) == {5: 4}

    def test_warlock_l20_four_fifth_level_slots(self):
        # Cap at 4 slots, all at 5th level.
        assert get_spell_slots("Warlock", 20) == {5: 4}

    def test_warlock_pact_magic_progression(self):
        # All slots are the same level (no mixed-level slots).
        for lvl in range(1, 21):
            slots = get_spell_slots("Warlock", lvl)
            assert len(slots) == 1
            (only_lvl, count) = list(slots.items())[0]
            # Only ever 1, 2, 3, or 4 slots.
            assert count in (1, 2, 3, 4)
            # Slot level is 1, 2, 3, 4, or 5.
            assert only_lvl in (1, 2, 3, 4, 5)


# ===========================================================================
# Cantrips known
# ===========================================================================


class TestCantripsKnown:
    @pytest.mark.parametrize("cls,expected", [
        ("Bard",     {1: 2, 4: 3, 10: 4}),
        ("Cleric",   {1: 3, 4: 4, 10: 5}),
        ("Druid",    {1: 2, 4: 3, 10: 4}),
        ("Sorcerer", {1: 4, 4: 5, 10: 6}),
        ("Warlock",  {1: 2, 4: 3, 10: 4}),
        ("Wizard",   {1: 3, 4: 4, 10: 5}),
    ])
    def test_cantrip_thresholds(self, cls, expected):
        for level, n in expected.items():
            assert get_cantrips_known(cls, level) == n

    def test_cantrips_known_at_20(self):
        assert get_cantrips_known("Cleric", 20) == 5
        assert get_cantrips_known("Sorcerer", 20) == 6
        assert get_cantrips_known("Wizard", 20) == 5


# ===========================================================================
# Spells known — known casters
# ===========================================================================


class TestSpellsKnown:
    def test_bard_l1(self):
        assert get_spells_known_max("Bard", 1) == 4

    def test_bard_l20(self):
        assert get_spells_known_max("Bard", 20) == 22

    def test_sorcerer_l1(self):
        assert get_spells_known_max("Sorcerer", 1) == 2

    def test_sorcerer_l20(self):
        assert get_spells_known_max("Sorcerer", 20) == 15

    def test_warlock_l1(self):
        assert get_spells_known_max("Warlock", 1) == 2

    def test_warlock_l20(self):
        assert get_spells_known_max("Warlock", 20) == 15

    def test_bard_known_monotonic(self):
        prev = 0
        for lvl in range(1, 21):
            n = get_spells_known_max("Bard", lvl)
            assert n >= prev
            prev = n

    def test_full_caster_no_known_cap(self):
        # Cleric / Druid / Paladin / Wizard use prepared, not known.
        assert get_spells_known_max("Cleric", 1) == 0
        assert get_spells_known_max("Wizard", 1) == 0


# ===========================================================================
# Wizard spellbook
# ===========================================================================


class TestWizardSpellbook:
    def test_spellbook_l1(self):
        assert get_spellbook_size("Wizard", 1) == 6

    def test_spellbook_l20(self):
        assert get_spellbook_size("Wizard", 20) == 44

    def test_spellbook_increments_by_two(self):
        for lvl in range(1, 20):
            assert (
                get_spellbook_size("Wizard", lvl + 1)
                - get_spellbook_size("Wizard", lvl)
                == 2
            )

    def test_spellbook_zero_for_non_wizard(self):
        assert get_spellbook_size("Cleric", 10) == 0
        assert get_spellbook_size("Sorcerer", 10) == 0


# ===========================================================================
# Prepared count
# ===========================================================================


class TestPreparedCount:
    def test_cleric_l1_mod_zero(self):
        assert get_prepared_count("Cleric", 1, 0) == 1  # min 1

    def test_cleric_l5_mod_three(self):
        assert get_prepared_count("Cleric", 5, 3) == 8  # 3+5

    def test_cleric_l20_mod_four(self):
        # 4 + 20 = 24
        assert get_prepared_count("Cleric", 20, 4) == 24

    def test_druid_l10_mod_two(self):
        assert get_prepared_count("Druid", 10, 2) == 12  # 2+10

    def test_paladin_uses_half_level(self):
        # Paladin: mod + level/2 (rounded down)
        # mod 0, L20 -> 0 + 10 = 10
        assert get_prepared_count("Paladin", 20, 0) == 10
        # mod 3, L20 -> 3 + 10 = 13
        assert get_prepared_count("Paladin", 20, 3) == 13

    def test_paladin_l1_mod_zero_min_one(self):
        # mod 0, L1 -> 0 + 0 = 0, but min 1
        assert get_prepared_count("Paladin", 1, 0) == 1

    def test_wizard_prepared_count(self):
        # Wizard uses mod + level formula (same as Cleric).
        assert get_prepared_count("Wizard", 20, 4) == 24


# ===========================================================================
# Capstone flag wiring — L20
# ===========================================================================


class TestCapstoneFlags:
    """Each class gains exactly one capstone at L20."""

    @pytest.mark.parametrize("cls,flag", [
        ("Barbarian", "has_primal_champion"),
        ("Cleric", "has_divine_intervention_improvement"),
        ("Druid", "has_archdruid"),
        ("Monk", "has_perfect_self"),
        ("Ranger", "has_foe_slayer"),
        ("Rogue", "has_stroke_of_luck"),
        ("Sorcerer", "has_arcane_apotheosis"),
        ("Warlock", "has_eldritch_master"),
        ("Wizard", "has_signature_spells"),
    ])
    def test_capstone_at_twenty(self, cls, flag):
        c = _make_character(class_name=cls, level=20)
        apply_class_features(c, at_level=20)
        assert getattr(c, flag) is True

    @pytest.mark.parametrize("cls,flag", [
        ("Barbarian", "has_primal_champion"),
        ("Cleric", "has_divine_intervention_improvement"),
        ("Druid", "has_archdruid"),
        ("Monk", "has_perfect_self"),
        ("Ranger", "has_foe_slayer"),
        ("Rogue", "has_stroke_of_luck"),
        ("Sorcerer", "has_arcane_apotheosis"),
        ("Warlock", "has_eldritch_master"),
        ("Wizard", "has_signature_spells"),
    ])
    def test_capstone_below_twenty_inactive(self, cls, flag):
        c = _make_character(class_name=cls, level=19)
        apply_class_features(c, at_level=19)
        assert getattr(c, flag) is False

    def test_barbarian_l20_increases_str(self):
        c = _make_character(class_name="Barbarian", level=20, str_score=14, con=14)
        apply_class_features(c, at_level=20)
        # Primal Champion: +4 STR/CON (max 24)
        assert c.abilities.strength == 18
        assert c.abilities.constitution == 18

    def test_barbarian_l20_caps_str_at_24(self):
        c = _make_character(class_name="Barbarian", level=20, str_score=20, con=20)
        apply_class_features(c, at_level=20)
        # Already 20, +4 capped at 24.
        assert c.abilities.strength == 24
        assert c.abilities.constitution == 24

    def test_barbarian_l20_damage_bonus(self):
        c = _make_character(class_name="Barbarian", level=20)
        apply_class_features(c, at_level=20)
        assert primal_champion_damage_bonus(c) == 2

    def test_rogue_l20_stroke_of_luck_uses(self):
        c = _make_character(class_name="Rogue", level=20)
        apply_class_features(c, at_level=20)
        assert c.stroke_of_luck_uses_remaining == 1

    def test_brutal_critical_progression(self):
        for level, expected_dice in [(8, 0), (9, 1), (12, 1), (13, 2), (16, 2), (17, 3), (19, 3)]:
            c = _make_character(class_name="Barbarian", level=level)
            apply_class_features(c, at_level=level)
            assert c.brutal_critical_dice == expected_dice, (
                f"L{level} expected {expected_dice} brutal crit dice, got {c.brutal_critical_dice}"
            )


# ===========================================================================
# Capstone behavior — runtime mechanics
# ===========================================================================


class TestWizardSignatureSpells:
    def test_signature_spell_choose(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        choose_signature_spells(c, ["Fireball", "Lightning Bolt"])
        assert c.signature_spell_names == ["Fireball", "Lightning Bolt"]

    def test_signature_spell_wrong_count_raises(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        with pytest.raises(ValueError):
            choose_signature_spells(c, ["Fireball"])
        with pytest.raises(ValueError):
            choose_signature_spells(c, ["Fireball", "Lightning Bolt", "Wish"])

    def test_signature_spell_must_be_3rd_or_lower(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        with pytest.raises(ValueError):
            choose_signature_spells(c, ["Fireball", "Wish"])

    def test_signature_spell_cast_uses_consume(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        choose_signature_spells(c, ["Fireball", "Lightning Bolt"])
        reset_signature_spells(c)
        assert c.signature_spell_uses_remaining == {
            "Fireball": 1, "Lightning Bolt": 1
        }
        assert cast_signature_spell(c, "Fireball") is True
        assert c.signature_spell_uses_remaining["Fireball"] == 0
        # Cannot cast twice in same rest.
        assert cast_signature_spell(c, "Fireball") is False

    def test_signature_spell_presence_check(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        choose_signature_spells(c, ["Fireball", "Lightning Bolt"])
        assert has_signature_spell(c, "Fireball") is True
        assert has_signature_spell(c, "Wish") is False


class TestWarlockMysticArcanum:
    def test_mystic_arcanum_levels(self):
        # 6th at L11, 7th at L13, 8th at L15, 9th at L17
        assert WARLOCK_MYSTIC_ARCANUM_LEVELS == {6: 11, 7: 13, 8: 15, 9: 17}

    def test_can_learn_6th_at_l11(self):
        c = _make_character(class_name="Warlock", level=11, cha_score=18)
        assert can_learn_mystic_arcanum(c, 6) is True
        assert can_learn_mystic_arcanum(c, 7) is False

    def test_can_learn_9th_at_l17(self):
        c = _make_character(class_name="Warlock", level=17, cha_score=18)
        assert can_learn_mystic_arcanum(c, 9) is True

    def test_learn_arcanum_populates_known(self):
        c = _make_character(class_name="Warlock", level=11, cha_score=18)
        learn_mystic_arcanum(c, 6, "Circle of Death")
        # 6th-level Warlock spell: Circle of Death (PHB p. 221).
        assert c.mystic_arcanum_known[6] == "Circle of Death"

    def test_learn_arcanum_below_level_raises(self):
        c = _make_character(class_name="Warlock", level=10, cha_score=18)
        with pytest.raises(ValueError):
            learn_mystic_arcanum(c, 6, "Circle of Death")

    def test_cast_arcanum_consumes_use(self):
        c = _make_character(class_name="Warlock", level=11, cha_score=18)
        learn_mystic_arcanum(c, 6, "Circle of Death")
        reset_mystic_arcanum(c)
        assert c.mystic_arcanum_uses[6] == 1
        assert cast_mystic_arcanum(c, 6) is True
        # Once per long rest per arcanum level.
        assert cast_mystic_arcanum(c, 6) is False
        # Reset and try again.
        reset_mystic_arcanum(c)
        assert cast_mystic_arcanum(c, 6) is True


class TestRogueStrokeOfLuck:
    def test_trigger_consumes_use(self):
        c = _make_character(class_name="Rogue", level=20)
        apply_class_features(c, at_level=20)
        assert c.stroke_of_luck_uses_remaining == 1
        assert trigger_stroke_of_luck(c) is True
        # Only once per short rest.
        assert trigger_stroke_of_luck(c) is False

    def test_reset_refills_use(self):
        c = _make_character(class_name="Rogue", level=20)
        apply_class_features(c, at_level=20)
        trigger_stroke_of_luck(c)
        assert c.stroke_of_luck_uses_remaining == 0
        reset_stroke_of_luck(c)
        assert c.stroke_of_luck_uses_remaining == 1


class TestMonkPerfectSelf:
    def test_trigger_requires_4_ki(self):
        c = _make_character(class_name="Monk", level=20, ki_points=3)
        apply_class_features(c, at_level=20)
        assert trigger_perfect_self(c) is False  # only 3 ki

    def test_trigger_recovers_all_ki(self):
        c = _make_character(class_name="Monk", level=20, ki_points=10)
        apply_class_features(c, at_level=20)
        assert c.ki_points == 10
        assert trigger_perfect_self(c) is True
        # Perfect Self refills to level (max ki = level for monks).
        assert c.ki_points == 20
        # Only once per short rest.
        assert trigger_perfect_self(c) is False


class TestBarbarianPrimalChampion:
    def test_damage_bonus_when_active(self):
        c = _make_character(class_name="Barbarian", level=20)
        apply_class_features(c, at_level=20)
        assert primal_champion_damage_bonus(c) == 2

    def test_damage_bonus_inactive(self):
        c = _make_character(class_name="Barbarian", level=19)
        apply_class_features(c, at_level=19)
        assert primal_champion_damage_bonus(c) == 0


class TestSorcererArcaneApotheosis:
    def test_sorcery_cap(self):
        assert arcane_apotheosis_sorcery_cap() == 20

    def test_active_at_l20(self):
        c = _make_character(class_name="Sorcerer", level=20, cha_score=18)
        apply_class_features(c, at_level=20)
        assert arcane_apotheosis_active(c) is True

    def test_inactive_below_l20(self):
        c = _make_character(class_name="Sorcerer", level=19, cha_score=18)
        apply_class_features(c, at_level=19)
        assert arcane_apotheosis_active(c) is False


class TestDruidArchdruid:
    def test_can_cast_in_wild_shape_at_l20(self):
        c = _make_character(class_name="Druid", level=20, wis_score=18)
        apply_class_features(c, at_level=20)
        assert can_cast_in_wild_shape(c) is True

    def test_cannot_cast_in_wild_shape_below_l20(self):
        c = _make_character(class_name="Druid", level=19, wis_score=18)
        apply_class_features(c, at_level=19)
        assert can_cast_in_wild_shape(c) is False


class TestClericDivineInterventionImprovement:
    def test_no_consume_at_l20(self):
        c = _make_character(class_name="Cleric", level=20, wis_score=18)
        apply_class_features(c, at_level=20)
        assert divine_intervention_no_consume(c) is True

    def test_consume_below_l20(self):
        c = _make_character(class_name="Cleric", level=19, wis_score=18)
        apply_class_features(c, at_level=19)
        assert divine_intervention_no_consume(c) is False


class TestWarlockEldritchMaster:
    def _warlock_with_slots(self) -> Character:
        """Build a Warlock L20 with spellcasting + pact slots."""
        c = _make_character(class_name="Warlock", level=20, cha_score=18)
        apply_class_features(c, at_level=20)
        slots = get_spell_slots("Warlock", 20)
        from auto_dm.state.models import Spellcasting
        c.spellcasting = Spellcasting(
            ability=Ability.CHA,
            save_dc=20,
            attack_bonus=12,
            cantrips_known=[],
            spells_known=[],
            spells_prepared=[],
            spell_slots={5: 0},  # expended
            spell_slots_max=dict(slots),
        )
        return c

    def test_active_at_l20(self):
        c = self._warlock_with_slots()
        assert eldritch_master_active(c) is True

    def test_trigger_refuels_slots(self):
        c = self._warlock_with_slots()
        assert c.spellcasting.spell_slots[5] == 0
        assert trigger_eldritch_master(c) is True
        assert c.spellcasting.spell_slots[5] == 4  # Warlock L20 has 4 slots
        # Only once per long rest.
        assert trigger_eldritch_master(c) is False
        assert c.eldritch_master_used is True


class TestRangerFoeSlayer:
    def _ranger(self, favored: bool) -> Character:
        c = _make_character(
            class_name="Ranger", level=20, wis_score=18,
            favored_enemies=["humanoid"] if favored else [],
        )
        apply_class_features(c, at_level=20)
        return c

    def test_active_at_l20(self):
        c = self._ranger(favored=True)
        assert foe_slayer_active(c) is True

    def test_favored_enemy_bonus(self):
        c = self._ranger(favored=True)
        # WIS 18 -> mod +4
        assert apply_foe_slayer_bonus(c, favored=True) == (4, 4)

    def test_non_favored_enemy_zero_bonus(self):
        # The caller is responsible for checking the target's type
        # against favored_enemies. If the target isn't favored, the
        # caller passes favored=False.
        c = self._ranger(favored=True)
        assert apply_foe_slayer_bonus(c, favored=False) == (0, 0)

    def test_once_per_turn(self):
        c = self._ranger(favored=True)
        assert apply_foe_slayer_bonus(c, favored=True) == (4, 4)
        c.foe_slayer_used_this_turn = True
        assert apply_foe_slayer_bonus(c, favored=True) == (0, 0)


# ===========================================================================
# Capstone summary
# ===========================================================================


class TestCapstoneSummary:
    def test_no_capstone_below_20(self):
        c = _make_character(class_name="Wizard", level=19)
        apply_class_features(c, at_level=19)
        assert capstone_summary(c) == []

    def test_capstone_at_20(self):
        c = _make_character(class_name="Wizard", level=20)
        apply_class_features(c, at_level=20)
        assert "Signature Spells" in capstone_summary(c)


# ===========================================================================
# Features gained at level narration
# ===========================================================================


class TestFeaturesGainedAtLevel:
    @pytest.mark.parametrize("cls,level,expected", [
        ("Barbarian", 9, "Brutal Critical (1 die)"),
        ("Barbarian", 13, "Brutal Critical (2 dice)"),
        ("Barbarian", 17, "Brutal Critical (3 dice)"),
        ("Barbarian", 20, "Primal Champion"),
        ("Wizard", 20, "Signature Spells"),
        ("Sorcerer", 20, "Arcane Apotheosis"),
        ("Rogue", 20, "Stroke of Luck"),
        ("Monk", 20, "Perfect Self"),
        ("Ranger", 20, "Foe Slayer"),
        ("Druid", 20, "Archdruid"),
        ("Cleric", 20, "Divine Intervention Improvement"),
        ("Warlock", 20, "Eldritch Master"),
    ])
    def test_features_gained(self, cls, level, expected):
        c = _make_character(class_name=cls, level=level)
        feats = features_gained_at_class_level(c, level)
        assert expected in feats


# ===========================================================================
# Full 1-20 progression smoke test
# ===========================================================================


class TestFullProgression:
    def test_wizard_1_to_20(self):
        c = _make_character(class_name="Wizard", level=1, hit_dice="1d6")
        for _ in range(19):
            from auto_dm.engine.progression import level_up
            level_up(c, hp_roll=4)
        apply_class_features(c, at_level=20)
        assert c.level == 20
        assert c.proficiency_bonus == 6
        # 9th-level slot unlocked.
        assert get_spell_slots("Wizard", 20)[9] == 1
        # Capstone active.
        assert c.has_signature_spells is True

    def test_barbarian_1_to_20(self):
        c = _make_character(
            class_name="Barbarian", level=1, hit_dice="1d12", str_score=14, con=14,
        )
        for _ in range(19):
            from auto_dm.engine.progression import level_up
            level_up(c, hp_roll=6)
        apply_class_features(c, at_level=20)
        assert c.level == 20
        assert c.has_primal_champion is True
        assert c.brutal_critical_dice == 3
        # +4 STR/CON.
        assert c.abilities.strength == 18
        assert c.abilities.constitution == 18
