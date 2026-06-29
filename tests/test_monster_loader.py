"""Tests for the PHB monster stat-block loader."""
from __future__ import annotations

import pytest
from pathlib import Path

from auto_dm.phb.loader import load_monsters, parse_monster_file
from auto_dm.phb.models import (
    Monster,
    MonsterSize,
    MonsterType,
)
from auto_dm.phb import set_phb_root


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    """Each test starts with a clean monster cache."""
    from auto_dm.phb import get_phb_root as _gpr
    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


SAMPLE_MONSTERS = [
    # name, file_stem, expected size, expected type, expected AC, expected HP, expected CR
    ("Goblin", "Goblin", MonsterSize.SMALL, MonsterType.HUMANOID, 15, 7, 0.25),
    ("Orc", "Orc", MonsterSize.MEDIUM, MonsterType.HUMANOID, 13, 15, 0.5),
    ("Owlbear", "Owlbear", MonsterSize.LARGE, MonsterType.MONSTROSITY, 13, 59, 3.0),
    ("Lich", "Lich", MonsterSize.MEDIUM, MonsterType.UNDEAD, 17, 135, 21.0),
    (
        "Adult Red Dragon (Chromatic)",
        "Adult Red Dragon (Chromatic)",
        MonsterSize.HUGE,
        MonsterType.DRAGON,
        19,
        256,
        17.0,
    ),
]


class TestMonsterLoaderCounts:
    def test_loads_all_318_stat_blocks(self) -> None:
        # 319 .md files total - 1 intro file ("# Monster Statistics.md") = 318
        monsters = load_monsters(Path("data/phb"))
        assert len(monsters) == 318

    def test_skips_intro_file(self) -> None:
        monsters = load_monsters(Path("data/phb"))
        names = {m.name for m in monsters}
        assert "Monster Statistics" not in names

    def test_every_monster_has_name(self) -> None:
        monsters = load_monsters(Path("data/phb"))
        for m in monsters:
            assert m.name, f"Monster with empty name: {m.source_file}"
            assert m.name != "Monster Statistics"


class TestMonsterFields:
    """Spot-check that core stat-block fields are parsed for canonical monsters."""

    @pytest.mark.parametrize(
        "name,stem,size,mtype,ac,hp,cr",
        SAMPLE_MONSTERS,
    )
    def test_basic_stats(
        self,
        name: str,
        stem: str,
        size: MonsterSize,
        mtype: MonsterType,
        ac: int,
        hp: int,
        cr: float,
    ) -> None:
        path = Path("data/phb/Monsters") / f"{stem}.md"
        m = parse_monster_file(path)
        assert m is not None, f"Failed to parse {stem}"
        assert m.name == name
        assert m.size == size
        assert m.type == mtype
        assert m.armor_class == ac
        assert m.hp_average == hp
        assert m.challenge_rating == cr

    def test_goblin_ability_scores(self) -> None:
        path = Path("data/phb/Monsters/Goblin.md")
        m = parse_monster_file(path)
        assert m.abilities.strength == 8
        assert m.abilities.dexterity == 14
        assert m.abilities.constitution == 10
        assert m.abilities.intelligence == 10
        assert m.abilities.wisdom == 8
        assert m.abilities.charisma == 8

    def test_goblin_speed(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert m.speed_walk == 30
        assert m.speed_fly == 0
        assert m.speed_climb == 0
        assert m.hover is False

    def test_red_dragon_multi_speed(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        assert m.speed_walk == 40
        assert m.speed_climb == 40
        assert m.speed_fly == 80
        assert m.speed_burrow == 0
        assert m.speed_swim == 0

    def test_red_dragon_ability_scores(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        assert m.abilities.strength == 27
        assert m.abilities.dexterity == 10
        assert m.abilities.constitution == 25
        assert m.abilities.intelligence == 16
        assert m.abilities.wisdom == 13
        assert m.abilities.charisma == 21

    def test_lich_condition_immunities(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        assert set(m.condition_immunities) == {
            "charmed", "exhaustion", "frightened", "paralyzed", "poisoned",
        }

    def test_lich_damage_immunities_compound(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        # "poison; bludgeoning, piercing, and slashing from nonmagical attacks"
        # gets split by ';' then by ','/'and'.
        assert "poison" in m.damage_immunities
        assert any("bludgeoning" in t for t in m.damage_immunities)
        assert any("piercing" in t for t in m.damage_immunities)
        assert any("slashing" in t for t in m.damage_immunities)

    def test_lich_damage_resistances(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        assert set(m.damage_resistances) == {"cold", "lightning", "necrotic"}

    def test_red_dragon_fire_immunity(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        assert "fire" in m.damage_immunities

    def test_goblin_skills(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert m.skills == {"stealth": 6}

    def test_lich_senses_truesight(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        assert m.senses.get("truesight") == 120
        assert m.passive_perception == 19

    def test_goblin_languages(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert m.languages == ["Common", "Goblin"]
        assert m.languages_note == ""

    def test_lich_languages_with_note(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        assert m.languages == ["Common"]
        assert "plus" in m.languages_note
        assert "five other" in m.languages_note

    def test_challenge_fractional(self) -> None:
        # Goblin: 1/4
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert m.challenge_rating_text == "1/4"
        assert m.challenge_rating == 0.25
        assert m.xp == 50

    def test_challenge_integer(self) -> None:
        # Orc: 1/2
        m = parse_monster_file(Path("data/phb/Monsters/Orc.md"))
        assert m is not None
        assert m.challenge_rating_text == "1/2"
        assert m.challenge_rating == 0.5
        assert m.xp == 100

    def test_challenge_high_cr(self) -> None:
        # Adult Red Dragon: CR 17
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        assert m.challenge_rating_text == "17"
        assert m.challenge_rating == 17.0
        assert m.xp == 18000


class TestMonsterActions:
    """Action parsing: attack type, bonus, damage, range, recharge."""

    def test_goblin_scimitar_melee(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        scimitar = next(a for a in m.actions if a.name == "Scimitar")
        assert scimitar.attack_type == "melee_weapon"
        assert scimitar.attack_bonus == 4
        assert scimitar.damage_dice == "1d6+2"
        assert scimitar.damage_type == "slashing"
        assert scimitar.reach_ft == 5
        assert scimitar.recharge is None
        assert scimitar.usages is None

    def test_goblin_shortbow_ranged(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        shortbow = next(a for a in m.actions if a.name == "Shortbow")
        assert shortbow.attack_type == "ranged_weapon"
        assert shortbow.attack_bonus == 4
        assert shortbow.damage_dice == "1d6+2"
        assert shortbow.damage_type == "piercing"
        assert shortbow.range_normal_ft == 80
        assert shortbow.range_long_ft == 320

    def test_red_dragon_bite_rider_damage(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        bite = next(a for a in m.actions if a.name == "Bite")
        assert bite.attack_type == "melee_weapon"
        assert bite.attack_bonus == 14
        assert bite.damage_dice == "2d10+8"
        assert bite.damage_type == "piercing"
        assert bite.additional_damage_dice == "2d6"
        assert bite.additional_damage_type == "fire"
        assert bite.reach_ft == 10

    def test_red_dragon_fire_breath_recharge(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        breath = next(a for a in m.actions if a.name == "Fire Breath")
        assert breath.attack_type is None  # AoE save, not an attack roll
        assert breath.recharge == "5-6"

    def test_red_dragon_multiattack_has_no_attack_stats(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        multi = next(a for a in m.actions if a.name == "Multiattack")
        assert multi.attack_type is None
        assert multi.damage_dice is None
        assert multi.description  # prose preserved

    def test_lich_paralyzing_touch(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        touch = next(a for a in m.actions if a.name == "Paralyzing Touch")
        assert touch.attack_type == "melee_spell"
        assert touch.attack_bonus == 12
        assert touch.damage_dice == "3d6"
        assert touch.damage_type == "cold"

    def test_red_dragon_has_six_actions(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        action_names = {a.name for a in m.actions}
        assert action_names == {
            "Multiattack", "Bite", "Claw", "Tail", "Frightful Presence", "Fire Breath",
        }


class TestMonsterLegendaryActions:
    """Legendary action parsing: list, count, costs, resistances."""

    def test_red_dragon_legendary_count(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        assert m.legendary_actions_count == 3
        assert m.legendary_resistances == 3

    def test_red_dragon_legendary_options(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        names = {a.name for a in m.legendary_actions}
        assert "Detect" in names
        assert "Tail Attack" in names
        assert any("Wing Attack" in n for n in names)

    def test_red_dragon_wing_attack_cost_2(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Adult Red Dragon (Chromatic).md"))
        assert m is not None
        wing = next(a for a in m.legendary_actions if "Wing Attack" in a.name)
        assert wing.cost == 2

    def test_lich_legendary_paralyzing_touch_cost(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        pt = next(a for a in m.legendary_actions if "Paralyzing Touch" in a.name)
        assert pt.cost == 2

    def test_goblin_has_no_legendary_actions(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert m.legendary_actions == []
        assert m.legendary_actions_count == 0
        assert m.legendary_resistances == 0


class TestMonsterTraits:
    def test_goblin_nimble_escape_trait(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Goblin.md"))
        assert m is not None
        assert any(t.name == "Nimble Escape" for t in m.traits)

    def test_lich_has_legendary_resistance_trait(self) -> None:
        m = parse_monster_file(Path("data/phb/Monsters/Lich.md"))
        assert m is not None
        assert any("Legendary Resistance" in t.name for t in m.traits)


class TestMonsterResilience:
    """Parser must not crash on any of the 318 files."""

    def test_every_monster_parses_to_a_model(self) -> None:
        monsters = load_monsters(Path("data/phb"))
        # Zero parses should have failed
        assert len(monsters) == 318

    def test_every_monster_has_at_least_one_attack_or_action_set(self) -> None:
        # Some monsters (e.g. swarm variants) may not have an Actions section,
        # but most do. We allow empty actions but require no exceptions.
        for m in load_monsters(Path("data/phb")):
            # Just touching attributes shouldn't raise
            _ = m.actions
            _ = m.traits
            _ = m.abilities
            _ = m.speed_walk


class TestMonsterParsingHelpers:
    """Spot-check the internal helpers used by parse_monster_file."""

    def test_speed_walk_only(self) -> None:
        from auto_dm.phb.loader import _parse_speed
        assert _parse_speed("30 ft.") == {"walk": 30, "hover": False}

    def test_speed_walk_climb_fly(self) -> None:
        from auto_dm.phb.loader import _parse_speed
        result = _parse_speed("40 ft., climb 40 ft., fly 80 ft.")
        assert result["walk"] == 40
        assert result["climb"] == 40
        assert result["fly"] == 80
        assert result["hover"] is False

    def test_speed_hover_parsing(self) -> None:
        from auto_dm.phb.loader import _parse_speed
        result = _parse_speed("30 ft., fly 60 ft. (hover)")
        assert result["fly"] == 60
        assert result["hover"] is True

    def test_armor_class_with_description(self) -> None:
        from auto_dm.phb.loader import _parse_armor_class
        assert _parse_armor_class("15 (leather armor, shield)") == (
            15, "(leather armor, shield)",
        )

    def test_armor_class_no_description(self) -> None:
        from auto_dm.phb.loader import _parse_armor_class
        assert _parse_armor_class("14") == (14, "")

    def test_hp_with_dice(self) -> None:
        from auto_dm.phb.loader import _parse_hp
        assert _parse_hp("7 (2d6)") == (7, "2d6")

    def test_hp_with_bonus(self) -> None:
        from auto_dm.phb.loader import _parse_hp
        assert _parse_hp("256 (19d12+133)") == (256, "19d12+133")

    def test_challenge_fraction_text(self) -> None:
        from auto_dm.phb.loader import _parse_challenge_text
        assert _parse_challenge_text("1/4 (50 XP)") == (0.25, "1/4")

    def test_challenge_integer_text(self) -> None:
        from auto_dm.phb.loader import _parse_challenge_text
        assert _parse_challenge_text("17 (18,000 XP)") == (17.0, "17")

    def test_tagline_with_subtype(self) -> None:
        from auto_dm.phb.loader import _parse_tagline
        size, mtype, subtype, alignment = _parse_tagline(
            "*Small humanoid (goblinoid), neutral evil*"
        )
        assert size == MonsterSize.SMALL
        assert mtype == MonsterType.HUMANOID
        assert subtype == "goblinoid"
        assert alignment == "neutral evil"

    def test_tagline_any_alignment(self) -> None:
        from auto_dm.phb.loader import _parse_tagline
        size, mtype, subtype, alignment = _parse_tagline(
            "*Medium undead, any evil alignment*"
        )
        assert size == MonsterSize.MEDIUM
        assert mtype == MonsterType.UNDEAD
        assert subtype is None
        assert alignment == "any evil alignment"