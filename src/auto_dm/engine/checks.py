"""Ability checks, skill checks, and saving throws for the virtual table."""
from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass

from auto_dm.engine.dice import DiceRoll, roll_d20
from auto_dm.state.models import Ability, Character, Skill


ABILITY_LABELS: dict[Ability, str] = {
    Ability.STR: "Forca",
    Ability.DEX: "Destreza",
    Ability.CON: "Constituicao",
    Ability.INT: "Inteligencia",
    Ability.WIS: "Sabedoria",
    Ability.CHA: "Carisma",
}

SKILL_ABILITY: dict[Skill, Ability] = {
    Skill.ACROBATICS: Ability.DEX,
    Skill.ANIMAL_HANDLING: Ability.WIS,
    Skill.ARCANA: Ability.INT,
    Skill.ATHLETICS: Ability.STR,
    Skill.DECEPTION: Ability.CHA,
    Skill.HISTORY: Ability.INT,
    Skill.INSIGHT: Ability.WIS,
    Skill.INTIMIDATION: Ability.CHA,
    Skill.INVESTIGATION: Ability.INT,
    Skill.MEDICINE: Ability.WIS,
    Skill.NATURE: Ability.INT,
    Skill.PERCEPTION: Ability.WIS,
    Skill.PERFORMANCE: Ability.CHA,
    Skill.PERSUASION: Ability.CHA,
    Skill.RELIGION: Ability.INT,
    Skill.SLEIGHT_OF_HAND: Ability.DEX,
    Skill.STEALTH: Ability.DEX,
    Skill.SURVIVAL: Ability.WIS,
}

SKILL_LABELS: dict[Skill, str] = {
    Skill.ACROBATICS: "Acrobacia",
    Skill.ANIMAL_HANDLING: "Adestrar Animais",
    Skill.ARCANA: "Arcanismo",
    Skill.ATHLETICS: "Atletismo",
    Skill.DECEPTION: "Enganacao",
    Skill.HISTORY: "Historia",
    Skill.INSIGHT: "Intuicao",
    Skill.INTIMIDATION: "Intimidacao",
    Skill.INVESTIGATION: "Investigacao",
    Skill.MEDICINE: "Medicina",
    Skill.NATURE: "Natureza",
    Skill.PERCEPTION: "Percepcao",
    Skill.PERFORMANCE: "Atuacao",
    Skill.PERSUASION: "Persuasao",
    Skill.RELIGION: "Religiao",
    Skill.SLEIGHT_OF_HAND: "Prestidigitacao",
    Skill.STEALTH: "Furtividade",
    Skill.SURVIVAL: "Sobrevivencia",
}


@dataclass(frozen=True)
class CheckSpec:
    """Resolved check target."""

    kind: str
    key: str
    label: str
    ability: Ability


@dataclass(frozen=True)
class CharacterCheckResult:
    """Transparent roll result for a player-facing d20 check."""

    character_id: str
    character_name: str
    spec: CheckSpec
    ability_modifier: int
    proficiency_bonus: int
    proficient: bool
    modifier: int
    roll: DiceRoll
    advantage: bool = False
    disadvantage: bool = False


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for ch in "-_/:":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _ability_aliases() -> dict[str, Ability]:
    aliases: dict[str, Ability] = {}
    for ability, label in ABILITY_LABELS.items():
        aliases[_norm(ability.value)] = ability
        aliases[_norm(ability.name)] = ability
        aliases[_norm(label)] = ability
    aliases.update({
        "forca": Ability.STR,
        "for": Ability.STR,
        "str": Ability.STR,
        "destreza": Ability.DEX,
        "des": Ability.DEX,
        "dex": Ability.DEX,
        "constituicao": Ability.CON,
        "con": Ability.CON,
        "inteligencia": Ability.INT,
        "int": Ability.INT,
        "sabedoria": Ability.WIS,
        "sab": Ability.WIS,
        "wis": Ability.WIS,
        "carisma": Ability.CHA,
        "car": Ability.CHA,
        "cha": Ability.CHA,
    })
    return aliases


def _skill_aliases() -> dict[str, Skill]:
    aliases: dict[str, Skill] = {}
    for skill, label in SKILL_LABELS.items():
        aliases[_norm(skill.value)] = skill
        aliases[_norm(label)] = skill
    aliases.update({
        "animal handling": Skill.ANIMAL_HANDLING,
        "lidar com animais": Skill.ANIMAL_HANDLING,
        "adestrar animais": Skill.ANIMAL_HANDLING,
        "arcana": Skill.ARCANA,
        "arcanismo": Skill.ARCANA,
        "athletics": Skill.ATHLETICS,
        "atletismo": Skill.ATHLETICS,
        "deception": Skill.DECEPTION,
        "enganacao": Skill.DECEPTION,
        "enganar": Skill.DECEPTION,
        "history": Skill.HISTORY,
        "historia": Skill.HISTORY,
        "insight": Skill.INSIGHT,
        "intuicao": Skill.INSIGHT,
        "investigation": Skill.INVESTIGATION,
        "investigacao": Skill.INVESTIGATION,
        "medicine": Skill.MEDICINE,
        "medicina": Skill.MEDICINE,
        "nature": Skill.NATURE,
        "natureza": Skill.NATURE,
        "perception": Skill.PERCEPTION,
        "percepcao": Skill.PERCEPTION,
        "performance": Skill.PERFORMANCE,
        "atuacao": Skill.PERFORMANCE,
        "performance artistica": Skill.PERFORMANCE,
        "persuasion": Skill.PERSUASION,
        "persuasao": Skill.PERSUASION,
        "religion": Skill.RELIGION,
        "religiao": Skill.RELIGION,
        "sleight of hand": Skill.SLEIGHT_OF_HAND,
        "prestigitacao": Skill.SLEIGHT_OF_HAND,
        "prestidigitacao": Skill.SLEIGHT_OF_HAND,
        "furtar bolsos": Skill.SLEIGHT_OF_HAND,
        "stealth": Skill.STEALTH,
        "furtividade": Skill.STEALTH,
        "survival": Skill.SURVIVAL,
        "sobrevivencia": Skill.SURVIVAL,
    })
    return aliases


def resolve_check(check: str, kind: str | None = None) -> CheckSpec:
    """Resolve user-facing check text to a skill, ability check, or save."""
    raw = _norm(check)
    explicit_kind = _norm(kind or "")

    for prefix in ("teste de resistencia de ", "salvaguarda de ", "save de "):
        if raw.startswith(prefix):
            explicit_kind = "save"
            raw = raw.removeprefix(prefix)
            break
    for prefix in ("teste de habilidade de ", "teste de ", "rolar ", "role "):
        if raw.startswith(prefix):
            raw = raw.removeprefix(prefix)
            break

    ability_match = _ability_aliases().get(raw)
    skill_match = _skill_aliases().get(raw)

    if explicit_kind in {"save", "saving throw", "salvaguarda", "resistencia"}:
        if not ability_match:
            raise ValueError(f"Unknown saving throw ability: {check!r}")
        return CheckSpec(
            kind="save",
            key=ability_match.value,
            label=f"Salvaguarda de {ABILITY_LABELS[ability_match]}",
            ability=ability_match,
        )

    if explicit_kind in {"ability", "atributo", "habilidade"}:
        if not ability_match:
            raise ValueError(f"Unknown ability: {check!r}")
        return CheckSpec(
            kind="ability",
            key=ability_match.value,
            label=f"Teste de {ABILITY_LABELS[ability_match]}",
            ability=ability_match,
        )

    if explicit_kind in {"skill", "pericia"}:
        if not skill_match:
            raise ValueError(f"Unknown skill: {check!r}")
        return CheckSpec(
            kind="skill",
            key=skill_match.value,
            label=SKILL_LABELS[skill_match],
            ability=SKILL_ABILITY[skill_match],
        )

    if skill_match:
        return CheckSpec(
            kind="skill",
            key=skill_match.value,
            label=SKILL_LABELS[skill_match],
            ability=SKILL_ABILITY[skill_match],
        )
    if ability_match:
        return CheckSpec(
            kind="ability",
            key=ability_match.value,
            label=f"Teste de {ABILITY_LABELS[ability_match]}",
            ability=ability_match,
        )
    raise ValueError(f"Unknown check: {check!r}")


def check_modifier(character: Character, spec: CheckSpec) -> tuple[int, bool, int, int]:
    """Return total modifier, proficiency flag, ability mod, and prof component."""
    ability_mod = character.abilities.modifier(spec.ability)
    proficient = False
    if spec.kind == "skill":
        skill = Skill(spec.key)
        proficient = skill in character.proficiencies.skills
    elif spec.kind == "save":
        proficient = spec.ability in character.proficiencies.saves
    prof_bonus = character.proficiency_bonus if proficient else 0
    return ability_mod + prof_bonus, proficient, ability_mod, prof_bonus


def roll_character_check(
    character: Character,
    check: str,
    *,
    kind: str | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> CharacterCheckResult:
    """Roll a d20 check using the character's sheet bonuses."""
    spec = resolve_check(check, kind)
    modifier, proficient, ability_mod, prof_bonus = check_modifier(character, spec)
    roll = roll_d20(
        advantage=advantage,
        disadvantage=disadvantage,
        modifier=modifier,
        rng=rng,
    )
    return CharacterCheckResult(
        character_id=character.id,
        character_name=character.name,
        spec=spec,
        ability_modifier=ability_mod,
        proficiency_bonus=prof_bonus,
        proficient=proficient,
        modifier=modifier,
        roll=roll,
        advantage=advantage and not disadvantage,
        disadvantage=disadvantage and not advantage,
    )
