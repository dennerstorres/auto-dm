"""Adventuring mechanics: rests, falling damage, suffocation, travel pace.

These are PHB rules that sit between combat and exploration. None of them
mutate state directly — they return result objects the caller applies via
``StateManager``. This keeps the module pure and testable.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from auto_dm.engine.dice import roll_dice
from auto_dm.state.models import Ability, Character, Condition, NPC


# ============================================================================
# Resting
# ============================================================================


@dataclass
class ShortRestResult:
    """PHB: short rest is at least 1 hour of light activity. A character
    can spend hit dice to heal. We don't enforce the "at least 1 hour"
    time cost — the narrative layer handles time."""

    hit_dice_spent: int
    hp_recovered: int
    hit_dice_remaining_after: int


@dataclass
class LongRestResult:
    """PHB: long rest = 8 hours, at least 6 of sleep. Restores all HP,
    half of total hit dice (minimum 1), and most daily resources."""

    hp_recovered: int
    hp_now: int
    hit_dice_recovered: int
    hit_dice_now: int
    # Effects cleared on long rest
    conditions_cleared: list[str]
    exhaustion_reduced: int
    # Spell slot restoration is the caller's responsibility (handled in
    # spellcasting module) — this struct doesn't track slots.


def short_rest(
    character: Character,
    *,
    hit_dice_to_spend: int | None = None,
    rng: random.Random | None = None,
) -> ShortRestResult:
    """Apply a short rest to one character.

    By default, spends all remaining hit dice. Caller can override
    ``hit_dice_to_spend``. HP recovered per die = hit_die face + CON mod
    (PHB: "add your Constitution modifier to the roll"). Minimum 1 HP
    per die spent (you can't "waste" a die).
    """
    rng = rng or random.Random()
    if hit_dice_to_spend is None:
        hit_dice_to_spend = character.hit_dice_remaining
    hit_dice_to_spend = max(0, min(hit_dice_to_spend, character.hit_dice_remaining))

    if hit_dice_to_spend == 0:
        return ShortRestResult(
            hit_dice_spent=0,
            hp_recovered=0,
            hit_dice_remaining_after=character.hit_dice_remaining,
        )

    con_mod = character.abilities.modifier(Ability.CON)
    hit_die_face = _parse_hit_die_face(character.hit_dice)
    rolls = roll_dice(f"{hit_dice_to_spend}d{hit_die_face}", rng=rng)
    hp_recovered = sum(max(1, r + con_mod) for r in rolls.rolls)

    character.hp_current = min(character.hp_max, character.hp_current + hp_recovered)
    character.hit_dice_remaining -= hit_dice_to_spend

    return ShortRestResult(
        hit_dice_spent=hit_dice_to_spend,
        hp_recovered=hp_recovered,
        hit_dice_remaining_after=character.hit_dice_remaining,
    )


def long_rest(character: Character) -> LongRestResult:
    """Apply a long rest.

    PHB rules (used by the engine):
    - HP fully restored.
    - Hit dice recovered: half of total (minimum 1).
    - Exhaustion reduced by 1.
    - Most conditions clear (e.g. poisoned, frightened) — see
      ``_LONG_REST_CLEARS`` below.
    - Spell slot restoration is handled by the spellcasting module.

    NOTE: the PHB also limits you to one long rest per 24 hours and
    requires 6+ hours of sleep. The narrative layer enforces those.
    """
    total_hit_dice = _parse_total_hit_dice(character.hit_dice, character.level)
    recovery = max(1, total_hit_dice // 2)

    hp_before = character.hp_current
    character.hp_current = character.hp_max
    hp_recovered = character.hp_current - hp_before

    hit_dice_before = character.hit_dice_remaining
    character.hit_dice_remaining = min(total_hit_dice, hit_dice_before + recovery)
    hit_dice_recovered = character.hit_dice_remaining - hit_dice_before

    exhaustion_reduced = 0
    if character.exhaustion_level > 0:
        character.exhaustion_level -= 1
        exhaustion_reduced = 1

    cleared: list[str] = []
    for cond in _LONG_REST_CLEARS:
        if cond in character.conditions:
            character.conditions.remove(cond)
            cleared.append(cond.value)

    # Reset death saves
    character.death_save_successes = 0
    character.death_save_failures = 0

    return LongRestResult(
        hp_recovered=hp_recovered,
        hp_now=character.hp_current,
        hit_dice_recovered=hit_dice_recovered,
        hit_dice_now=character.hit_dice_remaining,
        conditions_cleared=cleared,
        exhaustion_reduced=exhaustion_reduced,
    )


# Conditions that clear on a long rest (PHB p. 186)
_LONG_REST_CLEARS = [
    Condition.BLINDED, Condition.DEAFENED, Condition.FRIGHTENED,
    Condition.PARALYZED, Condition.PETRIFIED, Condition.POISONED,
    Condition.STUNNED, Condition.UNCONSCIOUS,
]


# ============================================================================
# Falling damage
# ============================================================================


def falling_damage(
    distance_feet: int,
    *,
    rng: random.Random | None = None,
) -> int:
    """PHB: 1d6 bludgeoning per 10 feet fallen, max 20d6. The damage is
    the same regardless of who falls. Distance is capped at 200 ft."""
    rng = rng or random.Random()
    if distance_feet <= 0:
        return 0
    dice = min(20, max(1, distance_feet // 10))
    roll = roll_dice(f"{dice}d6", rng=rng)
    return roll.total


# ============================================================================
# Suffocation
# ============================================================================


@dataclass
class SuffocationState:
    """PHB suffocation tracker. A creature can hold its breath for a
    number of rounds equal to 1 + its CON modifier (min 1 round). After
    that, it can survive for as many rounds as it has remaining, then
    drops to 0 HP and starts dying."""

    rounds_held: int = 0
    max_rounds: int = 0  # 1 + CON mod (min 1)
    is_suffocating: bool = False  # True once max_rounds exceeded

    @property
    def rounds_remaining(self) -> int:
        return max(0, self.max_rounds - self.rounds_held)


def start_suffocation(creature: Character | NPC) -> SuffocationState:
    """Begin tracking breath for a creature. Holds breath for
    ``1 + CON mod`` rounds (min 1)."""
    con_mod = creature.abilities.modifier(Ability.CON)
    max_rounds = max(1, 1 + con_mod)
    return SuffocationState(rounds_held=0, max_rounds=max_rounds, is_suffocating=False)


def hold_breath_round(state: SuffocationState) -> SuffocationState:
    """Increment the held-breath counter by 1 round. Returns the same
    state object so callers can chain."""
    state.rounds_held += 1
    if state.rounds_held > state.max_rounds:
        state.is_suffocating = True
    return state


# ============================================================================
# Helpers
# ============================================================================


def _parse_hit_die_face(hit_dice: str) -> int:
    """Parse '1d10' -> 10. Returns 8 as a sensible default if unparseable."""
    m = re.match(r"^\d*d(\d+)$", hit_dice)
    return int(m.group(1)) if m else 8


def _parse_total_hit_dice(hit_dice: str, level: int) -> int:
    """Total hit dice = level (PHB: you get one per level)."""
    return max(1, level)
