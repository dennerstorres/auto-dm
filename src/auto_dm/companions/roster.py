"""Pre-defined AI companion roster.

Each companion is a factory function returning a fully-built
``Character`` (via :class:`CharacterBuilder`). They have distinct
personality profiles, classes, and roles so the party feels alive.

Companions are intentionally L1 and use the standard array. The
PHB allows optimizing for class, but the standard array keeps the
roster balanced and the party roughly equivalent.

Factory functions (not module-level constants) so tests and saves
can instantiate fresh copies without sharing state.
"""
from __future__ import annotations

from typing import Callable

from auto_dm.character import CharacterBuilder
from auto_dm.state.models import Character


# ============================================================================
# Thorgrim — Hill Dwarf Fighter (Tank / Frontline)
# ============================================================================


def make_thorgrim() -> Character:
    """A stoic, loyal hill dwarf fighter. Speaks little; stands firm.

    Background: Soldier. Tank role: takes hits, protects the party.
    """
    draft = (
        CharacterBuilder()
        .with_name("Thorgrim")
        .with_race("Dwarf", subrace="Hill Dwarf")
        .with_class("Fighter")
        .with_background("Soldier")
        .with_alignment("LN")
        .with_level(1)
        .with_standard_array()
        # Standard array: 15, 14, 13, 12, 10, 8.
        # Fighter wants STR/CON/CON. Allocation:
        #   STR 15, CON 14 (with Hill Dwarf +1 -> 15), DEX 13, WIS 12, INT 10, CHA 8.
        .with_ability_scores([15, 13, 14, 10, 12, 8])
        .with_skills(["athletics", "perception"])
        .with_starting_weapon("Warhammer")
        .with_starting_armor("Chain Mail")
        .with_shield(True)
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Falo pouco. Quando falo, escolho as palavras como escolho batalhas.",
        "Nunca deixo um companheiro para trás — nem em batalha, nem em conversa.",
    ]
    character.ideals = [
        "Disciplina. A muralha só resiste se cada pedra fizer sua parte.",
    ]
    character.bonds = [
        "O regimento que me criou ainda vive na minha memória — honro seus mortos.",
    ]
    character.flaws = [
        "Desconfio de elfos. Demoro a aquecer com estranhos.",
    ]
    return character


# ============================================================================
# Lyra — Wood Elf Ranger (Scout / Ranged DPS)
# ============================================================================


def make_lyra() -> Character:
    """A curious high elf ranger. Quick to laugh, quicker to act.

    Background: Outlander. Scout role: exploration, ranged damage,
    tracking, survival.
    """
    draft = (
        CharacterBuilder()
        .with_name("Lyra")
        .with_race("Elf", subrace="High Elf")
        .with_class("Ranger")
        .with_background("Outlander")
        .with_alignment("CG")
        .with_level(1)
        .with_standard_array()
        # Ranger wants DEX/WIS. Allocation:
        #   DEX 15, WIS 14, CON 13, INT 12, STR 10, CHA 8.
        .with_ability_scores([10, 15, 13, 12, 14, 8])
        .with_skills(["survival", "perception", "stealth"])
        .with_starting_weapon("Longbow")
        .with_starting_armor("Leather")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Caminho horas sem sentir fome, mas esqueço onde deixei a minha faca.",
        "Faço piadas em momentos sérios para aliviar o clima — ou para esconder o medo.",
    ]
    character.ideals = [
        "Liberdade. Nenhuma cadeia, de ferro ou de tradição, deve prender uma alma.",
    ]
    character.bonds = [
        "A floresta onde cresci me chamou de filha. Açoito qualquer um que a machuque.",
    ]
    character.flaws = [
        "Sou curiosa demais. Às vezes a curiosidade é o último suspiro da pessoa.",
    ]
    return character


# ============================================================================
# Mira — Lightfoot Halfling Cleric (Support / Healer)
# ============================================================================


def make_mira() -> Character:
    """A warm halfling cleric of light. Believes small things matter.

    Background: Acolyte. Support role: healing, blessings, morale.
    """
    from auto_dm.character import (
        prepare_caster_spells,
        select_cantrips,
    )
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Cleric L1 (WIS 15 → +2): 3 cantrips known, 1+WIS_mod = 3 prepared.
    cantrips = select_cantrips(
        char_class=get_class("Cleric"),
        level=1,
        picks=["Sacred Flame", "Light"],
    )
    abilities = AbilityScores(
        strength=12, dexterity=13, constitution=14,
        intelligence=10, wisdom=15, charisma=8,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Cleric"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spells_prepared=["Cure Wounds", "Bless", "Healing Word"],
    )

    draft = (
        CharacterBuilder()
        .with_name("Mira")
        .with_race("Halfling", subrace="Lightfoot")
        .with_class("Cleric")
        .with_background("Acolyte")
        .with_alignment("LG")
        .with_level(1)
        # Cleric wants WIS/CON. Allocation:
        #   WIS 15, CON 14, DEX 13, STR 12, INT 10, CHA 8.
        .with_ability_scores([12, 13, 14, 10, 15, 8])
        .with_skills(["medicine", "insight"])
        .with_starting_weapon("Mace")
        .with_starting_armor("Chain Shirt")
        .with_shield(True)
        .with_spell_selection(selection)
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Canto baixinho enquanto caminho. Ajuda a manter a coragem.",
        "Ofereço comida a estranhos antes de perguntar o nome deles.",
    ]
    character.ideals = [
        "Esperança. A luz mais fraca é o bastante para guiar quem está perdido.",
    ]
    character.bonds = [
        "O templo onde fui ordenada é o único lar que conheço. Levo-o comigo.",
    ]
    character.flaws = [
        "Perdoo rápido demais. Algumas pessoas merecem ser postas na rua.",
    ]
    return character


# ============================================================================
# Vex — Tiefling Rogue (Striker / Skill Monkey)
# ============================================================================


def make_vex() -> Character:
    """A charming tiefling rogue. Talks his way out (or into) trouble.

    Background: Charlatan. Striker role: damage, traps, social.
    """
    draft = (
        CharacterBuilder()
        .with_name("Vex")
        .with_race("Tiefling")
        .with_class("Rogue")
        .with_background("Charlatan")
        .with_alignment("CN")
        .with_level(1)
        .with_standard_array()
        # Rogue wants DEX/INT/CHA. Allocation:
        #   DEX 15, INT 14, CHA 13, CON 12, WIS 10, STR 8.
        .with_ability_scores([8, 15, 12, 14, 10, 13])
        .with_skills(["deception", "sleight of hand", "stealth"])
        .with_starting_weapon("Rapier")
        .with_starting_armor("Leather")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Faço o meu melhor trabalho quando ninguém está olhando — ou quando todos estão.",
        "Invento apelidos para os meus inimigos. Ajuda a despersonalizar o combate.",
    ]
    character.ideals = [
        "Astúcia. Força bruta resolve um duelo; esperteza resolve o resto.",
    ]
    character.bonds = [
        "Alguém, em algum lugar, sabe o meu nome verdadeiro. Vou encontrá-lo antes que ele me encontre.",
    ]
    character.flaws = [
        "Não consigo resistir a uma aposta. Especialmente quando estou perdendo.",
    ]
    return character


# ============================================================================
# Registry
# ============================================================================


COMPANION_FACTORIES: dict[str, Callable[[], Character]] = {
    "thorgrim": make_thorgrim,
    "lyra": make_lyra,
    "mira": make_mira,
    "vex": make_vex,
}


def list_companion_keys() -> list[str]:
    """Return the keys of all available pre-defined companions."""
    return list(COMPANION_FACTORIES.keys())


def build_companion(key: str) -> Character:
    """Build a fresh copy of a named companion.

    Raises KeyError if the key isn't in the registry.
    """
    if key not in COMPANION_FACTORIES:
        raise KeyError(
            f"Unknown companion {key!r}. Available: {list_companion_keys()}"
        )
    return COMPANION_FACTORIES[key]()
