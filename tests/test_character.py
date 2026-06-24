"""Tests for the character builder and spell selection."""
from __future__ import annotations

import pytest

from auto_dm.character import (
    STANDARD_ARRAY,
    STAT_BLOCK_SIZE,
    CharacterBuilder,
    prepare_caster_spells,
    select_cantrips,
)
from auto_dm.character.builder import (
    parse_class_skill_options,
    parse_skill_name,
)
from auto_dm.character.spells import (
    get_cantrips_known,
    get_prepared_count,
    get_spell_slots,
    get_spellbook_size,
)
from auto_dm.phb import get_class, get_spells_for_class
from auto_dm.state.models import Ability, AbilityScores, Skill


# ============================================================================
# Constants and helpers
# ============================================================================


class TestConstants:
    def test_standard_array_size(self):
        assert len(STANDARD_ARRAY) == STAT_BLOCK_SIZE
        assert all(8 <= s <= 15 for s in STANDARD_ARRAY)

    def test_skill_name_parsing(self):
        assert parse_skill_name("Animal Handling") == Skill.ANIMAL_HANDLING
        assert parse_skill_name("athletics") == Skill.ATHLETICS
        assert parse_skill_name("Sleight of Hand") == Skill.SLEIGHT_OF_HAND

    def test_invalid_skill_raises(self):
        with pytest.raises(ValueError):
            parse_skill_name("Nonexistent")

    def test_parse_skill_options(self):
        text = "Choose two from Animal Handling, Athletics, Intimidation, Nature, Perception, and Survival"
        opts = parse_class_skill_options(text)
        assert "animal handling" in opts
        assert "athletics" in opts
        assert "perception" in opts
        assert len(opts) == 6


# ============================================================================
# Required fields
# ============================================================================


class TestRequiredFields:
    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            CharacterBuilder().build()

    def test_missing_race_raises(self):
        with pytest.raises(ValueError, match="race"):
            CharacterBuilder().with_name("X").build()

    def test_missing_class_raises(self):
        with pytest.raises(ValueError, match="class"):
            CharacterBuilder().with_name("X").with_race("Dwarf").build()

    def test_missing_abilities_raises(self):
        with pytest.raises(ValueError, match="ability scores"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("Dwarf")
                .with_class("Fighter")
                .build()
            )

    def test_unknown_race_raises(self):
        with pytest.raises(ValueError, match="Unknown race"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("NotARace")
                .with_class("Fighter")
                .with_standard_array()
                .build()
            )

    def test_unknown_class_raises(self):
        with pytest.raises(ValueError, match="Unknown class"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("Dwarf")
                .with_class("NotAClass")
                .with_standard_array()
                .build()
            )


# ============================================================================
# Ability scores
# ============================================================================


class TestAbilityScores:
    def test_wrong_number_of_scores_raises(self):
        with pytest.raises(ValueError, match="Expected 6"):
            CharacterBuilder().with_ability_scores([15, 14, 13])

    def test_out_of_range_score_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            CharacterBuilder().with_ability_scores([25, 14, 13, 12, 10, 8])

    def test_standard_array_used(self):
        builder = CharacterBuilder().with_standard_array()
        assert builder._ability_scores == list(STANDARD_ARRAY)

    def test_racial_bonuses_applied(self):
        # Dwarf +2 CON, Hill Dwarf +1 WIS
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Dwarf", subrace="Hill Dwarf")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        # Standard array: [15, 14, 13, 12, 10, 8] -> STR DEX CON INT WIS CHA
        # +2 CON: 13 -> 15
        # +1 WIS: 10 -> 11
        assert draft.character.abilities.strength == 15
        assert draft.character.abilities.dexterity == 14
        assert draft.character.abilities.constitution == 15
        assert draft.character.abilities.wisdom == 11
        assert draft.character.abilities.charisma == 8

    def test_rolled_stats_in_valid_range(self):
        from auto_dm.engine.dice import roll_stats

        for _ in range(20):
            stats = roll_stats()
            assert len(stats) == 6
            assert all(3 <= s <= 18 for s in stats)

    def test_unknown_subrace_raises(self):
        with pytest.raises(ValueError, match="Subrace .* not found"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("Dwarf", subrace="NotASubrace")
                .with_class("Fighter")
                .with_standard_array()
                .with_skills(["athletics", "perception"])
                .build()
            )


# ============================================================================
# HP calculation
# ============================================================================


class TestHP:
    def test_fighter_level_1(self):
        # Standard array CON 13 = +1; Fighter d10 max 10
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        # 10 + 1 = 11
        assert draft.character.hp_max == 11

    def test_barbarian_level_1(self):
        # d12 max 12; standard array CON 13 = +1
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Barbarian")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        assert draft.character.hp_max == 13

    def test_wizard_level_1(self):
        # d6 max 6; CON 13 = +1
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Wizard")
            .with_standard_array()
            .with_skills(["arcana", "history"])
            .build()
        )
        assert draft.character.hp_max == 7

    def test_hill_dwarf_gets_extra_hp(self):
        # Fighter d10 max 10, CON 15 = +2, Hill Dwarf +1
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Dwarf", subrace="Hill Dwarf")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        # 10 + 2 + 1 = 13
        assert draft.character.hp_max == 13

    def test_hp_at_higher_level(self):
        # Level 3 Fighter, CON +1, average = (10//2)+1 = 6
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_level(3)
            .build()
        )
        # 10 + 1 + 2*(6 + 1) = 10 + 1 + 14 = 25
        assert draft.character.hp_max == 25

    def test_minimum_hp_clamped(self):
        # CON 1 = -5, base HP = 6 - 5 = 1, clamped to 1
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Wizard")
            .with_ability_scores([8, 8, 1, 14, 14, 14])
            .with_skills(["arcana", "history"])
            .build()
        )
        assert draft.character.hp_max >= 1


# ============================================================================
# AC calculation
# ============================================================================


class TestAC:
    def test_unarmored_ac(self):
        # DEX 14 = +2; AC = 10 + 2 = 12
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Wizard")
            .with_standard_array()
            .with_skills(["arcana", "history"])
            .build()
        )
        assert draft.character.armor_class == 12

    def test_chain_mail_ac_no_dex(self):
        # Chain mail base 16, heavy doesn't add DEX
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_armor("Chain mail")
            .build()
        )
        assert draft.character.armor_class == 16

    def test_leather_armor_adds_dex(self):
        # Leather base 11 + DEX mod; no max dex
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Rogue")
            .with_standard_array()
            .with_skills(["stealth", "acrobatics"])
            .with_starting_armor("Leather")
            .build()
        )
        # DEX 14 = +2; AC = 11 + 2 = 13
        assert draft.character.armor_class == 13

    def test_half_plate_caps_dex(self):
        # Half plate base 15 + DEX (max +2)
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_armor("Half plate")
            .build()
        )
        # DEX 14 = +2; cap is 2; AC = 15 + 2 = 17
        assert draft.character.armor_class == 17

    def test_shield_adds_two(self):
        # Chain mail 16 + Shield 2
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Cleric")
            .with_standard_array()
            .with_skills(["medicine", "religion"])
            .with_starting_armor("Chain mail")
            .with_shield()
            .build()
        )
        assert draft.character.armor_class == 18


# ============================================================================
# Proficiencies
# ============================================================================


class TestProficiencies:
    def test_fighter_saving_throws(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        saves = draft.character.proficiencies.saves
        assert Ability.STR in saves
        assert Ability.CON in saves

    def test_wizard_saving_throws(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Wizard")
            .with_standard_array()
            .with_skills(["arcana", "history"])
            .build()
        )
        saves = draft.character.proficiencies.saves
        assert Ability.INT in saves
        assert Ability.WIS in saves

    def test_skill_choices_applied(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        skills = draft.character.proficiencies.skills
        assert Skill.ATHLETICS in skills
        assert Skill.PERCEPTION in skills

    def test_invalid_skill_raises(self):
        with pytest.raises(ValueError, match="not in"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("Human")
                .with_class("Fighter")
                .with_standard_array()
                .with_skills(["arcana"])
                .build()
            )

    def test_too_many_skills_raises(self):
        with pytest.raises(ValueError, match="allows 2"):
            (
                CharacterBuilder()
                .with_name("X")
                .with_race("Human")
                .with_class("Fighter")
                .with_standard_array()
                .with_skills(["athletics", "perception", "intimidation"])
                .build()
            )


# ============================================================================
# Equipment
# ============================================================================


class TestEquipment:
    def test_starting_weapon_in_inventory(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_weapon("Longsword")
            .build()
        )
        assert draft.character.equipped.main_hand is not None
        assert draft.character.equipped.main_hand.name == "Longsword"

    def test_starting_armor_in_inventory(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_armor("Chain mail")
            .build()
        )
        assert draft.character.equipped.armor is not None
        assert draft.character.equipped.armor.name == "Chain mail"

    def test_unknown_weapon_ignored(self):
        draft = (
            CharacterBuilder()
            .with_name("X")
            .with_race("Human")
            .with_class("Fighter")
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_weapon("NotARealWeapon")
            .build()
        )
        assert draft.character.equipped.main_hand is None


# ============================================================================
# Spell selection
# ============================================================================


class TestSpellSlots:
    def test_wizard_l1_slots(self):
        assert get_spell_slots("Wizard", 1) == {1: 2}

    def test_wizard_l3_slots(self):
        assert get_spell_slots("Wizard", 3) == {1: 4, 2: 2}

    def test_paladin_l1_no_slots(self):
        assert get_spell_slots("Paladin", 1) == {}

    def test_paladin_l2_slots(self):
        assert get_spell_slots("Paladin", 2) == {1: 2}

    def test_warlock_l1_one_slot(self):
        assert get_spell_slots("Warlock", 1) == {1: 1}


class TestCantripsKnown:
    def test_wizard_l1(self):
        assert get_cantrips_known("Wizard", 1) == 3

    def test_sorcerer_l1(self):
        assert get_cantrips_known("Sorcerer", 1) == 4

    def test_bard_l1(self):
        assert get_cantrips_known("Bard", 1) == 2


class TestPreparedCount:
    def test_cleric_l1_int_mod(self):
        assert get_prepared_count("Cleric", 1, casting_ability_mod=1) == 2

    def test_cleric_l1_low_int(self):
        assert get_prepared_count("Cleric", 1, casting_ability_mod=-1) == 1

    def test_paladin_uses_half_level(self):
        assert get_prepared_count("Paladin", 5, casting_ability_mod=3) == 5


class TestSpellbook:
    def test_wizard_l1(self):
        assert get_spellbook_size("Wizard", 1) == 6

    def test_wizard_l5(self):
        assert get_spellbook_size("Wizard", 5) == 14

    def test_non_wizard_zero(self):
        assert get_spellbook_size("Cleric", 1) == 0


class TestSelectCantrips:
    def test_valid_cantrip_selection(self):
        wizard = get_class("Wizard")
        cantrips = [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:3]
        selected = select_cantrips(wizard, 1, cantrips)
        assert len(selected) == 3

    def test_too_many_cantrips_raises(self):
        wizard = get_class("Wizard")
        cantrips = [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:5]
        with pytest.raises(ValueError, match="knows 3"):
            select_cantrips(wizard, 1, cantrips)

    def test_invalid_cantrip_raises(self):
        wizard = get_class("Wizard")
        with pytest.raises(ValueError, match="not a cantrip"):
            select_cantrips(wizard, 1, ["Fireball"])

    def test_non_caster_raises(self):
        fighter = get_class("Fighter")
        with pytest.raises(ValueError, match="not a spellcasting class"):
            select_cantrips(fighter, 1, [])


class TestPrepareCasterSpells:
    def _wizard_class(self):
        return get_class("Wizard")

    def _wizard_cantrips(self, n=3):
        return [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:n]

    def _wizard_l1(self, n=6):
        return [s.name for s in get_spells_for_class("Wizard") if s.level == 1][:n]

    def _wizard_abilities(self):
        return AbilityScores(
            strength=8,
            dexterity=14,
            constitution=13,
            intelligence=15,
            wisdom=12,
            charisma=10,
        )

    def test_wizard_spellcasting(self):
        cls = self._wizard_class()
        abilities = self._wizard_abilities()
        selection = prepare_caster_spells(
            cls,
            level=1,
            abilities=abilities,
            proficiency_bonus=2,
            cantrips=self._wizard_cantrips(),
            spellbook=self._wizard_l1(),
        )
        sc = selection.to_spellcasting(cls, abilities, 2)
        assert sc.ability == Ability.INT
        # INT 15 = +2; DC = 8 + 2 + 2 = 12
        assert sc.save_dc == 12
        # Attack bonus = 2 + 2 = 4
        assert sc.attack_bonus == 4
        assert sc.spell_slots == {1: 2}
        assert len(selection.cantrips) == 3
        assert len(selection.spellbook) == 6
        assert sc.ritual_casting is True

    def test_sorcerer_known_caster(self):
        cls = get_class("Sorcerer")
        abilities = AbilityScores(
            strength=8, dexterity=14, constitution=13,
            intelligence=10, wisdom=12, charisma=15,
        )
        sorcerer_cantrips = [s.name for s in get_spells_for_class("Sorcerer") if s.level == 0][:4]
        sorcerer_l1 = [s.name for s in get_spells_for_class("Sorcerer") if s.level == 1][:2]
        selection = prepare_caster_spells(
            cls,
            level=1,
            abilities=abilities,
            proficiency_bonus=2,
            cantrips=sorcerer_cantrips,
            spells_known=sorcerer_l1,
        )
        sc = selection.to_spellcasting(cls, abilities, 2)
        # CHA 15 = +2; DC = 8 + 2 + 2 = 12
        assert sc.save_dc == 12
        assert sc.ability == Ability.CHA
        assert len(selection.spells_known) == 2

    def test_too_many_spells_known_raises(self):
        cls = get_class("Sorcerer")
        sorcerer_cantrips = [s.name for s in get_spells_for_class("Sorcerer") if s.level == 0][:4]
        too_many = [s.name for s in get_spells_for_class("Sorcerer") if s.level == 1][:10]
        with pytest.raises(ValueError, match="knows"):
            prepare_caster_spells(
                cls, 1, AbilityScores(
                    strength=8, dexterity=14, constitution=13,
                    intelligence=10, wisdom=12, charisma=15,
                ), 2,
                cantrips=sorcerer_cantrips,
                spells_known=too_many,
            )

    def test_wizard_too_many_in_book_raises(self):
        cls = get_class("Wizard")
        wizard_cantrips = self._wizard_cantrips()
        too_many_book = [s.name for s in get_spells_for_class("Wizard") if s.level == 1][:20]
        with pytest.raises(ValueError, match="book"):
            prepare_caster_spells(
                cls, 1, self._wizard_abilities(), 2,
                cantrips=wizard_cantrips,
                spellbook=too_many_book,
            )


# ============================================================================
# Integration: full character build
# ============================================================================


class TestIntegration:
    def test_full_hill_dwarf_fighter(self):
        draft = (
            CharacterBuilder()
            .with_name("Thorgar")
            .with_race("Dwarf", subrace="Hill Dwarf")
            .with_class("Fighter")
            .with_background("Soldier")
            .with_alignment("LN")
            .with_level(1)
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .with_starting_weapon("Greataxe")
            .with_starting_armor("Chain mail")
            .build()
        )
        c = draft.character
        assert c.name == "Thorgar"
        assert c.race == "Dwarf"
        assert c.class_ == "Fighter"
        assert c.level == 1
        assert c.alignment == "LN"
        # AC: Chain mail = 16 (no DEX, heavy)
        assert c.armor_class == 16
        # HP: 10 (d10) + 2 (CON 15) + 1 (Hill Dwarf) = 13
        assert c.hp_max == 13
        assert c.hp_current == 13
        # Speed: Dwarf = 25
        assert c.speed == 25
        # Proficiency bonus at L1
        assert c.proficiency_bonus == 2

    def test_invalid_alignment(self):
        with pytest.raises(ValueError, match="alignment"):
            CharacterBuilder().with_alignment("XX")

    def test_invalid_level(self):
        with pytest.raises(ValueError, match="levels 1-5"):
            CharacterBuilder().with_level(10)

    def test_full_wizard_with_spellcasting(self):
        wizard = get_class("Wizard")
        # Standard array — assign INT 15 (highest to primary caster ability)
        abilities = AbilityScores(
            strength=8, dexterity=14, constitution=13,
            intelligence=15, wisdom=12, charisma=10,
        )
        cantrips = [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:3]
        spellbook = [s.name for s in get_spells_for_class("Wizard") if s.level == 1][:6]

        selection = prepare_caster_spells(
            wizard, 1, abilities, 2,
            cantrips=cantrips,
            spellbook=spellbook,
        )
        # INT 15 = +2; DC = 8 + 2 + 2 = 12
        sc = selection.to_spellcasting(wizard, abilities, 2)
        assert sc.save_dc == 12
        assert sc.ability == Ability.INT
        assert sc.spell_slots == {1: 2}

    def test_builder_attaches_spellcasting(self):
        wizard = get_class("Wizard")
        # Use explicit ability scores so INT=15 (highest) -> +2 mod
        abilities = AbilityScores(
            strength=8, dexterity=14, constitution=13,
            intelligence=15, wisdom=12, charisma=10,
        )
        cantrips = [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:3]
        spellbook = [s.name for s in get_spells_for_class("Wizard") if s.level == 1][:6]
        selection = prepare_caster_spells(
            wizard, 1, abilities, 2,
            cantrips=cantrips,
            spellbook=spellbook,
        )

        draft = (
            CharacterBuilder()
            .with_name("Gandalf")
            .with_race("Human")
            .with_class("Wizard")
            .with_ability_scores([8, 14, 13, 15, 12, 10])
            .with_skills(["arcana", "history"])
            .with_spell_selection(selection)
            .build()
        )
        assert draft.character.spellcasting is not None
        assert draft.character.spellcasting.ability == Ability.INT
        # INT 15 -> +2; DC = 8 + 2 + 2 = 12
        assert draft.character.spellcasting.save_dc == 12