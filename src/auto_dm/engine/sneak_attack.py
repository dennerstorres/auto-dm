"""Rogue's Sneak Attack (PHB p. 96).

Once per turn, a rogue can add extra damage to one attack that meets:
  - uses a finesse or ranged weapon
  - has advantage on the attack roll, OR an ally is within 5 feet of
    the target and the rogue doesn't have disadvantage
The extra damage is 1d6 at L1, +1d6 every 2 rogue levels (1d6 at L1,
2d6 at L3, 3d6 at L5, ... capped at 5d6).
"""
from __future__ import annotations

import random

from auto_dm.engine.dice import roll_dice
from auto_dm.state.models import Character, NPC


def sneak_attack_dice(level: int) -> int:
    """Number of Sneak Attack damage dice for a rogue of ``level``.

    PHB: 1d6 at L1, +1d6 per 2 rogue levels, max 5d6.
    """
    return min(5, 1 + (level - 1) // 2)


def can_sneak_attack(
    attacker: Character,
    target: Character | NPC,
    *,
    has_advantage: bool,
    has_disadvantage: bool,
    ally_adjacent: bool,
    weapon_is_finesse_or_ranged: bool,
) -> bool:
    """PHB preconditions for triggering Sneak Attack damage."""
    if attacker.class_.lower() != "rogue":
        return False
    if attacker.sneak_attack_used_this_turn:
        return False
    if not weapon_is_finesse_or_ranged:
        return False
    # PHB: "The attack must use a finesse or a ranged weapon." (handled)
    # You don't need advantage if an ally is within 5 ft of the target,
    # and you don't have disadvantage on the attack roll.
    if has_disadvantage and not has_advantage:
        return False
    if has_advantage or ally_adjacent:
        return True
    return False


def roll_sneak_attack(
    attacker: Character,
    *,
    rng: random.Random | None = None,
) -> int:
    """Roll and apply Sneak Attack damage. Returns the rolled total.

    Marks ``sneak_attack_used_this_turn = True`` so the rogue can't
    trigger it again this turn.
    """
    rng = rng or random.Random()
    dice = sneak_attack_dice(attacker.level)
    roll = roll_dice(f"{dice}d6", rng=rng)
    attacker.sneak_attack_used_this_turn = True
    return roll.total


def reset_turn_flags(attacker: Character) -> None:
    """Clear the per-turn Sneak Attack flag. Call at the start of the
    rogue's turn (PHB: "once per turn")."""
    attacker.sneak_attack_used_this_turn = False