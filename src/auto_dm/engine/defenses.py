"""Passive defense features: Unarmored Defense, Danger Sense, Evasion, Brutal Critical, Aura of Protection.

These are mostly read/calculated features — they don't consume resources
except where noted. They are integrated into ``attack_roll``,
``damage_roll``, and ``saving_throw`` in ``auto_dm.engine.combat``.
"""
from __future__ import annotations

from auto_dm.state.models import Ability, Character


# ============================================================================
# Unarmored Defense (Barbarian L1, Monk L1)
# ============================================================================


def unarmored_defense_ability(character: Character) -> Ability | None:
    """PHB: Barbarian uses DEX+CON, Monk uses DEX+WIS."""
    cls = character.class_.lower()
    if cls == "barbarian":
        return Ability.CON
    if cls == "monk":
        return Ability.WIS
    return None


def can_use_unarmored_defense(character: Character) -> bool:
    """Only if no armor is equipped."""
    armor = character.equipped.armor
    if armor is None:
        return True
    return False


def unarmored_defense_ac(character: Character) -> int:
    """PHB: 10 + DEX mod + (CON mod for barbarian, WIS mod for monk)."""
    if not can_use_unarmored_defense(character):
        return 0
    ability = unarmored_defense_ability(character)
    if ability is None:
        return 0
    dex_mod = character.abilities.modifier(Ability.DEX)
    other_mod = character.abilities.modifier(ability)
    return 10 + dex_mod + other_mod


# ============================================================================
# Danger Sense (Barbarian L2)
# ============================================================================


def has_danger_sense(character: Character) -> bool:
    return getattr(character, "has_danger_sense", False)


def danger_sense_grants_advantage(character: Character) -> bool:
    """PHB: advantage on DEX saves vs effects you can see."""
    return has_danger_sense(character)


# ============================================================================
# Evasion (Rogue L7, Monk L7)
# ============================================================================


def has_evasion(character: Character) -> bool:
    return getattr(character, "has_evasion", False)


def evasion_damage_multiplier(is_success: bool) -> float:
    """PHB: on DEX save success → 0; failure → half (0.5)."""
    if is_success:
        return 0.0
    return 0.5


# ============================================================================
# Brutal Critical (Barbarian L9+)
# ============================================================================


def brutal_critical_extra_dice(level: int) -> int:
    """PHB: +1 die at L9, +2 at L13, +3 at L17."""
    if level >= 17:
        return 3
    if level >= 13:
        return 2
    if level >= 9:
        return 1
    return 0


def has_brutal_critical(character: Character) -> bool:
    return (
        character.class_.lower() == "barbarian"
        and character.level >= 9
    )


# ============================================================================
# Aura of Protection (Paladin L6+)
# ============================================================================


def has_aura_of_protection(character: Character) -> bool:
    return getattr(character, "has_aura_of_protection", False)


def aura_of_protection_radius(character: Character) -> int:
    """PHB: 10 ft at L6, 30 ft at L18."""
    if not has_aura_of_protection(character):
        return 0
    if character.level >= 18:
        return 30
    return 10


def aura_of_protection_save_bonus(character: Character) -> int:
    """+CHA mod to saves for self and allies in range."""
    if not has_aura_of_protection(character):
        return 0
    return character.abilities.modifier(Ability.CHA)
