from __future__ import annotations

import random

from auto_dm.engine.checks import check_modifier, resolve_check, roll_character_check
from auto_dm.state import Ability, AbilityScores, Character, Proficiencies, Skill


def make_player() -> Character:
    return Character(
        id="pc",
        name="Nara",
        race="Human",
        **{"class": "Rogue"},
        level=1,
        background="Criminal",
        alignment="CN",
        abilities=AbilityScores(
            strength=8,
            dexterity=16,
            constitution=12,
            intelligence=14,
            wisdom=10,
            charisma=13,
        ),
        hp_current=9,
        hp_max=9,
        armor_class=14,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d8",
        hit_dice_remaining=1,
        proficiencies=Proficiencies(
            saves=[Ability.DEX, Ability.INT],
            skills=[Skill.STEALTH, Skill.PERCEPTION],
        ),
        is_player=True,
    )


def test_resolve_portuguese_skill_alias():
    spec = resolve_check("teste de Furtividade")

    assert spec.kind == "skill"
    assert spec.key == "stealth"
    assert spec.ability is Ability.DEX


def test_skill_modifier_adds_proficiency():
    character = make_player()
    spec = resolve_check("percepcao")

    total, proficient, ability_mod, prof_bonus = check_modifier(character, spec)

    assert total == 2
    assert proficient is True
    assert ability_mod == 0
    assert prof_bonus == 2


def test_ability_check_does_not_add_proficiency():
    character = make_player()
    spec = resolve_check("Destreza")

    total, proficient, ability_mod, prof_bonus = check_modifier(character, spec)

    assert total == 3
    assert proficient is False
    assert ability_mod == 3
    assert prof_bonus == 0


def test_saving_throw_adds_save_proficiency():
    character = make_player()
    spec = resolve_check("salvaguarda de Destreza")

    total, proficient, ability_mod, prof_bonus = check_modifier(character, spec)

    assert total == 5
    assert proficient is True
    assert ability_mod == 3
    assert prof_bonus == 2


def test_roll_character_check_returns_breakdown():
    result = roll_character_check(make_player(), "furtividade", rng=random.Random(42))

    assert result.spec.kind == "skill"
    assert result.modifier == 5
    assert result.roll.total == result.roll.kept[0] + 5
    assert result.proficient is True
