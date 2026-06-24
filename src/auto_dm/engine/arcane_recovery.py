"""Wizard's Arcane Recovery (PHB p. 115).

Once per day, on a short rest, the wizard can recover spell slots with
a combined level ≤ wizard_level / 2 (rounded up). No slot recovered
can be 6th level or higher.
"""
from __future__ import annotations

from auto_dm.state.models import Character


def arcane_recovery_max_slot_value(level: int) -> int:
    """PHB: total slot levels recoverable per day = ceil(level / 2)."""
    return (level + 1) // 2


def can_arcane_recover(character: Character) -> bool:
    return character.class_.lower() == "wizard"


def arcane_recovery(
    character: Character,
    slots_to_recover: list[int],
) -> tuple[bool, str]:
    """Recover spell slots via Arcane Recovery.

    Args:
        character: the wizard
        slots_to_recover: list of slot levels to recover (e.g. [2, 1] =
            one 2nd-level and one 1st-level slot)

    PHB rules:
      - Total combined level ≤ ceil(wizard_level / 2)
      - No recovered slot above 5th level
      - Once per short rest (caller's responsibility to track)
    """
    if not can_arcane_recover(character):
        return False, "only wizards can use Arcane Recovery"
    if character.spellcasting is None:
        return False, "no spellcasting"
    if any(s > 5 for s in slots_to_recover):
        return False, "cannot recover slots above 5th level"
    total = sum(slots_to_recover)
    cap = arcane_recovery_max_slot_value(character.level)
    if total > cap:
        return False, (
            f"total slot levels ({total}) exceeds Arcane Recovery cap "
            f"({cap} = ceil({character.level}/2))"
        )
    for slot_level in slots_to_recover:
        character.spellcasting.spell_slots[slot_level] = (
            character.spellcasting.spell_slots.get(slot_level, 0) + 1
        )
    return True, ""