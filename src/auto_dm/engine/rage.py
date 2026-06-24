"""Barbarian's Rage (PHB p. 48).

Rage is a bonus action that gives the barbarian a damage bonus on STR
melee attacks, resistance to bludgeoning/piercing/slashing, and advantage
on STR checks/saves. It ends after 1 minute, or earlier if the barbarian
is incapacitated or ends a turn without attacking/taking damage.

This module owns the state transitions and duration bookkeeping. The
combat pipeline (damage, saves) consults :func:`is_raging` /
:func:`rage_damage_bonus` to apply the effects.
"""
from __future__ import annotations

from dataclasses import dataclass

from auto_dm.state.models import Character, Condition


# ============================================================================
# PHB tables
# ============================================================================


# PHB p. 48: damage bonus while raging
def rage_damage_bonus(level: int) -> int:
    """Damage bonus on STR melee attacks while raging.

    PHB: +2 (L1-8), +3 (L9-15), +4 (L16+).
    """
    if level >= 16:
        return 4
    if level >= 9:
        return 3
    return 2


# PHB p. 79: rages per long rest
def rages_per_long_rest(level: int) -> int:
    """How many rages the barbarian gets per long rest.

    PHB: 2 (L1-2), 3 (L3-5), 4 (L6-11), 5 (L12-16), 6 (L17+).
    """
    if level >= 12:
        return 5
    if level >= 6:
        return 4
    if level >= 3:
        return 3
    return 2


# PHB p. 48: rage duration
RAGE_DURATION_ROUNDS = 10  # 1 minute = 10 rounds

# Damage types the barbarian resists while raging
RAGE_RESISTANCES = frozenset({"bludgeoning", "piercing", "slashing"})


# ============================================================================
# State transitions
# ============================================================================


@dataclass
class RageResult:
    """Outcome of an attempt to enter or exit Rage."""

    success: bool
    message: str
    duration_rounds: int = 0


def can_rage(character: Character) -> tuple[bool, str]:
    """Check whether the character can enter Rage right now.

    Preconditions (PHB):
      1. Must be a barbarian (class_ check).
      2. Must not already be raging.
      3. Must not be incapacitated.
      4. Must not be wearing heavy armor (ends rage; can't even start).
      5. Must have at least 1 rage remaining (rages_used < rages_max).
    """
    if character.class_.lower() != "barbarian":
        return False, "only barbarians can rage"
    if character.is_raging:
        return False, "already raging"
    if character.rages_used >= character.rages_max:
        return False, "no rages remaining"
    if Condition.INCAPACITATED in character.conditions:
        return False, "incapacitated"
    if Condition.UNCONSCIOUS in character.conditions:
        return False, "unconscious"
    # Heavy armor check: if the equipped armor is heavy, can't rage.
    if character.equipped.armor and character.equipped.armor.armor is not None:
        if character.equipped.armor.armor.stealth_disadvantage and (
            not character.equipped.armor.armor.add_dex_modifier
        ):
            return False, "heavy armor prevents raging"
    return True, ""


def enter_rage(character: Character) -> RageResult:
    """Enter Rage. Consumes 1 use, starts the duration counter."""
    allowed, reason = can_rage(character)
    if not allowed:
        return RageResult(success=False, message=reason)
    character.is_raging = True
    character.rages_used += 1
    character.rounds_raging = 0
    return RageResult(
        success=True,
        message=f"{character.name} entra em fúria!",
        duration_rounds=RAGE_DURATION_ROUNDS,
    )


def end_rage(character: Character, reason: str = "") -> RageResult:
    """Exit Rage. Resets the duration counter."""
    if not character.is_raging:
        return RageResult(success=False, message="not raging")
    character.is_raging = False
    character.rounds_raging = 0
    msg = f"{character.name} sai da fúria."
    if reason:
        msg += f" ({reason})"
    return RageResult(success=True, message=msg)


def tick_rage_duration(character: Character) -> RageResult | None:
    """Advance the rage duration by 1 round. Called at end of barbarian's turn.

    Returns a RageResult with success=False if the rage ended this tick.
    """
    if not character.is_raging:
        return None
    character.rounds_raging += 1
    if character.rounds_raging >= RAGE_DURATION_ROUNDS:
        return end_rage(character, "duração expirou")
    return None


def end_rage_if_incapacitated(character: Character) -> RageResult | None:
    """Check conditions that auto-end rage (called whenever conditions change)."""
    if not character.is_raging:
        return None
    if Condition.INCAPACITATED in character.conditions:
        return end_rage(character, "incapacitado")
    if Condition.UNCONSCIOUS in character.conditions:
        return end_rage(character, "inconsciente")
    return None


# ============================================================================
# Long rest integration
# ============================================================================


def recover_rages(character: Character) -> int:
    """Reset rages_used to 0 and end any active rage. Called on long rest."""
    if character.class_.lower() != "barbarian":
        return 0
    recovered = character.rages_used
    character.rages_used = 0
    if character.is_raging:
        end_rage(character, "long rest")
    return recovered


# ============================================================================
# Combat integration helpers
# ============================================================================


def is_raging(character: Character) -> bool:
    """True if the character is currently raging. NPCs and other classes are
    treated as not raging."""
    if not isinstance(character, Character):
        return False
    return character.is_raging


def apply_rage_resistance(damage_type: str) -> bool:
    """True if raging gives resistance to ``damage_type``."""
    return damage_type.lower() in RAGE_RESISTANCES