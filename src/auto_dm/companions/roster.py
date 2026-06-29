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
# Garrick — Human Paladin (Tank / Support)
# ============================================================================


def make_garrick() -> Character:
    """A steadfast human paladin. Holds the line; protects the faithful.

    Background: Noble. Tank/support hybrid: takes hits, smites evil.
    """
    draft = (
        CharacterBuilder()
        .with_name("Garrick")
        .with_race("Human")
        .with_class("Paladin", subclass="Devotion")
        .with_background("Noble")
        .with_alignment("LG")
        .with_level(1)
        .with_standard_array()
        # Paladin wants STR/CHA/CON. Allocation pre-Human (+1 all):
        #   STR 15, CHA 13, CON 14, WIS 12, DEX 10, INT 8.
        # After Human +1 all: STR 16, CHA 14, CON 15, WIS 13, DEX 11, INT 9.
        .with_ability_scores([15, 10, 14, 8, 12, 13])
        .with_skills(["athletics", "insight"])
        .with_starting_weapon("Longsword")
        .with_starting_armor("Chain Mail")
        .with_shield(True)
        .build()
    )
    character = draft.character
    # Paladin L1 has NO spell slots (half caster) — spellcasting stays None.
    character.personality_traits = [
        "Mantenho a espada limpa e a consciência limpa — a ordem das duas coisas importa.",
        "Cumprimento o juramento primeiro à letra, depois ao espírito.",
    ]
    character.ideals = [
        "Justiça. O mal não espera a corte; a corte precisa alcançá-lo.",
    ]
    character.bonds = [
        "O brasão da minha família foi entregue ao fogo. Carrego-o na lâmina.",
    ]
    character.flaws = [
        "Acredito demais na redenção — às vezes ela não vem.",
    ]
    return character


# ============================================================================
# Brom — Half-Orc Barbarian (Berserker / Bruiser)
# ============================================================================


def make_brom() -> Character:
    """A gruff half-orc barbarian. Carries grudges and a big axe.

    Background: Outlander. Tank-melee: durable, high burst damage.
    """
    draft = (
        CharacterBuilder()
        .with_name("Brom")
        .with_race("Half-Orc")
        .with_class("Barbarian", subclass="Berserker")
        .with_background("Outlander")
        .with_alignment("CN")
        .with_level(1)
        .with_standard_array()
        # Barbarian wants STR/CON/DEX. Allocation pre-Half-Orc (+2 STR, +1 CON):
        #   STR 13 (→15), CON 13 (→14), DEX 13, WIS 12, INT 10, CHA 8.
        .with_ability_scores([13, 13, 13, 10, 12, 8])
        .with_skills(["athletics", "intimidation"])
        .with_starting_weapon("Greataxe")
        .with_starting_armor("Hide Armor")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Resmungo antes do café-da-manhã. Depois do café, resmungo mais alto.",
        "Guardo troféus dos inimigos que venci — dentes, armas, mechas de cabelo.",
    ]
    character.ideals = [
        "Força. O mundo respeita quem aguenta mais um golpe que os outros.",
    ]
    character.bonds = [
        "A tribo que me expulsou não me quis. Vou provar que estavam errados.",
    ]
    character.flaws = [
        "Entro em fúria antes de pensar. Às vezes a fúria está certa; às vezes, não.",
    ]
    return character


# ============================================================================
# Kael — Wood Elf Wizard (Evoker / Controller)
# ============================================================================


def make_kael() -> Character:
    """A patient wood elf wizard. Calculates before he casts.

    Background: Sage. Controller: spells, knowledge, tactics.
    """
    from auto_dm.character import prepare_caster_spells, select_cantrips
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Wizard L1 (INT 15 → +2): 3 cantrips, spellbook size 6, prepared = INT mod + 1 = 3.
    cantrips = select_cantrips(
        char_class=get_class("Wizard"),
        level=1,
        picks=["Fire Bolt", "Mage Hand", "Minor Illusion"],
    )
    abilities = AbilityScores(
        strength=8, dexterity=14, constitution=13,
        intelligence=15, wisdom=12, charisma=10,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Wizard"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spellbook=[
            "Magic Missile", "Shield", "Sleep",
            "Detect Magic", "Burning Hands", "Mage Armor",
        ],
        spells_prepared=["Magic Missile", "Sleep", "Mage Armor"],
    )

    draft = (
        CharacterBuilder()
        .with_name("Kael")
        .with_race("Elf", subrace="Wood Elf")
        .with_class("Wizard", subclass="Evocation")
        .with_background("Sage")
        .with_alignment("LN")
        .with_level(1)
        # Wizard wants INT/DEX/CON. Allocation pre-Wood Elf (+2 DEX, +1 WIS):
        #   INT 15, DEX 12 (→14), CON 13, WIS 11 (→12), STR 8, CHA 10.
        .with_ability_scores([8, 12, 13, 15, 11, 10])
        .with_skills(["arcana", "history"])
        .with_starting_weapon("Quarterstaff")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Falo devagar e escolho cada palavra como escolho cada componente.",
        "Anoto tudo. Papel é mais leal que gente.",
    ]
    character.ideals = [
        "Conhecimento. O que não se entende, se teme; o que se teme, se destrói.",
    ]
    character.bonds = [
        "Minha biblioteca era um casebre. Cada livro que carrego é uma parede reconstruída.",
    ]
    character.flaws = [
        "Confio mais em fórmulas do que em pessoas. As pessoas falham; as fórmulas, não.",
    ]
    character.spellcasting = selection.to_spellcasting(
        get_class("Wizard"), abilities, 2
    )
    return character


# ============================================================================
# Sage — Half-Elf Sorcerer Draconic (Burst caster)
# ============================================================================


def make_sage() -> Character:
    """A half-elf sorcerer with bronze dragon blood. Sparks at the fingertips.

    Background: Hermit. Striker/caster: burst damage, scaling magic.
    """
    from auto_dm.character import prepare_caster_spells, select_cantrips
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Sorcerer L1 (CHA 15 → +2): 4 cantrips, 2 spells known.
    cantrips = select_cantrips(
        char_class=get_class("Sorcerer"),
        level=1,
        picks=["Fire Bolt", "Mage Hand", "Minor Illusion", "Shocking Grasp"],
    )
    abilities = AbilityScores(
        strength=8, dexterity=13, constitution=14,
        intelligence=10, wisdom=12, charisma=15,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Sorcerer"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spells_known=["Magic Missile", "Shield"],
    )

    draft = (
        CharacterBuilder()
        .with_name("Sage")
        .with_race("Half-Elf")
        .with_class("Sorcerer", subclass="Draconic Bloodline")
        .with_background("Hermit")
        .with_alignment("CG")
        .with_level(1)
        # Sorcerer wants CHA/CON/DEX. Allocation pre-Half-Elf (+2 CHA, +1 two skills):
        #   CHA 13 (→15), CON 13 (→14), DEX 12 (→13), WIS 12, INT 10, STR 8.
        .with_ability_scores([8, 12, 13, 10, 12, 13])
        .with_skills(["arcana", "persuasion"])
        .with_starting_weapon("Light Crossbow")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Falo com o vento e ele me responde — quase sempre.",
        "Quando estou nervoso, solto faísca pelo nariz. Aprendi a controlar a maior parte.",
    ]
    character.ideals = [
        "Paixão. O fogo queima, mas também aquece; o que decide é a mão.",
    ]
    character.bonds = [
        "O dragão de bronze que me marcou desapareceu. Procuro a trilha dele nos mapas.",
    ]
    character.flaws = [
        "Confio no instinto mais do que no plano. O instinto erra bonito.",
    ]
    character.spellcasting = selection.to_spellcasting(
        get_class("Sorcerer"), abilities, 2
    )
    return character


# ============================================================================
# Maren — Human Monk (Open Hand / Skirmisher)
# ============================================================================


def make_maren() -> Character:
    """A human monk in flowing robes. Breathes four times before striking.

    Background: Hermit. Skirmisher: mobile, durable, ki-fueled strikes.
    """
    draft = (
        CharacterBuilder()
        .with_name("Maren")
        .with_race("Human")
        .with_class("Monk", subclass="Open Hand")
        .with_background("Hermit")
        .with_alignment("LN")
        .with_level(1)
        .with_standard_array()
        # Monk wants DEX/WIS/CON. Allocation pre-Human (+1 all):
        #   DEX 14 (→15), WIS 13 (→14), CON 12 (→13), STR 11 (→12), INT 9 (→10), CHA 7 (→8).
        .with_ability_scores([11, 14, 12, 9, 13, 7])
        .with_skills(["acrobatics", "insight"])
        .with_starting_weapon("Shortsword")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Respiro três vezes antes de responder. Às vezes respondo na quarta.",
        "Como devagar. Mastigo quarenta vezes. A comida merece.",
    ]
    character.ideals = [
        "Equilíbrio. O centro firme é o que sustenta o golpe.",
    ]
    character.bonds = [
        "O mestre que me ensinou morreu com um sorriso no rosto. Quero morrer igual.",
    ]
    character.flaws = [
        "Recuso ajuda até não poder mais. Às vezes não posso mais cedo do que deveria.",
    ]
    return character


# ============================================================================
# Eldra — Forest Gnome Druid (Land / Healer-support)
# ============================================================================


def make_eldra() -> Character:
    """A forest gnome druid who talks to roots. Speaks slowly to everything.

    Background: Hermit. Healer/support: ritual magic, wildshape (L2+).
    """
    from auto_dm.character import prepare_caster_spells, select_cantrips
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Druid L1 (WIS 15 → +2): 2 cantrips, prepared = WIS mod + 1 = 3.
    cantrips = select_cantrips(
        char_class=get_class("Druid"),
        level=1,
        picks=["Druidcraft", "Produce Flame"],
    )
    abilities = AbilityScores(
        strength=10, dexterity=13, constitution=14,
        intelligence=12, wisdom=15, charisma=8,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Druid"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spells_prepared=["Cure Wounds", "Entangle", "Faerie Fire"],
    )

    draft = (
        CharacterBuilder()
        .with_name("Eldra")
        .with_race("Gnome", subrace="Forest Gnome")
        .with_class("Druid", subclass="Land")
        .with_background("Hermit")
        .with_alignment("N")
        .with_level(1)
        # Druid wants WIS/CON/DEX. Allocation pre-Forest Gnome (+2 INT, +1 DEX):
        #   WIS 15, CON 14, DEX 12 (→13), INT 10 (→12), STR 10, CHA 8.
        .with_ability_scores([10, 12, 14, 10, 15, 8])
        .with_skills(["nature", "medicine"])
        .with_starting_weapon("Scimitar")
        .with_starting_armor("Leather Armor")
        .with_shield(True)
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Converso com os galhos antes de pedir sombra.",
        "Tenho nome para cada cogumelo da trilha. Alguns respondem.",
    ]
    character.ideals = [
        "Ciclo. Tudo cresce, tudo apodrece, tudo volta.",
    ]
    character.bonds = [
        "A clareira onde fui iniciada virou cinzas. Planto uma semente lá todo ano.",
    ]
    character.flaws = [
        "Recuso cortar árvore mesmo quando a árvore recusa cair em cima de mim.",
    ]
    character.spellcasting = selection.to_spellcasting(
        get_class("Druid"), abilities, 2
    )
    return character


# ============================================================================
# Tobias — Dragonborn Bard (Lore / Skill monkey)
# ============================================================================


def make_tobias() -> Character:
    """A red dragonborn bard with a flute and a flair for the dramatic.

    Background: Entertainer. Support/skill monkey: songs, faces, fire.
    """
    from auto_dm.character import prepare_caster_spells, select_cantrips
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Bard L1 (CHA 15 → +2): 2 cantrips, 4 spells known.
    cantrips = select_cantrips(
        char_class=get_class("Bard"),
        level=1,
        picks=["Vicious Mockery", "Minor Illusion"],
    )
    abilities = AbilityScores(
        strength=12, dexterity=14, constitution=13,
        intelligence=8, wisdom=10, charisma=15,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Bard"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spells_known=[
            "Cure Wounds", "Faerie Fire", "Healing Word", "Hideous Laughter",
        ],
    )

    draft = (
        CharacterBuilder()
        .with_name("Tobias")
        .with_race("Dragonborn")
        .with_class("Bard", subclass="Lore")
        .with_background("Entertainer")
        .with_alignment("CG")
        .with_level(1)
        # Bard wants CHA/DEX/CON. Allocation pre-Dragonborn (+2 STR, +1 CHA):
        #   CHA 14 (→15), DEX 14, CON 13, STR 10 (→12), WIS 10, INT 8.
        .with_ability_scores([10, 14, 13, 8, 10, 14])
        .with_skills(["performance", "persuasion"])
        .with_starting_weapon("Rapier")
        .with_starting_armor("Leather Armor")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Canto alto demais em tavernas e em funerais.",
        "Invento rima para qualquer nome. A maioria não merece.",
    ]
    character.ideals = [
        "Arte. A história bem contada vale mais que a verdade mal dita.",
    ]
    character.bonds = [
        "A flauta do meu pai tem um entalhe. Eu o fiz quando era criança.",
    ]
    character.flaws = [
        "Gosto demais de plateia. Às vezes a plateia é o inimigo.",
    ]
    character.spellcasting = selection.to_spellcasting(
        get_class("Bard"), abilities, 2
    )
    return character


# ============================================================================
# Dax — Stout Halfling Warlock (Fiend / Striker-ranged)
# ============================================================================


def make_dax() -> Character:
    """A stout halfling warlock with a devil's bargain and a sharp smile.

    Background: Criminal. Striker/ranged: eldritch blast, hex, fiend buffs.
    """
    from auto_dm.character import prepare_caster_spells, select_cantrips
    from auto_dm.phb import get_class
    from auto_dm.state.models import AbilityScores

    # Warlock L1 (CHA 15 → +2): 2 cantrips, 2 spells known, 1 pact slot @ L1.
    cantrips = select_cantrips(
        char_class=get_class("Warlock"),
        level=1,
        picks=["Eldritch Blast", "Minor Illusion"],
    )
    abilities = AbilityScores(
        strength=8, dexterity=14, constitution=13,
        intelligence=12, wisdom=10, charisma=15,
    )
    selection = prepare_caster_spells(
        char_class=get_class("Warlock"),
        level=1,
        abilities=abilities,
        proficiency_bonus=2,
        cantrips=cantrips,
        spells_known=["Hellish Rebuke", "Charm Person"],
    )

    draft = (
        CharacterBuilder()
        .with_name("Dax")
        .with_race("Halfling", subrace="Stout")
        .with_class("Warlock", subclass="Fiend")
        .with_background("Criminal")
        .with_alignment("CN")
        .with_level(1)
        # Warlock wants CHA/DEX/CON. Allocation pre-Stout Halfling (+2 DEX, +1 CON):
        #   CHA 15, DEX 12 (→14), CON 12 (→13), INT 12, WIS 10, STR 8.
        .with_ability_scores([8, 12, 12, 12, 10, 15])
        .with_skills(["deception", "intimidation"])
        .with_starting_weapon("Light Crossbow")
        .with_starting_armor("Leather Armor")
        .build()
    )
    character = draft.character
    character.personality_traits = [
        "Sorrio quando minto. É o sorriso que convence.",
        "Finjo não ouvir. Finjo bem.",
    ]
    character.ideals = [
        "Transação. Tudo é troca; só importa quem paga primeiro.",
    ]
    character.bonds = [
        "O patrono prometeu-me um favor. Ainda não sei qual. Tenho medo de cobrar.",
    ]
    character.flaws = [
        "Não consigo recusar um favor. Cobrar depois, talvez. Recusar, nunca.",
    ]
    character.spellcasting = selection.to_spellcasting(
        get_class("Warlock"), abilities, 2
    )
    return character


# ============================================================================
# Registry
# ============================================================================


COMPANION_FACTORIES: dict[str, Callable[[], Character]] = {
    "thorgrim": make_thorgrim,
    "lyra": make_lyra,
    "mira": make_mira,
    "vex": make_vex,
    "garrick": make_garrick,
    "brom": make_brom,
    "kael": make_kael,
    "sage": make_sage,
    "maren": make_maren,
    "eldra": make_eldra,
    "tobias": make_tobias,
    "dax": make_dax,
}


# Centralized pt-BR blurbs used by CLI prompt and web wizard catalog.
# Keyed by COMPANION_FACTORIES key.
COMPANION_BLURBS: dict[str, str] = {
    "thorgrim": "Anão da colina, fighter tanque. Leal e calado.",
    "lyra": "Elfa alta, ranger. Mira certeira, cautelosa.",
    "mira": "Halfling, clériga. Curandeira devota, otimista.",
    "vex": "Tiefling, ladino. Esperto, motivações próprias.",
    "garrick": "Humano, paladino. Juramento e espada.",
    "brom": "Meio-orc, bárbaro. Força bruta, fúria contida.",
    "kael": "Elfo da floresta, mago. Estrategista arcano.",
    "sage": "Meio-elfo, sorcerer. Fogo nas veias.",
    "maren": "Humana, monja. Punho rápido, mente serena.",
    "eldra": "Gnoma da floresta, druida. Parla com os bichos.",
    "tobias": "Dragonborn, bardo. Trova e sopro de fogo.",
    "dax": "Halfling stout, warlock. Pacto com o patrono.",
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
