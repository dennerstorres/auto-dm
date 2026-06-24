"""Class resource pools: Ki, Sorcery Points, Lay on Hands, Second Wind.

Each pool is a small int counter on the character. This module owns
spend/recover helpers and PHB table lookups.

Recovery:
  - Ki: short rest
  - Sorcery Points: long rest
  - Lay on Hands: long rest
  - Second Wind: short rest (1/short, 2 at L17+)
  - Action Surge: short rest (1, 2 at L17+)
"""
from __future__ import annotations

from auto_dm.state.models import Character


# ============================================================================
# Ki (Monk PHB p. 78)
# ============================================================================


def ki_max_for(level: int) -> int:
    """PHB: ki points = monk level."""
    return max(0, level)


def spend_ki(character: Character, amount: int = 1) -> bool:
    """Spend ``amount`` ki. Returns True on success."""
    if character.ki_points < amount:
        return False
    character.ki_points -= amount
    return True


def recover_ki_on_short_rest(character: Character) -> int:
    """Short rest: recover all ki."""
    if character.class_.lower() != "monk":
        return 0
    before = character.ki_points
    character.ki_points = character.ki_max
    return character.ki_points - before


# ============================================================================
# Sorcery Points (Sorcerer PHB p. 101)
# ============================================================================


def sorcery_points_max_for(level: int) -> int:
    """PHB: sorcery points = sorcerer level."""
    return max(0, level)


def spend_sorcery_points(character: Character, amount: int) -> bool:
    if character.sorcery_points < amount:
        return False
    character.sorcery_points -= amount
    return True


def create_spell_slot_from_points(
    character: Character, slot_level: int,
) -> bool:
    """PHB: convert sorcery points → spell slot.

    Cost: 2 (L1), 3 (L2), 5 (L3), 6 (L4), 7 (L5), 8 (L6), 9 (L7), 10 (L8), 11 (L9)
    """
    cost_table = {1: 2, 2: 3, 3: 5, 4: 6, 5: 7, 6: 8, 7: 9, 8: 10, 9: 11}
    cost = cost_table.get(slot_level)
    if cost is None:
        return False
    if character.sorcery_points < cost:
        return False
    if character.spellcasting is None:
        return False
    character.sorcery_points -= cost
    character.spellcasting.spell_slots[slot_level] = (
        character.spellcasting.spell_slots.get(slot_level, 0) + 1
    )
    return True


def convert_slot_to_points(
    character: Character, slot_level: int,
) -> bool:
    """PHB: convert spell slot → sorcery points (1 point per slot level)."""
    if character.spellcasting is None:
        return False
    if character.spellcasting.spell_slots.get(slot_level, 0) <= 0:
        return False
    character.spellcasting.spell_slots[slot_level] -= 1
    character.sorcery_points += slot_level
    return True


def recover_sorcery_on_long_rest(character: Character) -> int:
    """Long rest: recover all sorcery points."""
    if character.class_.lower() != "sorcerer":
        return 0
    before = character.sorcery_points
    character.sorcery_points = character.sorcery_points_max
    return character.sorcery_points - before


# ============================================================================
# Lay on Hands (Paladin PHB p. 84)
# ============================================================================


def lay_on_hands_pool_for(level: int) -> int:
    """PHB: pool = 5 × paladin level."""
    return 5 * max(0, level)


def spend_lay_on_hands(character: Character, hp: int) -> bool:
    """Spend ``hp`` from the Lay on Hands pool to heal. Returns success."""
    if character.lay_on_hands_pool < hp:
        return False
    if hp <= 0:
        return False
    character.lay_on_hands_pool -= hp
    return True


def heal_lay_on_hands(character: Character, target_hp: int) -> int:
    """Heal the character by ``target_hp`` (capped by pool). Returns HP healed."""
    healed = min(target_hp, character.lay_on_hands_pool)
    if healed > 0:
        spend_lay_on_hands(character, healed)
    return healed


# ============================================================================
# Second Wind (Fighter PHB p. 72)
# ============================================================================


def second_wind_max_for(level: int) -> int:
    """PHB: 1/short rest, 2 at L17+. The engine stores this in
    second_wind_used (bool flag, since 1 < 2 it's easier to track
    uses-remaining)."""
    if level >= 17:
        return 2
    return 1


def can_use_second_wind(character: Character) -> bool:
    return not character.second_wind_used


def roll_second_wind_heal(level: int, *, rng=None) -> int:
    """1d10 + fighter level."""
    from auto_dm.engine.dice import roll_dice
    import random
    rng = rng or random.Random()
    return roll_dice("1d10", rng=rng).total + level


def recover_second_wind_on_short_rest(character: Character) -> bool:
    if character.class_.lower() != "fighter":
        return False
    if not character.second_wind_used:
        return False
    character.second_wind_used = False
    return True


# ============================================================================
# Action Surge (Fighter PHB p. 72)
# ============================================================================


def action_surge_max_for(level: int) -> int:
    if level >= 17:
        return 2
    return 1


def action_surge(character: Character) -> bool:
    """Spend one action surge. Returns True if successful."""
    if character.action_surges_remaining <= 0:
        return False
    character.action_surges_remaining -= 1
    return True


def recover_action_surges_on_short_rest(character: Character, level: int) -> int:
    if character.class_.lower() != "fighter":
        return 0
    before = character.action_surges_remaining
    character.action_surges_remaining = action_surge_max_for(level)
    return character.action_surges_remaining - before


# ============================================================================
# Channel Divinity (Cleric PHB p. 58)
# ============================================================================


def channel_divinity_max_for(level: int) -> int:
    """PHB: 1/short, 2 at L18."""
    if level >= 18:
        return 2
    return 1


def use_channel_divinity(character: Character) -> bool:
    if character.channel_divinity_remaining <= 0:
        return False
    character.channel_divinity_remaining -= 1
    return True


def recover_channel_divinity_on_short_rest(character: Character, level: int) -> int:
    if character.class_.lower() != "cleric":
        return 0
    before = character.channel_divinity_remaining
    character.channel_divinity_remaining = channel_divinity_max_for(level)
    return character.channel_divinity_remaining - before


# ============================================================================
# Bardic Inspiration (Bard PHB p. 53)
# ============================================================================


def bardic_inspiration_die_for(level: int) -> int:
    """PHB: d6 (L1-4), d8 (L5-9), d10 (L10-14), d12 (L15+)."""
    if level >= 15:
        return 12
    if level >= 10:
        return 10
    if level >= 5:
        return 8
    return 6


def bardic_inspiration_max_for(charisma_mod: int) -> int:
    """Uses = CHA mod, min 1."""
    return max(1, charisma_mod)


def spend_bardic_inspiration(character: Character) -> bool:
    if character.bardic_inspiration_uses <= 0:
        return False
    character.bardic_inspiration_uses -= 1
    return True


def recover_bardic_on_short_rest(character: Character) -> int:
    """Short rest: reset uses (or long rest before Font of Inspiration L5)."""
    if character.class_.lower() != "bard":
        return 0
    before = character.bardic_inspiration_uses
    character.bardic_inspiration_uses = character.bardic_inspiration_max
    return character.bardic_inspiration_uses - before


# ============================================================================
# Generic: reset all "short rest" pools
# ============================================================================


def short_rest_recovery(character: Character) -> dict:
    """Apply short-rest recovery to all short-rest pools.

    Returns a dict of {pool_name: amount_recovered}.
    """
    result = {}
    result["ki"] = recover_ki_on_short_rest(character)
    if recover_second_wind_on_short_rest(character):
        result["second_wind"] = 1
    if character.class_.lower() == "fighter":
        result["action_surge"] = recover_action_surges_on_short_rest(
            character, character.level,
        )
    if character.class_.lower() == "cleric":
        result["channel_divinity"] = recover_channel_divinity_on_short_rest(
            character, character.level,
        )
    if character.class_.lower() == "wizard":
        # Arcane Recovery handled separately (it consumes the daily use)
        pass
    if character.class_.lower() == "bard":
        result["bardic_inspiration"] = recover_bardic_on_short_rest(character)
    return result


def long_rest_recovery(character: Character) -> dict:
    """Apply long-rest recovery to all long-rest pools."""
    result = {}
    # Ki: short rest recovery also happens on long
    result["ki"] = recover_ki_on_short_rest(character)
    # Sorcery
    result["sorcery_points"] = recover_sorcery_on_long_rest(character)
    # Lay on Hands: refill
    if character.class_.lower() == "paladin":
        before = character.lay_on_hands_pool
        character.lay_on_hands_pool = lay_on_hands_pool_for(character.level)
        result["lay_on_hands"] = character.lay_on_hands_pool - before
    # Second wind
    if recover_second_wind_on_short_rest(character):
        result["second_wind"] = 1
    # Action surge
    if character.class_.lower() == "fighter":
        result["action_surge"] = recover_action_surges_on_short_rest(
            character, character.level,
        )
    # Rages (delegated to rage.py normally)
    # Bardic inspiration
    if character.class_.lower() == "bard":
        result["bardic_inspiration"] = recover_bardic_on_short_rest(character)
    return result