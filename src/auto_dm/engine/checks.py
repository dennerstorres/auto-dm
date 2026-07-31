"""Ability checks, skill checks, and saving throws for the virtual table.

Phase 52 adds the *pending check* protocol: the DM announces a check and
fixes its DC **before** the player rolls (``build_pending_check``), and the
engine alone decides success or failure (``resolve_pending_check``). The
LLM never compares a total to a DC — it only narrates the outcome the
engine hands it.
"""
from __future__ import annotations

import random
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

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


# ============================================================================
# Pending checks (Phase 52)
# ============================================================================


# PHB p. 174 "Typical Difficulty Classes". The DM picks a DC from this
# band; anything outside it is clamped so a hallucinated "DC 90" can never
# make a check unwinnable.
DC_MIN = 5
DC_MAX = 30
DEFAULT_DC = 15

DC_LABELS: dict[int, str] = {
    5: "muito facil",
    10: "facil",
    15: "media",
    20: "dificil",
    25: "muito dificil",
    30: "quase impossivel",
}


def clamp_dc(value: object) -> int:
    """Coerce the DM's proposed DC into the PHB band [5, 30].

    Anything non-numeric (missing key, ``"quinze"``, ``None``) falls back
    to :data:`DEFAULT_DC` — a missing DC must never block the check.
    """
    try:
        dc = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_DC
    return max(DC_MIN, min(DC_MAX, dc))


def dc_label(dc: int) -> str:
    """Return the pt-BR difficulty band for a DC (nearest PHB tier)."""
    tier = min(DC_LABELS, key=lambda t: (abs(t - dc), t))
    return DC_LABELS[tier]


def build_pending_check(
    check: str,
    dc: object,
    *,
    kind: str | None = None,
    reason: str = "",
    character_id: str | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
) -> dict:
    """Build the ``GameState.pending_check`` payload for a DM request.

    ``check`` goes through :func:`resolve_check`, so the DM may write
    "Investigação", "investigation", or "teste de Percepção" and still land
    on the same skill. Raises ``ValueError`` when the check text can't be
    resolved — the caller drops the request rather than storing junk.

    Advantage and disadvantage are the *circumstantial* ones the DM grants
    ("você tem uma lupa"); they cancel out when both are set, per PHB.
    """
    spec = resolve_check(check, kind)
    adv = bool(advantage) and not bool(disadvantage)
    dis = bool(disadvantage) and not bool(advantage)
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": spec.kind,
        "key": spec.key,
        "label": spec.label,
        "ability": spec.ability.value,
        "dc": clamp_dc(dc),
        "reason": (reason or "").strip()[:200],
        "character_id": character_id,
        "advantage": adv,
        "disadvantage": dis,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "outcome": None,
        "total": None,
        "natural": None,
    }


def pending_check_is_open(pending: dict | None) -> bool:
    """True when ``pending`` is a check still waiting on a roll."""
    return bool(pending) and not pending.get("resolved")


def check_matches_pending(spec: CheckSpec, pending: dict | None) -> bool:
    """True when a rolled check answers the open pending request.

    Matches on kind + key, so rolling Percepção against a pending
    Investigação request is treated as a free roll, not an answer.
    """
    if not pending_check_is_open(pending):
        return False
    return pending.get("kind") == spec.kind and pending.get("key") == spec.key


def resolve_pending_check(pending: dict, result: CharacterCheckResult) -> dict:
    """Score a roll against the pending check's DC and close the request.

    Mutates ``pending`` in place (``resolved``/``outcome``/``total``/
    ``natural``) and returns the resolution the web layer publishes.

    A natural 20 or 1 on an ability check is **not** an automatic
    success/failure in 5e (PHB p. 7 — that rule is for attack rolls and
    death saves). We report ``natural`` for flavor and nothing else.
    """
    dc = clamp_dc(pending.get("dc"))
    total = result.roll.total
    natural = result.roll.kept[0]
    success = total >= dc

    pending["resolved"] = True
    pending["outcome"] = "success" if success else "failure"
    pending["total"] = total
    pending["natural"] = natural

    return {
        "id": pending.get("id"),
        "kind": pending.get("kind"),
        "key": pending.get("key"),
        "label": pending.get("label"),
        "dc": dc,
        "dc_label": dc_label(dc),
        "total": total,
        "natural": natural,
        "success": success,
        "outcome": pending["outcome"],
        "margin": total - dc,
        "reason": pending.get("reason", ""),
        "narration_line": describe_check_outcome(pending, result, dc=dc),
    }


def describe_check_outcome(
    pending: dict,
    result: CharacterCheckResult,
    *,
    dc: int | None = None,
) -> str:
    """Build the ``[TESTE]`` line handed to the DM to narrate.

    Deliberately terse and fully numeric: every value already came out of
    the engine, so the DM has nothing left to invent — it only describes
    what the roll means in the fiction. See the "Testes de perícia" section
    of ``DM_SYSTEM_PROMPT``.
    """
    dc = clamp_dc(pending.get("dc")) if dc is None else dc
    total = result.roll.total
    success = total >= dc
    verdict = "SUCESSO" if success else "FALHA"
    margin = abs(total - dc)
    mode = ""
    if result.advantage:
        mode = " (com vantagem)"
    elif result.disadvantage:
        mode = " (com desvantagem)"
    reason = pending.get("reason") or ""
    context = f" Motivo do teste: {reason}." if reason else ""
    return (
        f"[TESTE] {result.character_name} rolou {pending.get('label')}{mode}: "
        f"{total} contra CD {dc} — {verdict} por {margin}.{context} "
        "Narre a consequência desse resultado."
    )
