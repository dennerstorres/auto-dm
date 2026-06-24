"""Paladin's Divine Smite (PHB p. 85).

Starting at 2nd level, when a paladin hits a creature with a melee
weapon attack, they can expend a spell slot to deal radiant damage
on top of the regular weapon damage.

PHB damage:
  - 2d8 at 1st-level slot
  - +1d8 per slot level above 1st (3d8 at L2, 4d8 at L3, 5d8 at L4)
  - +1d8 bonus if the target is undead or fiend (any slot level)
  - Maximum 5d8 from a single smite

Smite is triggered at the time of the attack (it's not a separate
action). The caller passes ``smite_slot_level`` in the attack params;
the engine consumes the slot, rolls the damage, and reports it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from auto_dm.engine.dice import roll_dice
from auto_dm.engine.spellcasting import consume_slot
from auto_dm.state.models import Character, NPC


SMITE_DAMAGE_TYPE = "radiant"


@dataclass
class SmiteResult:
    """Outcome of a Divine Smite."""

    success: bool
    slot_level_used: int
    smite_dice: int  # 2..5
    damage: int
    target_creature_type: str  # "undead" | "fiend" | "other"
    bonus_dice_undead_fiend: int  # +1d8 if applicable
    reason: str = ""


def smite_dice_for_slot(slot_level: int) -> int:
    """Number of d8s from a smite at the given slot level.

    PHB: 2d8 at L1, +1d8/level, max 5d8.
    """
    return min(5, 1 + slot_level)


def is_undead_or_fiend(target: Character | NPC) -> bool:
    """Heuristic for the smite bonus. True if the target has any flag
    set or a name suggesting undead/fiend. (Out of MVP: a proper tag.)"""
    name = target.name.lower()
    keywords = (
        "undead", "zombie", "skeleton", "ghost", "vampire", "lich", "wraith",
        "fiend", "demon", "devil", "imp", "hell", "abyssal", "succubus",
    )
    return any(k in name for k in keywords)


def creature_type_for_smite(target: Character | NPC) -> str:
    """'undead', 'fiend', or 'other'. Used in the SmiteResult."""
    name = target.name.lower()
    undead_kw = ("undead", "zombie", "skeleton", "ghost", "vampire", "lich", "wraith")
    fiend_kw = ("fiend", "demon", "devil", "imp", "abyssal", "succubus", "hell")
    if any(k in name for k in undead_kw):
        return "undead"
    if any(k in name for k in fiend_kw):
        return "fiend"
    return "other"


def divine_smite(
    paladin: Character,
    target: Character | NPC,
    slot_level: int,
    *,
    rng: random.Random | None = None,
) -> SmiteResult:
    """Apply Divine Smite: roll damage, consume the slot.

    Caller is responsible for passing the right ``slot_level`` (PHB
    cap of 5d8 means no slot above 4 is ever useful; this function
    caps internally to be safe).
    """
    rng = rng or random.Random()
    if paladin.class_.lower() != "paladin":
        return SmiteResult(
            success=False, slot_level_used=0, smite_dice=0, damage=0,
            target_creature_type="other", bonus_dice_undead_fiend=0,
            reason="only paladins can divine smite",
        )
    if paladin.spellcasting is None:
        return SmiteResult(
            success=False, slot_level_used=0, smite_dice=0, damage=0,
            target_creature_type="other", bonus_dice_undead_fiend=0,
            reason="paladin has no spellcasting",
        )
    if slot_level < 1:
        return SmiteResult(
            success=False, slot_level_used=0, smite_dice=0, damage=0,
            target_creature_type="other", bonus_dice_undead_fiend=0,
            reason="slot_level must be >= 1",
        )

    # Consume the slot (this also validates availability)
    try:
        used_level = consume_slot(paladin.spellcasting, slot_level)
    except ValueError as e:
        return SmiteResult(
            success=False, slot_level_used=0, smite_dice=0, damage=0,
            target_creature_type="other", bonus_dice_undead_fiend=0,
            reason=str(e),
        )

    # Roll smite damage
    base_dice = smite_dice_for_slot(used_level)
    is_uf = is_undead_or_fiend(target)
    bonus = 1 if is_uf else 0
    total_dice = base_dice + bonus
    roll = roll_dice(f"{total_dice}d8", rng=rng)

    return SmiteResult(
        success=True,
        slot_level_used=used_level,
        smite_dice=base_dice,
        damage=roll.total,
        target_creature_type=creature_type_for_smite(target),
        bonus_dice_undead_fiend=bonus,
    )