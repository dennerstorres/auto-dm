"""Spellcasting mechanics: slots, preparation, concentration, rituals.

This module is the **runtime** layer for spellcasting. ``character/spells.py``
handles character-creation selection (initial pick of known/prepared). This
module owns what happens *after* the game starts:

- ``consume_slot`` / ``refill_slots`` — daily resource bookkeeping
- ``concentration_save`` — break-on-damage rule (PHB p. 203)
- ``cast_as_ritual`` — extended casting time, no slot cost
- ``cast_spell`` — main entry point used by CombatEngine

PHB references are inline.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from auto_dm.character.spells import (
    get_prepared_count,
    get_spells_known_max,
    get_spell_slots,
)
from auto_dm.engine.combat import saving_throw
from auto_dm.phb import get_spell
from auto_dm.state.models import Ability, Character, Spellcasting

if TYPE_CHECKING:
    from auto_dm.state.models import NPC


# ============================================================================
# Spell slots
# ============================================================================


def available_slot_levels(spellcasting: Spellcasting) -> list[int]:
    """Slot levels with at least 1 slot remaining, in ascending order."""
    return sorted(
        lvl for lvl, remaining in spellcasting.spell_slots.items()
        if remaining > 0
    )


def has_slot(spellcasting: Spellcasting, level: int) -> bool:
    """True if the caster can cast a spell of ``level`` (allowing upcasting).

    A 3rd-level spell can be cast with any slot of level >= 3.
    """
    for lvl, remaining in spellcasting.spell_slots.items():
        if lvl >= level and remaining > 0:
            return True
    return False


def consume_slot(
    spellcasting: Spellcasting, level: int,
) -> int:
    """Consume a slot for a spell of ``level``. Returns the slot level used.

    Upcasting rule (PHB): if no slot of exactly ``level`` remains, consume
    the lowest available slot >= ``level``. Returns the actual level
    consumed (e.g. casting a 2nd-level spell with a 3rd-level slot
    returns 3).
    """
    # Prefer the exact level if available.
    if spellcasting.spell_slots.get(level, 0) > 0:
        spellcasting.spell_slots[level] -= 1
        return level
    # Upcast: lowest slot >= level with remaining.
    candidates = [
        lvl for lvl, remaining in spellcasting.spell_slots.items()
        if lvl >= level and remaining > 0
    ]
    if not candidates:
        raise ValueError(f"No spell slot available for level {level}")
    chosen = min(candidates)
    spellcasting.spell_slots[chosen] -= 1
    return chosen


def refill_slots(spellcasting: Spellcasting) -> None:
    """Restore all slots to their maximum. Called on long rest."""
    spellcasting.spell_slots = dict(spellcasting.spell_slots_max)


# ============================================================================
# Preparation & known
# ============================================================================


def can_prepare_count(
    character: Character, level: int | None = None,
) -> int:
    """Max number of spells the character can have prepared."""
    lvl = level if level is not None else character.level
    if character.spellcasting is None:
        return 0
    ability = character.spellcasting.ability
    mod = character.abilities.modifier(ability)
    return get_prepared_count(character.class_, lvl, mod)


def can_know_count(character: Character, level: int | None = None) -> int:
    """Max number of leveled spells the character can know."""
    lvl = level if level is not None else character.level
    return get_spells_known_max(character.class_, lvl)


def prepare_spell(character: Character, spell_name: str) -> None:
    """Add a spell to the character's prepared list.

    Validates against the class's spell list and the prepared-count cap.
    Idempotent: re-preparing an already prepared spell is a no-op.
    """
    if character.spellcasting is None:
        raise ValueError(f"{character.name} cannot cast spells")
    sc = character.spellcasting
    if spell_name in sc.spells_prepared:
        return  # already prepared
    spell = get_spell(spell_name)
    if spell is None:
        raise ValueError(f"Unknown spell: {spell_name!r}")
    if character.class_.lower() not in {c.lower() for c in spell.classes}:
        raise ValueError(
            f"{spell_name!r} is not on the {character.class_} spell list"
        )
    cap = can_prepare_count(character)
    if len(sc.spells_prepared) >= cap:
        raise ValueError(
            f"{character.name} can prepare {cap} spells "
            f"(already has {len(sc.spells_prepared)})"
        )
    sc.spells_prepared.append(spell_name)


def unprepare_spell(character: Character, spell_name: str) -> None:
    """Remove a spell from the character's prepared list."""
    if character.spellcasting is None:
        return
    if spell_name in character.spellcasting.spells_prepared:
        character.spellcasting.spells_prepared.remove(spell_name)


def can_cast_as_prepared(character: Character, spell_name: str) -> bool:
    """True if the character can cast ``spell_name`` from prepared list."""
    if character.spellcasting is None:
        return False
    return spell_name in character.spellcasting.spells_prepared


def can_cast_as_known(character: Character, spell_name: str) -> bool:
    """True if the character knows ``spell_name`` (for known casters)."""
    if character.spellcasting is None:
        return False
    return spell_name in character.spellcasting.spells_known


# ============================================================================
# Concentration
# ============================================================================


@dataclass
class ConcentrationResult:
    """Outcome of a concentration-saving throw when damaged."""

    broken: bool
    save_dc: int
    save_result: object = None  # SaveResult, but kept loose to avoid import cycle


def concentration_dc(damage: int) -> int:
    """PHB p. 203: DC = max(10, damage // 2)."""
    return max(10, damage // 2)


def break_concentration(character: Character) -> str | None:
    """Force-break any concentration. Returns the spell name that was dropped."""
    if character.spellcasting is None:
        return None
    prev = character.spellcasting.concentration
    character.spellcasting.concentration = None
    return prev


def start_concentration(character: Character, spell_name: str) -> None:
    """Begin concentrating on ``spell_name``.

    Raises ValueError if already concentrating on a different spell.
    Re-concentrating on the same spell is a no-op.
    """
    if character.spellcasting is None:
        raise ValueError(f"{character.name} cannot cast spells")
    sc = character.spellcasting
    if sc.concentration == spell_name:
        return  # already concentrating on this spell
    if sc.concentration is not None:
        raise ValueError(
            f"{character.name} is already concentrating on "
            f"{sc.concentration!r}; cannot also concentrate on "
            f"{spell_name!r}"
        )
    sc.concentration = spell_name


def concentration_save(
    character: Character,
    damage: int,
    *,
    rng: random.Random | None = None,
) -> ConcentrationResult:
    """Roll a concentration save after taking ``damage``.

    PHB p. 203: when a concentrating caster takes damage, they make a
    Constitution save vs DC = max(10, damage // 2). On a failure, the
    spell ends.

    No-op if the caster is not concentrating or takes no damage.
    """
    if character.spellcasting is None or character.spellcasting.concentration is None:
        return ConcentrationResult(broken=False, save_dc=0)
    if damage <= 0:
        return ConcentrationResult(broken=False, save_dc=0)

    dc = concentration_dc(damage)
    save = saving_throw(character, Ability.CON, dc, rng=rng)
    if not save.is_success:
        break_concentration(character)
        return ConcentrationResult(broken=True, save_dc=dc, save_result=save)
    return ConcentrationResult(broken=False, save_dc=dc, save_result=save)


# ============================================================================
# Ritual casting
# ============================================================================


def can_cast_as_ritual(character: Character, spell_name: str) -> tuple[bool, str]:
    """Check whether ``spell_name`` can be cast as a ritual.

    Returns (allowed, reason_if_not). The spell must:
      1. Be marked ``is_ritual`` in the PHB
      2. Be on the character's class list (known or prepared)
      3. The character must have ritual casting capability
    """
    if character.spellcasting is None:
        return False, "not a spellcaster"
    if not character.spellcasting.ritual_casting:
        return False, "class cannot cast rituals"
    spell = get_spell(spell_name)
    if spell is None:
        return False, "unknown spell"
    if not spell.is_ritual:
        return False, "spell is not a ritual"
    if character.class_.lower() not in {c.lower() for c in spell.classes}:
        return False, "spell not on class list"
    # Must be either known or prepared (and must be in *one* of those lists).
    if spell_name not in (
        character.spellcasting.spells_known
        + character.spellcasting.spells_prepared
        + character.spellcasting.spellbook
    ):
        return False, "spell must be known, prepared, or in spellbook"
    return True, ""


@dataclass
class RitualResult:
    """Result of casting a spell as a ritual."""

    success: bool
    spell_name: str
    reason: str = ""


def cast_as_ritual(character: Character, spell_name: str) -> RitualResult:
    """Cast a ritual spell — no slot consumed, but takes 10 minutes (PHB).

    Does NOT start concentration (the ritual mechanic is about extended
    casting time, not breaking the slot economy).
    """
    allowed, reason = can_cast_as_ritual(character, spell_name)
    if not allowed:
        return RitualResult(success=False, spell_name=spell_name, reason=reason)
    return RitualResult(success=True, spell_name=spell_name)


# ============================================================================
# cast_spell — the main entry point
# ============================================================================


@dataclass
class CastResult:
    """Outcome of casting a leveled spell in combat."""

    success: bool
    spell_name: str
    slot_level_used: int  # 0 for cantrips
    upcast: bool  # True if cast at a higher level than the spell's base
    target_ids: list[str] = field(default_factory=list)
    damage_dealt: dict[str, int] = field(default_factory=dict)  # target_id -> total
    healing_dealt: int = 0
    conditions_applied: dict[str, list[str]] = field(default_factory=dict)
    started_concentration: bool = False
    error: str = ""

    def __str__(self) -> str:
        if not self.success:
            return f"cast {self.spell_name} failed: {self.error}"
        bits = [f"cast {self.spell_name}"]
        if self.upcast:
            bits.append(f"at slot level {self.slot_level_used}")
        if self.damage_dealt:
            bits.append(f"damage={self.damage_dealt}")
        if self.healing_dealt:
            bits.append(f"heal={self.healing_dealt}")
        if self.conditions_applied:
            bits.append(f"conditions={self.conditions_applied}")
        if self.started_concentration:
            bits.append("concentration")
        return " | ".join(bits)


# PHB caster type metadata (used by cast_spell to decide preparation vs known)
_PREPARED_CASTERS = frozenset({"cleric", "druid", "paladin", "wizard"})
_KNOWN_CASTERS = frozenset({"bard", "sorcerer", "warlock", "ranger"})


def cast_spell(
    caster: Character,
    spell_name: str,
    *,
    slot_level: int | None = None,
    targets: list["NPC | Character"] | None = None,
    rng: random.Random | None = None,
) -> CastResult:
    """Cast a spell. Main entry point for ``ActionType.CAST_SPELL``.

    PHB rules:
      - Cantrips: free, at will, may scale with character level.
      - Leveled spells: consume a slot of the spell's level (or higher
        via upcasting). Cannot cast without an available slot.
      - Preparation: prepared casters must have the spell prepared;
        known casters must have it known.
      - Concentration: spell may start concentration; only one at a time.
      - Upcasting: if ``slot_level`` > spell level, the engine
        transparently consumes the higher slot.
    """
    rng = rng or random.Random()
    if caster.spellcasting is None:
        return CastResult(
            success=False, spell_name=spell_name, slot_level_used=0,
            upcast=False, error=f"{caster.name} cannot cast spells",
        )

    spell = get_spell(spell_name)
    if spell is None:
        return CastResult(
            success=False, spell_name=spell_name, slot_level_used=0,
            upcast=False, error=f"unknown spell {spell_name!r}",
        )

    # Class list check
    if caster.class_.lower() not in {c.lower() for c in spell.classes}:
        return CastResult(
            success=False, spell_name=spell_name, slot_level_used=0,
            upcast=False,
            error=f"{spell_name!r} not on {caster.class_} spell list",
        )

    # Cantrip: at-will, no slot
    if spell.is_cantrip:
        if spell_name not in caster.spellcasting.cantrips_known:
            return CastResult(
                success=False, spell_name=spell_name, slot_level_used=0,
                upcast=False, error=f"{spell_name!r} not known",
            )
        return CastResult(
            success=True, spell_name=spell_name, slot_level_used=0, upcast=False,
        )

    # Must be known (known casters) or prepared (prepared casters).
    cls = caster.class_.lower()
    if cls in _PREPARED_CASTERS:
        if not can_cast_as_prepared(caster, spell_name):
            return CastResult(
                success=False, spell_name=spell_name, slot_level_used=0,
                upcast=False, error=f"{spell_name!r} not prepared",
            )
    elif cls in _KNOWN_CASTERS:
        if not can_cast_as_known(caster, spell_name):
            return CastResult(
                success=False, spell_name=spell_name, slot_level_used=0,
                upcast=False, error=f"{spell_name!r} not known",
            )
    else:
        # Custom / non-standard caster: require prepared
        if not can_cast_as_prepared(caster, spell_name):
            return CastResult(
                success=False, spell_name=spell_name, slot_level_used=0,
                upcast=False, error=f"{spell_name!r} not prepared",
            )

    # Slot consumption (with upcasting)
    desired = slot_level if slot_level is not None else spell.level
    if desired < spell.level:
        return CastResult(
            success=False, spell_name=spell_name, slot_level_used=0,
            upcast=False,
            error=f"slot_level={desired} below spell level {spell.level}",
        )
    if not has_slot(caster.spellcasting, desired):
        return CastResult(
            success=False, spell_name=spell_name, slot_level_used=0,
            upcast=False, error=f"no slot available for level {desired}",
        )
    used_level = consume_slot(caster.spellcasting, desired)
    upcast = used_level > spell.level

    # Start concentration if applicable
    started_conc = False
    if spell.is_concentration:
        # If already concentrating on this same spell, no-op.
        if caster.spellcasting.concentration == spell_name:
            started_conc = False
        else:
            try:
                start_concentration(caster, spell_name)
                started_conc = True
            except ValueError:
                # Already concentrating on a different spell: the higher-priority
                # one wins; we report success but no new concentration starts.
                started_conc = False

    return CastResult(
        success=True,
        spell_name=spell_name,
        slot_level_used=used_level,
        upcast=upcast,
        target_ids=[t.id for t in (targets or [])],
        started_concentration=started_conc,
    )


# ============================================================================
# Helpers exposed for combat integration
# ============================================================================


def slot_levels_for_level(class_name: str, char_level: int) -> dict[int, int]:
    """Convenience wrapper around ``character.spells.get_spell_slots``."""
    return get_spell_slots(class_name, char_level)
