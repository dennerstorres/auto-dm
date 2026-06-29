"""Class capstone & signature feature mechanics (Phase 25g).

This module owns the **runtime** behavior of L17-L20 capstones, the
gating of which is in :mod:`auto_dm.character.level_up`. The split
mirrors spellcasting: level_up.py declares "what becomes active at
this level"; this module declares "what the active feature does".

Capstones covered (one per class, PHB L20 unless noted):

- Barbarian: Primal Champion (+4 STR/CON, +2 weapon damage)
- Bard: Words of Creation (no implementation; flag only)
- Cleric: Divine Intervention Improvement (no consumption)
- Druid: Archdruid (cast spells in Wild Shape)
- Fighter: covered by Extra Attack progression in ``engine/extra_attack``
- Monk: Perfect Self (4 ki refills all ki)
- Paladin: covered by L18 aura upgrades (in ``engine/defenses``)
- Ranger: Foe Slayer (+WIS to attack & damage, favored enemy only)
- Rogue: Stroke of Luck (turn miss into hit, succeed on failed check)
- Sorcerer: Arcane Apotheosis (sorcery cap 20, recover on short rest)
- Warlock: Eldritch Master (refuel all Pact Magic slots, 1/long rest)
- Wizard: Signature Spells (2 spells of 3rd level or lower, always
  prepared, free cast once per short rest)

The 9th-level spell unlock for full casters (L17+) and Warlock Mystic
Arcanum (L11+) live in :mod:`auto_dm.character.spells` and
:mod:`auto_dm.engine.spellcasting`.
"""
from __future__ import annotations

from auto_dm.state.models import Character


# ============================================================================
# Barbarian: Primal Champion
# ============================================================================


def primal_champion_damage_bonus(character: Character) -> int:
    """Barbarian L20 Primal Champion: +2 to weapon damage rolls."""
    return 2 if getattr(character, "has_primal_champion", False) else 0


# ============================================================================
# Wizard: Signature Spells
# ============================================================================


def choose_signature_spells(character: Character, spell_names: list[str]) -> None:
    """Select the wizard's two signature spells (3rd level or lower).

    Raises ValueError if not exactly 2, or if the wizard lacks
    Signature Spells, or if a chosen spell is unknown or >3rd level.
    Idempotent — replaces the current selection.
    """
    if not getattr(character, "has_signature_spells", False):
        raise ValueError(f"{character.name} has no Signature Spells feature.")
    if len(spell_names) != 2:
        raise ValueError(
            f"Wizard must pick exactly 2 signature spells, got {len(spell_names)}"
        )
    # Defer lookup until we import lazily (avoid circular import).
    from auto_dm.phb import get_spell

    for name in spell_names:
        sp = get_spell(name)
        if sp is None:
            raise ValueError(f"Unknown spell: {name!r}")
        if sp.level is None or sp.level < 1 or sp.level > 3:
            raise ValueError(
                f"Signature spell {name!r} must be 1st-3rd level (got {sp.level})."
            )
        cls_lower = character.class_.strip().lower()
        if cls_lower not in {c.lower() for c in sp.classes}:
            raise ValueError(
                f"{name!r} is not on the {character.class_} spell list."
            )
    character.signature_spell_names = list(spell_names)
    # Initialize use counters to 0 (will be set to 1 by reset_signature_spells).
    character.signature_spell_uses_remaining = {n: 0 for n in spell_names}


def reset_signature_spells(character: Character) -> None:
    """Refill signature spell uses (call on short rest)."""
    if not getattr(character, "has_signature_spells", False):
        return
    character.signature_spell_uses_remaining = {
        n: 1 for n in character.signature_spell_names
    }


def cast_signature_spell(character: Character, spell_name: str) -> bool:
    """Cast a signature spell without expending a slot.

    Returns True if cast was successful (use remaining), False otherwise.
    """
    if not getattr(character, "has_signature_spells", False):
        return False
    uses = character.signature_spell_uses_remaining
    if uses.get(spell_name, 0) <= 0:
        return False
    uses[spell_name] -= 1
    return True


def has_signature_spell(character: Character, spell_name: str) -> bool:
    """True if the spell is in the wizard's signature list."""
    return spell_name in getattr(character, "signature_spell_names", [])


# ============================================================================
# Sorcerer: Arcane Apotheosis
# ============================================================================


def arcane_apotheosis_sorcery_cap() -> int:
    """Sorcerer L20: cap on sorcery points becomes 20 (was 0 / no cap)."""
    return 20


def arcane_apotheosis_active(character: Character) -> bool:
    """True if the sorcerer has Arcane Apotheosis (L20)."""
    return getattr(character, "has_arcane_apotheosis", False)


# ============================================================================
# Druid: Archdruid
# ============================================================================


def can_cast_in_wild_shape(character: Character) -> bool:
    """Druid L20 Archdruid: can cast spells while in Wild Shape."""
    return getattr(character, "has_archdruid", False)


# ============================================================================
# Monk: Perfect Self
# ============================================================================


def perfect_self_active(character: Character) -> bool:
    """True if the monk has Perfect Self (L20)."""
    return getattr(character, "has_perfect_self", False)


def trigger_perfect_self(character: Character, ki_pool_field: str = "ki_points") -> bool:
    """Monk L20: spend 4 ki to recover all ki (once per short rest).

    The ``ki_pool_field`` argument lets the caller point at whatever
    attribute holds the monk's current ki (kept loose to avoid coupling
    this module to a particular Character field name).

    Returns True if Perfect Self was triggered, False if the monk has
    no Perfect Self, has used it, or doesn't have enough ki.
    """
    if not perfect_self_active(character):
        return False
    if getattr(character, "perfect_self_used", False):
        return False
    if not hasattr(character, ki_pool_field):
        return False
    if getattr(character, ki_pool_field) < 4:
        return False
    setattr(character, ki_pool_field, getattr(character, ki_pool_field) - 4)
    # Recover all ki — the caller is expected to know the max; for now
    # we set to a fixed cap (monk ki = level). The DM agent can also
    # pre-compute and call this with a custom field. Default max = level.
    max_ki = getattr(character, "level", 20)
    setattr(character, ki_pool_field, max_ki)
    character.perfect_self_used = True
    return True


# ============================================================================
# Ranger: Foe Slayer
# ============================================================================


def foe_slayer_active(character: Character) -> bool:
    """True if ranger has Foe Slayer (L20)."""
    return getattr(character, "has_foe_slayer", False)


def apply_foe_slayer_bonus(character: Character, favored: bool) -> tuple[int, int]:
    """Ranger L20 Foe Slayer: add WIS to attack and damage (once per turn,
    favored enemy only).

    Returns (attack_bonus, damage_bonus) — both 0 if Foe Slayer is not
    active, has been used this turn, or the target is not a favored
    enemy. The caller is expected to set ``foe_slayer_used_this_turn``
    after applying.
    """
    if not foe_slayer_active(character):
        return 0, 0
    if getattr(character, "foe_slayer_used_this_turn", False):
        return 0, 0
    if not favored:
        return 0, 0
    from auto_dm.state.models import Ability

    wis_mod = character.abilities.modifier(Ability.WIS)
    return wis_mod, wis_mod


# ============================================================================
# Rogue: Stroke of Luck
# ============================================================================


def stroke_of_luck_active(character: Character) -> bool:
    """True if rogue has Stroke of Luck (L20)."""
    return getattr(character, "has_stroke_of_luck", False)


def trigger_stroke_of_luck(character: Character) -> bool:
    """Rogue L20: turn a missed attack into a hit, or succeed on a
    failed ability check. Once per short rest.

    Returns True if Stroke of Luck was used, False if not available
    or already spent this rest.
    """
    if not stroke_of_luck_active(character):
        return False
    if character.stroke_of_luck_uses_remaining <= 0:
        return False
    character.stroke_of_luck_uses_remaining -= 1
    return True


def reset_stroke_of_luck(character: Character) -> None:
    """Refill Stroke of Luck use (on short rest)."""
    if stroke_of_luck_active(character):
        character.stroke_of_luck_uses_remaining = 1


# ============================================================================
# Warlock: Eldritch Master
# ============================================================================


def eldritch_master_active(character: Character) -> bool:
    """True if warlock has Eldritch Master (L20)."""
    return getattr(character, "has_eldritch_master", False)


def trigger_eldritch_master(character: Character) -> bool:
    """Warlock L20: refuel all Pact Magic slots (1/long rest).

    Returns True if used successfully, False if not available or
    already used today.
    """
    if not eldritch_master_active(character):
        return False
    if character.eldritch_master_used:
        return False
    if character.spellcasting is None:
        return False
    # Refill pact slots to max.
    from auto_dm.engine.spellcasting import refill_slots

    refill_slots(character.spellcasting)
    character.eldritch_master_used = True
    return True


# ============================================================================
# Cleric: Divine Intervention Improvement
# ============================================================================


def divine_intervention_no_consume(character: Character) -> bool:
    """Cleric L20: Divine Intervention doesn't expend the daily use."""
    return getattr(character, "has_divine_intervention_improvement", False)


# ============================================================================
# Warlock: Mystic Arcanum
# ============================================================================


# (slot_level, character_level_required) — Warlock learns one of each.
WARLOCK_MYSTIC_ARCANUM_LEVELS: dict[int, int] = {
    6: 11,
    7: 13,
    8: 15,
    9: 17,
}


def can_learn_mystic_arcanum(character: Character, slot_level: int) -> bool:
    """True if the warlock's level unlocks a Mystic Arcanum spell of
    ``slot_level`` (6, 7, 8, or 9).
    """
    if slot_level not in WARLOCK_MYSTIC_ARCANUM_LEVELS:
        return False
    return character.level >= WARLOCK_MYSTIC_ARCANUM_LEVELS[slot_level]


def learn_mystic_arcanum(
    character: Character, slot_level: int, spell_name: str,
) -> None:
    """Pick a Mystic Arcanum spell of ``slot_level``.

    Idempotent — replaces any previous arcanum at the same level.
    Raises ValueError if the level isn't unlocked, or if the spell
    is not on the warlock list.
    """
    if not can_learn_mystic_arcanum(character, slot_level):
        raise ValueError(
            f"Warlock L{character.level} can't learn a "
            f"{slot_level}-level Mystic Arcanum."
        )
    from auto_dm.phb import get_spell

    sp = get_spell(spell_name)
    if sp is None:
        raise ValueError(f"Unknown spell: {spell_name!r}")
    cls_lower = character.class_.strip().lower()
    if cls_lower not in {c.lower() for c in sp.classes}:
        raise ValueError(
            f"{spell_name!r} is not on the {character.class_} spell list."
        )
    if sp.level != slot_level:
        raise ValueError(
            f"{spell_name!r} is level {sp.level}, not {slot_level}."
        )
    character.mystic_arcanum_known[slot_level] = spell_name


def cast_mystic_arcanum(character: Character, slot_level: int) -> bool:
    """Cast the warlock's Mystic Arcanum of ``slot_level`` (once per
    long rest per arcanum level).

    Returns True if cast, False if not configured or not available.
    The per-arcanum recovery happens via :func:`reset_mystic_arcanum`.
    """
    if not can_learn_mystic_arcanum(character, slot_level):
        return False
    arcanum = character.mystic_arcanum_known.get(slot_level)
    if not arcanum:
        return False
    uses = getattr(character, "mystic_arcanum_uses", None)
    if uses is None:
        uses = {}
        character.mystic_arcanum_uses = uses
    if uses.get(slot_level, 0) <= 0:
        return False
    uses[slot_level] -= 1
    return True


def reset_mystic_arcanum(character: Character) -> None:
    """Refill all Mystic Arcanum uses (on long rest)."""
    if not character.mystic_arcanum_known:
        return
    character.mystic_arcanum_uses = {
        lvl: 1 for lvl in character.mystic_arcanum_known
    }


# ============================================================================
# Capstone summary
# ============================================================================


def capstone_summary(character: Character) -> list[str]:
    """List of capstone feature names the character has active.

    Useful for /level-up narration and for DMs to know what's special
    about a high-level character.
    """
    cls = (character.class_ or "").strip().lower()
    out: list[str] = []
    if cls == "barbarian" and getattr(character, "has_primal_champion", False):
        out.append("Primal Champion")
    if cls == "cleric" and getattr(character, "has_divine_intervention_improvement", False):
        out.append("Divine Intervention Improvement")
    if cls == "druid" and getattr(character, "has_archdruid", False):
        out.append("Archdruid")
    if cls == "monk" and getattr(character, "has_perfect_self", False):
        out.append("Perfect Self")
    if cls == "ranger" and getattr(character, "has_foe_slayer", False):
        out.append("Foe Slayer")
    if cls == "rogue" and getattr(character, "has_stroke_of_luck", False):
        out.append("Stroke of Luck")
    if cls == "sorcerer" and getattr(character, "has_arcane_apotheosis", False):
        out.append("Arcane Apotheosis")
    if cls == "warlock" and getattr(character, "has_eldritch_master", False):
        out.append("Eldritch Master")
    if cls == "wizard" and getattr(character, "has_signature_spells", False):
        out.append("Signature Spells")
    return out
