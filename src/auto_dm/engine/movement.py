"""Movement-related checks and contests (Phase 25e).

Covers:
- ``climb_check`` / ``swim_check``: STR (Athletics) or DEX (Acrobatics)
  ability check against a DC. Falls back to STR-only (Athletics) when
  the caller doesn't specify the skill. Climbing/swimming at half speed
  is the no-DC default; this module kicks in when something is harder
  than that (stormy water, slick surface, etc.).
- ``grapple``: contested Athletics check; success applies the
  ``GRAPPLED`` condition to the target, and the ``GRAPPLED``/``RESTRAINED``
  conditions to the grappler (PHB p. 195).
- ``shove``: contested Athletics check; success pushes the target 5 ft
  away (PHB p. 195). If the target is already prone, this is automatic.

Results are dataclasses; the engine does NOT mutate state directly. The
caller (CombatEngine handler or GameApp) applies the conditions / HP
changes via StateManager.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from auto_dm.engine.combat import roll_d20
from auto_dm.engine.conditions import (
    apply_attack_modifiers,
    apply_save_modifiers,
)
from auto_dm.state.models import Ability, Character, NPC, Skill


# Skill → ability mapping (PHB). Used by checks that go through a
# skill (e.g. STR (Athletics)) rather than a raw ability.
_SKILL_ABILITY: dict[Skill, Ability] = {
    Skill.ATHLETICS: Ability.STR,
    Skill.ACROBATICS: Ability.DEX,
    Skill.SLEIGHT_OF_HAND: Ability.DEX,
    Skill.STEALTH: Ability.DEX,
    Skill.ARCANA: Ability.INT,
    Skill.HISTORY: Ability.INT,
    Skill.INVESTIGATION: Ability.INT,
    Skill.NATURE: Ability.INT,
    Skill.RELIGION: Ability.INT,
    Skill.ANIMAL_HANDLING: Ability.WIS,
    Skill.INSIGHT: Ability.WIS,
    Skill.MEDICINE: Ability.WIS,
    Skill.PERCEPTION: Ability.WIS,
    Skill.SURVIVAL: Ability.WIS,
    Skill.DECEPTION: Ability.CHA,
    Skill.INTIMIDATION: Ability.CHA,
    Skill.PERFORMANCE: Ability.CHA,
    Skill.PERSUASION: Ability.CHA,
}


@dataclass
class AbilityCheckResult:
    """Outcome of an ability check (climb, swim, etc.)."""

    creature_id: str
    ability: Ability
    skill: Optional[str]
    dc: int
    roll: int
    modifier: int
    total: int
    is_success: bool


@dataclass
class ContestResult:
    """Outcome of a contested check (grapple, shove)."""

    attacker_id: str
    target_id: str
    action: str  # "grapple" | "shove"
    attacker_roll: int
    attacker_modifier: int
    attacker_total: int
    target_roll: int
    target_modifier: int
    target_total: int
    is_success: bool


# ============================================================================
# Ability / skill checks
# ============================================================================


def _skill_modifier(creature: Character | NPC, skill: Skill) -> int:
    """Compute the modifier for a given skill on the creature.

    Only Characters have a ``proficiencies.skills`` list; NPCs and
    uncontested creatures default to no proficiency bonus.
    """
    ability = _SKILL_ABILITY.get(skill, Ability.STR)
    base = creature.abilities.modifier(ability)
    if isinstance(creature, Character):
        if skill in creature.proficiencies.skills:
            base += creature.proficiency_bonus
    return base


def climb_check(
    creature: Character | NPC,
    dc: int,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> AbilityCheckResult:
    """Roll a climb check: STR (Athletics), with advantage vs slippery
    surfaces (DMG p. 110). Returns the result without mutating state.
    """
    return _ability_skill_check(
        creature, Ability.STR, Skill.ATHLETICS, dc,
        advantage=advantage, disadvantage=disadvantage, rng=rng,
    )


def swim_check(
    creature: Character | NPC,
    dc: int,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> AbilityCheckResult:
    """Roll a swim check: STR (Athletics). Heavy armor imposes disadvantage
    (PHB p. 198). Caller passes that as the disadvantage flag.
    """
    return _ability_skill_check(
        creature, Ability.STR, Skill.ATHLETICS, dc,
        advantage=advantage, disadvantage=disadvantage, rng=rng,
    )


def _ability_skill_check(
    creature: Character | NPC,
    ability: Ability,
    skill: Skill,
    dc: int,
    *,
    advantage: bool,
    disadvantage: bool,
    rng: random.Random | None,
) -> AbilityCheckResult:
    rng = rng or random.Random()
    modifier = _skill_modifier(creature, skill)
    result = roll_d20(
        advantage=advantage,
        disadvantage=disadvantage,
        modifier=modifier,
        rng=rng,
    )
    natural = result.kept[0]
    return AbilityCheckResult(
        creature_id=creature.id,
        ability=ability,
        skill=skill.value,
        dc=dc,
        roll=natural,
        modifier=modifier,
        total=result.total,
        is_success=result.total >= dc,
    )


# ============================================================================
# Contests (grapple, shove)
# ============================================================================


def grapple(
    attacker: Character | NPC,
    target: Character | NPC,
    *,
    rng: random.Random | None = None,
) -> ContestResult:
    """Contested grapple (PHB p. 195).

    Both roll d20 + STR (Athletics). The attacker can also use DEX
    (Acrobatics) — not implemented in this MVP entry point; the caller
    can dispatch to a DEX-based variant if needed.
    """
    return _contested_athletics(attacker, target, "grapple", rng=rng)


def shove(
    attacker: Character | NPC,
    target: Character | NPC,
    *,
    rng: random.Random | None = None,
) -> ContestResult:
    """Contested shove (PHB p. 195).

    Both roll d20 + STR (Athletics). Success pushes the target 5 ft
    away; caller applies the effect (no separate condition).
    """
    return _contested_athletics(attacker, target, "shove", rng=rng)


def _contested_athletics(
    attacker: Character | NPC,
    target: Character | NPC,
    action: str,
    *,
    rng: random.Random | None,
) -> ContestResult:
    rng = rng or random.Random()
    # Attacker
    atk_roll = rng.randint(1, 20)
    atk_mod = _skill_modifier(attacker, Skill.ATHLETICS)
    atk_total = atk_roll + atk_mod
    # Target — defender chooses Athletics (STR) or Acrobatics (DEX).
    # For MVP we assume Athletics (PHB default). Caller can pre-call
    # with a DEX variant if needed.
    tgt_roll = rng.randint(1, 20)
    tgt_mod = _skill_modifier(target, Skill.ATHLETICS)
    tgt_total = tgt_roll + tgt_mod
    return ContestResult(
        attacker_id=attacker.id,
        target_id=target.id,
        action=action,
        attacker_roll=atk_roll,
        attacker_modifier=atk_mod,
        attacker_total=atk_total,
        target_roll=tgt_roll,
        target_modifier=tgt_mod,
        target_total=tgt_total,
        is_success=atk_total >= tgt_total,
    )


# ============================================================================
# Convenience predicates
# ============================================================================


def can_climb_unassisted(creature: Character | NPC) -> bool:
    """True if the creature can climb at full speed without a check.

    Most creatures can climb at half speed with no check; a creature
    with a climbing speed (e.g. via race or class feature) climbs at
    full speed. The PHB doesn't expose climbing speed on Character
    directly — if you need this, store a custom flag.
    """
    # Default: assume half-speed, no check needed.
    return True


def forced_disadvantage_climb(creature: Character | NPC) -> bool:
    """True if the creature has disadvantage on climb checks.

    Slipping conditions or carrying heavy loads impose disadvantage.
    The PHB doesn't track this state; we expose a stub so the combat
    handler can compose conditions into the check.
    """
    return False


def forced_disadvantage_swim(creature: Character | NPC) -> bool:
    """True if the creature has disadvantage on swim checks.

    PHB p. 198: "If you're wearing heavy armor, you have disadvantage
    on STR (Athletics) checks made to swim."
    """
    if not isinstance(creature, Character):
        return False
    armor = creature.equipped.armor
    if armor is None or armor.armor is None:
        return False
    # Heavy armor is the only armor category with swim disadvantage.
    # We don't have the armor category here, but we can check by name.
    return "plate" in armor.name.lower() or "heavy" in armor.name.lower()