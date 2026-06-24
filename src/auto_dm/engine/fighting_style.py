"""Fighting Style feature (PHB p. 72, Paladin p. 84, Ranger p. 91).

Each character picks one of these at class level that grants the level
(Fighter L1, Paladin L2, Ranger L2):

  - archery: +2 attack with ranged weapons
  - defense: +1 AC when wearing armor
  - dueling: +2 damage with a one-handed melee weapon, no weapon in
             the other hand
  - great_weapon_fighting: reroll 1s and 2s on damage dice for
             two-handed or versatile melee weapons (taken in 2H grip)
  - protection: reaction to impose disadvantage on a melee attack
             against an ally within 5 ft (shield required)
  - two_weapon_fighting: add ability modifier to off-hand damage

Each is a small helper that returns a modifier given the current
combat context. The combat pipeline calls them at the right point.
"""
from __future__ import annotations

from auto_dm.state.models import Character


FIGHTING_STYLES = frozenset({
    "archery", "defense", "dueling",
    "great_weapon_fighting", "protection", "two_weapon_fighting",
})


def is_ranged_weapon(weapon) -> bool:
    """True if ``weapon`` is a ranged weapon (has range_normal)."""
    if weapon is None or weapon.weapon is None:
        return False
    return weapon.weapon.range_normal is not None


def is_two_handed_melee(weapon) -> bool:
    """True if ``weapon`` is two-handed or used two-handed (versatile in
    2H grip)."""
    if weapon is None or weapon.weapon is None:
        return False
    wp = weapon.weapon
    return wp.two_handed or wp.heavy  # heavy implies 2H typically


def has_shield(character: Character) -> bool:
    off = character.equipped.off_hand
    return off is not None and off.armor is not None and off.armor.is_shield


# ---------------------------------------------------------------------------
# Style bonuses (per-attack/per-damage)
# ---------------------------------------------------------------------------


def attack_bonus(character: Character, weapon) -> int:
    """Bonus (or penalty) to apply to the attack roll from fighting style.

    Returns:
      - archery: +2 with a ranged weapon
      - 0 otherwise
    """
    style = character.fighting_style
    if style == "archery" and is_ranged_weapon(weapon):
        return 2
    return 0


def damage_bonus(character: Character, weapon) -> int:
    """Bonus (or penalty) to apply to damage from fighting style.

    Returns:
      - dueling: +2 with a one-handed melee weapon and no other weapon
      - 0 otherwise
    """
    style = character.fighting_style
    if style == "dueling":
        if weapon is None or weapon.weapon is None:
            return 0
        wp = weapon.weapon
        # One-handed melee (not two_handed, not heavy, not ranged)
        if wp.two_handed or wp.heavy or wp.range_normal is not None:
            return 0
        # No weapon in other hand (shield or empty)
        off = character.equipped.off_hand
        if off is not None and off.weapon is not None:
            return 0  # dual wielding
        return 2
    return 0


def ac_bonus(character: Character) -> int:
    """Bonus to AC from fighting style.

    Returns:
      - defense: +1 when wearing armor
    """
    style = character.fighting_style
    if style == "defense":
        if character.equipped.armor is not None:
            return 1
    return 0


# ---------------------------------------------------------------------------
# Great Weapon Fighting: reroll 1s and 2s on damage
# ---------------------------------------------------------------------------


def reroll_damage_die(value: int, *, rng=None) -> int:
    """Reroll 1s and 2s once (PHB). For multi-die, the caller iterates."""
    if value in (1, 2):
        if rng is not None:
            return rng.randint(1, 8)
        import random
        return random.randint(1, 8)
    return value


def apply_gwf(rolls: list[int], *, rng=None) -> list[int]:
    """Apply Great Weapon Fighting to a list of damage die rolls."""
    return [reroll_damage_die(r, rng=rng) for r in rolls]


# ---------------------------------------------------------------------------
# Two-Weapon Fighting: add ability mod to off-hand
# ---------------------------------------------------------------------------


def off_hand_damage_modifier(character: Character) -> int:
    """Damage modifier to add to off-hand attack (normally just the
    ability modifier, no STR mod by default).

    Returns:
      - two_weapon_fighting: full ability modifier (no penalty)
      - 0 otherwise (PHB default: no modifier to off-hand)
    """
    style = character.fighting_style
    if style != "two_weapon_fighting":
        return 0
    # Use the same ability the main-hand attack used. We don't have
    # access to that here, so default to STR for melee off-hand.
    from auto_dm.state.models import Ability
    return character.abilities.modifier(Ability.STR)


# ---------------------------------------------------------------------------
# Protection: impose disadvantage (called by reaction handler)
# ---------------------------------------------------------------------------


def can_use_protection(character: Character) -> bool:
    """A character with the Protection style can use it only if they
    have a shield and are within 5 ft of the ally being attacked."""
    if character.fighting_style != "protection":
        return False
    return has_shield(character)