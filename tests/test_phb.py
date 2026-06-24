"""Tests for the PHB loader (parser, models, lookup API)."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_dm.phb import (
    PHBArmorCategory,
    PHBSpellSchool,
    PHBWeaponCategory,
    get_armor,
    get_armor_list,
    get_class,
    get_classes,
    get_condition,
    get_conditions,
    get_phb_root,
    get_race,
    get_races,
    get_spell,
    get_spells,
    get_spells_for_class,
    get_weapon,
    get_weapons,
    set_phb_root,
)
from auto_dm.phb.parser import (
    parse_cost_gp,
    parse_damage,
    parse_fields,
    parse_range,
    parse_table,
    parse_traits,
    parse_weight_lb,
    split_sections,
)


# ============================================================================
# Parser tests
# ============================================================================


class TestSplitSections:
    def test_basic_split(self):
        text = "# Title\n\nbody1\n\n## Subtitle\n\nbody2\n"
        sections = split_sections(text)
        assert len(sections) == 2
        assert sections[0].title == "Title"
        assert sections[0].level == 1
        assert sections[1].title == "Subtitle"
        assert sections[1].level == 2

    def test_flat_list_includes_all_levels(self):
        text = "# A\n\n## B\n\n### C\n\nbody\n## D\n\nbody2\n"
        sections = split_sections(text)
        # Flat list: A(L1), B(L2), C(L3), D(L2)
        assert [s.level for s in sections] == [1, 2, 3, 2]
        assert [s.title for s in sections] == ["A", "B", "C", "D"]

    def test_empty_input(self):
        assert split_sections("") == []


class TestParseTable:
    def test_simple_table(self):
        text = (
            "| Name | Cost |\n"
            "|------|------|\n"
            "| Club | 1 sp |\n"
            "| Dagger | 2 gp |\n"
        )
        tbl = parse_table(text)
        assert tbl is not None
        assert tbl.headers == ["Name", "Cost"]
        assert len(tbl.rows) == 2
        assert tbl.rows[0] == ["Club", "1 sp"]
        assert tbl.rows[1] == ["Dagger", "2 gp"]

    def test_find_row(self):
        text = "| Name | Damage |\n|------|--------|\n| Club | 1d4 |\n"
        tbl = parse_table(text)
        row = tbl.find_row(col="Name", value="Club")
        assert row[1] == "1d4"

    def test_no_table_returns_none(self):
        assert parse_table("just text\nmore text") is None


class TestParseTraits:
    def test_single_trait(self):
        body = "***Darkvision***. Accustomed to life underground, you have superior vision."
        traits = parse_traits(body)
        assert len(traits) == 1
        assert traits[0][0] == "Darkvision"
        assert "Accustomed to life underground" in traits[0][1]

    def test_multiple_traits(self):
        body = (
            "***Darkvision***. You can see in dim light.\n\n"
            "***Dwarven Resilience***. You have advantage on poison saves.\n\n"
            "***Languages***. You can speak Common and Dwarvish.\n"
        )
        traits = parse_traits(body)
        assert len(traits) == 3
        assert [t[0] for t in traits] == [
            "Darkvision",
            "Dwarven Resilience",
            "Languages",
        ]


class TestParseFields:
    def test_simple_fields(self):
        body = (
            "**Armor:** Light armor, medium armor, shields\n"
            "**Weapons:** Simple weapons, martial weapons\n"
        )
        fields = parse_fields(body)
        assert fields["armor"] == "Light armor, medium armor, shields"
        assert fields["weapons"] == "Simple weapons, martial weapons"


class TestParseCostGp:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1 cp", 0.01),
            ("5 cp", 0.05),
            ("1 sp", 0.1),
            ("5 sp", 0.5),
            ("1 gp", 1.0),
            ("5 gp", 5.0),
            ("1,500 gp", 1500.0),
            ("1 pp", 10.0),
            ("-", 0.0),
        ],
    )
    def test_costs(self, text, expected):
        assert parse_cost_gp(text) == pytest.approx(expected)


class TestParseWeight:
    def test_whole_pounds(self):
        assert parse_weight_lb("10 lb.") == 10.0

    def test_fraction(self):
        assert parse_weight_lb("1/4 lb.") == 0.25

    def test_dash_is_zero(self):
        assert parse_weight_lb("-") == 0.0

    def test_no_unit(self):
        assert parse_weight_lb("10") == 10.0


class TestParseDamage:
    def test_dice_damage(self):
        assert parse_damage("1d8 slashing") == ("1d8", "slashing")

    def test_simple_damage(self):
        assert parse_damage("1 piercing") == ("", "piercing")

    def test_multi_dice(self):
        assert parse_damage("2d6 bludgeoning") == ("2d6", "bludgeoning")


class TestParseRange:
    def test_inline(self):
        assert parse_range("Thrown (range 20/60)") == (20, 60)

    def test_no_range(self):
        assert parse_range("Light, finesse") == (None, None)


# ============================================================================
# Lookup API tests — counts
# ============================================================================


class TestRaces:
    def test_at_least_nine_races(self):
        races = get_races()
        assert len(races) >= 9  # PHB has 9 races

    def test_dwarf_basics(self):
        dwarf = get_race("Dwarf")
        assert dwarf is not None
        assert dwarf.speed == 25
        assert dwarf.size == "Medium"
        assert "Dwarvish" in dwarf.languages
        assert "Common" in dwarf.languages
        # Has Dwarven Resilience trait
        assert any(t.name == "Dwarven Resilience" for t in dwarf.traits)

    def test_dwarf_has_hill_subrace(self):
        dwarf = get_race("Dwarf")
        assert dwarf is not None
        sub_names = [s.name for s in dwarf.subraces]
        assert "Hill Dwarf" in sub_names

    def test_subrace_lists_match_phb(self):
        # Regression: PHB race files used to ship with only the first
        # subrace, hiding Mountain Dwarf / Wood Elf / Dark Elf / Stout /
        # Forest Gnome from the character creation wizard.
        from auto_dm.state.models import Ability

        cases = {
            "Dwarf": {
                "Hill Dwarf": {Ability.WIS: 1},
                "Mountain Dwarf": {Ability.STR: 2},
            },
            "Elf": {
                "High Elf": {Ability.INT: 1},
                "Wood Elf": {Ability.WIS: 1},
                "Dark Elf": {Ability.CHA: 1},
            },
            "Halfling": {
                "Lightfoot": {Ability.CHA: 1},
                "Stout": {Ability.CON: 1},
            },
            "Gnome": {
                "Rock Gnome": {Ability.CON: 1},
                "Forest Gnome": {Ability.DEX: 1},
            },
        }
        for race_name, expected in cases.items():
            race = get_race(race_name)
            assert race is not None, f"{race_name} not found"
            got = {s.name: {b.ability: b.bonus for b in s.ability_bonuses}
                   for s in race.subraces}
            assert got == expected, f"{race_name}: {got} != {expected}"

    def test_gnome_is_small(self):
        gnome = get_race("Gnome")
        assert gnome is not None
        assert gnome.size == "Small"
        assert gnome.speed == 25

    def test_lookup_case_insensitive(self):
        assert get_race("dwarf") is not None
        assert get_race("DWARF") is not None
        assert get_race("Dwarf") is not None

    def test_unknown_race_returns_none(self):
        assert get_race("Foobar") is None


class TestClasses:
    def test_twelve_classes(self):
        classes = get_classes()
        assert len(classes) == 12

    @pytest.mark.parametrize(
        "name,hd",
        [
            ("Barbarian", "1d12"),
            ("Fighter", "1d10"),
            ("Wizard", "1d6"),
            ("Sorcerer", "1d6"),
            ("Rogue", "1d8"),
            ("Cleric", "1d8"),
        ],
    )
    def test_hit_dice(self, name, hd):
        cls = get_class(name)
        assert cls is not None
        assert cls.hit_dice == hd

    def test_barbarian_has_rage(self):
        barb = get_class("Barbarian")
        assert barb is not None
        feature_names = [f.name for f in barb.features]
        assert "Rage" in feature_names

    def test_barbarian_subclass(self):
        barb = get_class("Barbarian")
        assert barb is not None
        sub_names = [s.name for s in barb.subclasses]
        assert "Path of the Berserker" in sub_names

    def test_spellcasting_classes_have_ability(self):
        wizard = get_class("Wizard")
        assert wizard.spellcasting is not None
        assert wizard.spellcasting.ability.value == "intelligence"

        cleric = get_class("Cleric")
        assert cleric.spellcasting.ability.value == "wisdom"

        bard = get_class("Bard")
        assert bard.spellcasting.ability.value == "charisma"

    def test_non_caster_has_no_spellcasting(self):
        fighter = get_class("Fighter")
        assert fighter.spellcasting is None

    def test_proficiency_saving_throws(self):
        fighter = get_class("Fighter")
        assert fighter is not None
        saves = fighter.proficiencies.saving_throws
        # Fighter gets STR and CON
        from auto_dm.state.models import Ability
        assert Ability.STR in saves
        assert Ability.CON in saves


class TestSpells:
    def test_hundreds_of_spells(self):
        spells = get_spells()
        # PHB has ~300 spells
        assert len(spells) >= 250

    def test_fireball_basics(self):
        fb = get_spell("Fireball")
        assert fb is not None
        assert fb.level == 3
        assert fb.school == PHBSpellSchool.EVOCATION
        assert "8d6" in fb.description

    def test_cantrips_level_zero(self):
        cantrips = [s for s in get_spells() if s.level == 0]
        assert len(cantrips) >= 10
        assert all(s.is_cantrip for s in cantrips)

    def test_components_parsed(self):
        fb = get_spell("Fireball")
        assert fb is not None
        from auto_dm.phb.models import PHBSpellComponent
        assert PHBSpellComponent.VERBAL in fb.components
        assert PHBSpellComponent.SOMATIC in fb.components
        assert PHBSpellComponent.MATERIAL in fb.components
        assert fb.material is not None
        assert "bat guano" in fb.material

    def test_spell_class_lists(self):
        # Fireball should be on Sorcerer and Wizard lists
        fb = get_spell("Fireball")
        assert fb is not None
        assert "Sorcerer" in fb.classes
        assert "Wizard" in fb.classes

    def test_get_spells_for_class(self):
        wizard_spells = get_spells_for_class("Wizard")
        assert len(wizard_spells) >= 50
        # All wizard spells should include Wizard in their classes
        for s in wizard_spells[:5]:
            assert "Wizard" in s.classes

    def test_concentration_detected(self):
        # Bless is "Concentration, up to 1 minute"
        bless = get_spell("Bless")
        assert bless is not None
        assert bless.is_concentration

    def test_lookup_case_insensitive(self):
        assert get_spell("fireball") is not None
        assert get_spell("FIREBALL") is not None


class TestWeapons:
    def test_count(self):
        weapons = get_weapons()
        # PHB has ~35 weapons
        assert len(weapons) >= 30

    def test_longsword(self):
        ls = get_weapon("Longsword")
        assert ls is not None
        assert ls.damage_dice == "1d8"
        assert ls.damage_type == "slashing"
        assert ls.cost_gp == pytest.approx(15.0)
        assert ls.versatile_dice == "1d10"
        assert ls.category == PHBWeaponCategory.MARTIAL_MELEE

    def test_dagger_finesse(self):
        dagger = get_weapon("Dagger")
        assert dagger is not None
        assert dagger.finesse is True
        assert dagger.light is True
        assert dagger.thrown is True
        assert dagger.range_normal == 20
        assert dagger.range_long == 60
        assert dagger.damage_dice == "1d4"
        assert dagger.damage_type == "piercing"

    def test_greatsword_two_handed(self):
        gs = get_weapon("Greatsword")
        assert gs is not None
        assert gs.two_handed is True
        assert gs.heavy is True
        assert gs.damage_dice == "2d6"

    def test_longbow_heavy_and_ammunition(self):
        bow = get_weapon("Longbow")
        assert bow is not None
        assert bow.heavy is True
        assert bow.ammunition is True
        assert bow.two_handed is True
        assert bow.range_normal == 150
        assert bow.range_long == 600


class TestArmor:
    def test_count(self):
        armors = get_armor_list()
        assert len(armors) == 13  # 3 light + 5 medium + 4 heavy + 1 shield

    def test_plate_is_heavy(self):
        plate = get_armor("Plate")
        assert plate is not None
        assert plate.category == PHBArmorCategory.HEAVY
        assert plate.base_ac == 18
        assert plate.strength_required == 15
        assert plate.stealth_disadvantage is True
        assert plate.add_dex is False  # Heavy doesn't add dex

    def test_studded_leather_is_light(self):
        studded = get_armor("Studded Leather") or get_armor("Studded leather")
        assert studded is not None
        assert studded.category == PHBArmorCategory.LIGHT
        assert studded.base_ac == 12
        assert studded.add_dex is True
        assert studded.max_dex is None  # Light has no max

    def test_half_plate_medium_max_dex(self):
        hp = get_armor("Half plate")
        assert hp is not None
        assert hp.category == PHBArmorCategory.MEDIUM
        assert hp.max_dex == 2
        assert hp.stealth_disadvantage is True

    def test_shield_is_shield(self):
        shield = get_armor("Shield")
        assert shield is not None
        assert shield.category == PHBArmorCategory.SHIELD
        assert shield.is_shield is True
        assert shield.base_ac == 2  # +2

    def test_no_duplicate_armor(self):
        names = [a.name for a in get_armor_list()]
        assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"


class TestConditions:
    def test_count(self):
        # PHB has 14 base + Exhaustion = 15
        conds = get_conditions()
        assert len(conds) == 15

    def test_blinded_present(self):
        blinded = get_condition("Blinded")
        assert blinded is not None
        assert len(blinded.effects) >= 1
        # Blinded makes attack rolls against the creature have advantage
        assert any("advantage" in e.lower() for e in blinded.effects)

    def test_unconscious_present(self):
        uc = get_condition("Unconscious")
        assert uc is not None

    def test_exhaustion_has_levels(self):
        exh = get_condition("Exhaustion")
        assert exh is not None
        assert exh.has_levels is True
        # 6 levels of effects
        assert len(exh.effects) >= 6

    def test_lookup_case_insensitive(self):
        assert get_condition("blinded") is not None
        assert get_condition("BLINDED") is not None


# ============================================================================
# Caching and root switching
# ============================================================================


class TestCaching:
    def test_get_races_cached(self):
        # Calling twice returns the same list object
        a = get_races()
        b = get_races()
        assert a is b

    def test_set_phb_root_clears_cache(self, tmp_path: Path):
        # Create a minimal stub PHB tree
        races_dir = tmp_path / "Races"
        races_dir.mkdir()
        (races_dir / "Stub.md").write_text(
            "# Stub\n\n"
            "### Stub Traits\n\n"
            "***Speed***. Your base walking speed is 30 feet.\n\n"
            "***Languages***. You can speak Common.\n",
            encoding="utf-8",
        )

        original_root = get_phb_root()
        try:
            set_phb_root(tmp_path)
            races = get_races()
            assert len(races) == 1
            assert races[0].name == "Stub"
            # Cache cleared -> second call also hits the loader but returns same obj
            assert get_races() is not None
        finally:
            set_phb_root(original_root)

    def test_full_loader_after_switch_back(self):
        # After switching back, full data is available again
        races = get_races()
        assert len(races) >= 9


# ============================================================================
# Integration: spell on character creation
# ============================================================================


class TestIntegration:
    def test_wizard_can_learn_fireball(self):
        wizard = get_class("Wizard")
        assert wizard is not None
        wizard_spell_names = [s.name for s in get_spells_for_class("Wizard")]
        assert "Fireball" in wizard_spell_names
        assert "Magic Missile" in wizard_spell_names

    def test_ranger_subclass_features(self):
        ranger = get_class("Ranger")
        assert ranger is not None
        hunter = next((s for s in ranger.subclasses if s.name == "Hunter"), None)
        assert hunter is not None
        assert len(hunter.features) > 0

    def test_dwarf_hill_subrace_asi(self):
        dwarf = get_race("Dwarf")
        assert dwarf is not None
        hill = next((s for s in dwarf.subraces if s.name == "Hill Dwarf"), None)
        assert hill is not None
        from auto_dm.state.models import Ability
        bonuses = {b.ability: b.bonus for b in hill.ability_bonuses}
        # Hill Dwarf gets +1 WIS
        assert bonuses.get(Ability.WIS) == 1