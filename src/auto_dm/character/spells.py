"""Spell selection for spellcasting classes.

D&D 5e has three caster models:

1. **Prepared casters** (Cleric, Druid, Paladin, Wizard): after each long
   rest, pick a number of spells from your class's full spell list to
   prepare. Wizard is special — uses a spellbook (limited + learnable).

2. **Known casters** (Bard, Sorcerer, Warlock): have a small fixed list
   of spells known that grows with level.

3. **Half-casters** (Paladin, Ranger — already in PHB at L2): same as
   prepared casters but with fewer slots.

This module provides:
- ``select_cantrips(class, level, n)``: pick N cantrip names from the
  class's list.
- ``prepare_caster_spells(class, level, casting_ability, mod, prof_bonus)``:
  returns a SpellSelection with the right spell_slots, save DC, and
  attack bonus. Spell lists are populated by the caller (the LLM-driven
  flow or the CLI prompts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from auto_dm.phb import CharacterClass, get_spells_for_class
from auto_dm.state.models import AbilityScores, Spellcasting


# ============================================================================
# Spell slot tables (PHB, levels 1-20)
# ============================================================================

# Full casters (Bard, Cleric, Druid, Sorcerer, Wizard) share the same
# progression per PHB p. 113.
_FULL_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1:  {1: 2},
    2:  {1: 3},
    3:  {1: 4, 2: 2},
    4:  {1: 4, 2: 3},
    5:  {1: 4, 2: 3, 3: 2},
    6:  {1: 4, 2: 3, 3: 3},
    7:  {1: 4, 2: 3, 3: 3, 4: 1},
    8:  {1: 4, 2: 3, 3: 3, 4: 2},
    9:  {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

# Half casters (Paladin, Ranger) per PHB p. 113.
_HALF_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1:  {},
    2:  {1: 2},
    3:  {1: 3},
    4:  {1: 3},
    5:  {1: 4, 2: 2},
    6:  {1: 4, 2: 2},
    7:  {1: 4, 2: 3},
    8:  {1: 4, 2: 3},
    9:  {1: 4, 2: 3, 3: 2},
    10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3},
    12: {1: 4, 2: 3, 3: 3},
    13: {1: 4, 2: 3, 3: 3, 4: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2},
    16: {1: 4, 2: 3, 3: 3, 4: 2},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}

# Warlock Pact Magic per PHB p. 107 — fixed slot count, all at the same level.
# Format: {character_level: (slot_count, slot_level)}
_WARLOCK_PACT_MAGIC: dict[int, tuple[int, int]] = {
    1:  (1, 1),
    2:  (2, 1),
    3:  (2, 2),
    4:  (2, 2),
    5:  (2, 3),
    6:  (2, 3),
    7:  (2, 4),
    8:  (2, 4),
    9:  (2, 5),
    10: (2, 5),
    11: (3, 5),
    12: (3, 5),
    13: (3, 5),
    14: (3, 5),
    15: (3, 5),
    16: (3, 5),
    17: (4, 5),
    18: (4, 5),
    19: (4, 5),
    20: (4, 5),
}

# Format: {class -> {level -> {slot_level: count}}}
# Half casters (Paladin, Ranger) only get slots at L2+.
_SPELL_SLOTS: dict[str, dict[int, dict[int, int]]] = {
    "bard":     _FULL_CASTER_SLOTS,
    "cleric":   _FULL_CASTER_SLOTS,
    "druid":    _FULL_CASTER_SLOTS,
    "sorcerer": _FULL_CASTER_SLOTS,
    "wizard":   _FULL_CASTER_SLOTS,
    "paladin":  _HALF_CASTER_SLOTS,
    "ranger":   _HALF_CASTER_SLOTS,
    # Warlock is special-cased: all slots are the same level (Pact Magic).
    "warlock":  {
        level: {slot_lvl: count}
        for level, (count, slot_lvl) in _WARLOCK_PACT_MAGIC.items()
    },
}

# Cantrips known by class and level (PHB class tables).
def _build_cantrips_by_class(thresholds: list[tuple[int, int]]) -> dict[int, int]:
    """Helper: build a {level: cantrips} map from a list of (level, n) gates.

    L1 to L20 inclusive; later gates overwrite earlier ones.
    """
    out: dict[int, int] = {}
    for lvl in range(1, 21):
        n = 0
        for gate_level, gate_n in thresholds:
            if lvl >= gate_level:
                n = gate_n
        out[lvl] = n
    return out


def _spells_known_bard() -> dict[int, int]:
    """Bard spells known per PHB p. 53."""
    return {
        1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14,
        11: 15, 12: 15, 13: 16, 14: 18, 15: 19, 16: 19, 17: 20, 18: 22,
        19: 22, 20: 22,
    }


def _spells_known_sorcerer() -> dict[int, int]:
    """Sorcerer spells known per PHB p. 101."""
    return {
        1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11,
        11: 12, 12: 12, 13: 13, 14: 13, 15: 14, 16: 14, 17: 15, 18: 15,
        19: 15, 20: 15,
    }


def _spells_known_warlock() -> dict[int, int]:
    """Warlock spells known per PHB p. 107."""
    return {
        1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 10,
        11: 11, 12: 11, 13: 12, 14: 12, 15: 13, 16: 13, 17: 14, 18: 14,
        19: 15, 20: 15,
    }


_CANTRIPS_KNOWN: dict[str, dict[int, int]] = {
    "bard":     _build_cantrips_by_class([(1, 2), (4, 3), (10, 4)]),
    "cleric":   _build_cantrips_by_class([(1, 3), (4, 4), (10, 5)]),
    "druid":    _build_cantrips_by_class([(1, 2), (4, 3), (10, 4)]),
    "sorcerer": _build_cantrips_by_class([(1, 4), (4, 5), (10, 6)]),
    "warlock":  _build_cantrips_by_class([(1, 2), (4, 3), (10, 4)]),
    "wizard":   _build_cantrips_by_class([(1, 3), (4, 4), (10, 5)]),
}

# Spells known (for "known" casters: bard, sorcerer, warlock)
# Prepared casters (cleric, druid, paladin, wizard) compute prepared count
# from casting ability mod + level (wizard from spellbook size).
# PHB Bard / Sorcerer / Warlock class tables.
_SPELLS_KNOWN: dict[str, dict[int, int]] = {
    "bard":     _spells_known_bard(),
    "sorcerer": _spells_known_sorcerer(),
    "warlock":  _spells_known_warlock(),
}

# Spellbook size for Wizard (always full class list, but only N prepared
# from it: INT mod + wizard level, min 1). +2 spells per level beyond L1.
_WIZARD_SPELLBOOK: dict[int, int] = {
    lvl: 6 + 2 * (lvl - 1) for lvl in range(1, 21)
}


# ============================================================================
# Spell selection result
# ============================================================================


@dataclass
class SpellSelection:
    """The choices a caster made during character creation."""

    cantrips: list[str] = field(default_factory=list)
    spells_known: list[str] = field(default_factory=list)  # for known casters
    spells_prepared: list[str] = field(default_factory=list)  # for prepared casters
    spellbook: list[str] = field(default_factory=list)  # for wizard

    def to_spellcasting(
        self,
        char_class: CharacterClass,
        abilities: AbilityScores,
        proficiency_bonus: int,
    ) -> Spellcasting:
        """Convert this selection + class info into a Spellcasting model."""
        if char_class.spellcasting is None:
            raise ValueError(f"{char_class.name} is not a spellcasting class")

        ability = char_class.spellcasting.ability
        casting_mod = abilities.modifier(ability)
        save_dc = 8 + proficiency_bonus + casting_mod
        attack_bonus = proficiency_bonus + casting_mod

        level = 1  # default; overridden by caller if known
        slots_max = get_spell_slots(char_class.name, level)
        spell_slots = dict(slots_max)

        return Spellcasting(
            ability=ability,
            save_dc=save_dc,
            attack_bonus=attack_bonus,
            cantrips_known=list(self.cantrips),
            spells_known=list(self.spells_known),
            spells_prepared=list(self.spells_prepared),
            spell_slots=spell_slots,
            spell_slots_max=spell_slots,
            concentration=None,
            ritual_casting=(char_class.name.lower() in {"cleric", "druid", "wizard"}),
        )


# ============================================================================
# Public API
# ============================================================================


def get_spell_slots(class_name: str, level: int) -> dict[int, int]:
    """Return {slot_level: count} for the class at the given level.

    Level must be 1-20 (full progression per PHB p. 113).
    """
    cls = class_name.strip().lower()
    if cls not in _SPELL_SLOTS:
        return {}
    per_level = _SPELL_SLOTS[cls]
    if level not in per_level:
        return {}
    return dict(per_level[level])


def get_cantrips_known(class_name: str, level: int) -> int:
    """Number of cantrips the class knows at the given level."""
    cls = class_name.strip().lower()
    return _CANTRIPS_KNOWN.get(cls, {}).get(level, 0)


def get_spells_known_max(class_name: str, level: int) -> int:
    """For known-casters: how many leveled spells they can know."""
    cls = class_name.strip().lower()
    return _SPELLS_KNOWN.get(cls, {}).get(level, 0)


def get_prepared_count(
    class_name: str, level: int, casting_ability_mod: int
) -> int:
    """For prepared casters: max prepared spells (casting mod + level, min 1).

    Wizard uses the same formula but selects from their spellbook.
    Paladin uses half-level (rounded down) + casting mod, min 1.
    """
    cls = class_name.strip().lower()
    if cls == "paladin":
        return max(1, casting_ability_mod + (level // 2))
    return max(1, casting_ability_mod + level)


def get_spellbook_size(class_name: str, level: int) -> int:
    """For Wizard: number of spells in their spellbook at this level."""
    cls = class_name.strip().lower()
    if cls != "wizard":
        return 0
    return _WIZARD_SPELLBOOK.get(level, 0)


def select_cantrips(
    char_class: CharacterClass, level: int, picks: list[str]
) -> list[str]:
    """Validate that ``picks`` is a valid cantrip selection for the class.

    Returns the validated list (also checks for duplicates).
    """
    if char_class.spellcasting is None:
        raise ValueError(f"{char_class.name} is not a spellcasting class")
    max_cantrips = get_cantrips_known(char_class.name, level)
    if len(picks) > max_cantrips:
        raise ValueError(
            f"{char_class.name} L{level} knows {max_cantrips} cantrips, "
            f"got {len(picks)}"
        )
    class_cantrips = {
        s.name
        for s in get_spells_for_class(char_class.name)
        if s.level == 0
    }
    if not class_cantrips:
        return list(picks)  # can't validate further; trust the caller
    for name in picks:
        if name not in class_cantrips:
            raise ValueError(
                f"{name!r} is not a cantrip on the {char_class.name} list"
            )
    if len(set(picks)) != len(picks):
        raise ValueError("Duplicate cantrip in selection")
    return list(picks)


def prepare_caster_spells(
    char_class: CharacterClass,
    level: int,
    abilities: AbilityScores,
    proficiency_bonus: int,
    *,
    cantrips: list[str],
    spells_known: Optional[list[str]] = None,
    spells_prepared: Optional[list[str]] = None,
    spellbook: Optional[list[str]] = None,
) -> SpellSelection:
    """Build a validated SpellSelection for a caster at creation.

    At least one of spells_known/spells_prepared/spellbook should be
    provided, depending on the caster type:

    - Bard / Sorcerer / Warlock: ``spells_known``
    - Cleric / Druid: ``spells_prepared``
    - Paladin: ``spells_prepared`` (half-level formula)
    - Wizard: ``spellbook`` + ``spells_prepared``
    """
    if char_class.spellcasting is None:
        raise ValueError(f"{char_class.name} is not a spellcasting class")

    cls = char_class.name.lower()

    # Cantrips
    cantrips = select_cantrips(char_class, level, cantrips)

    # Spell slots and casting mod
    casting_mod = abilities.modifier(char_class.spellcasting.ability)

    # Validate per-class rules
    if cls in _SPELLS_KNOWN:
        # Known caster
        max_known = get_spells_known_max(char_class.name, level)
        if spells_known is None:
            raise ValueError(f"{char_class.name} needs spells_known")
        if len(spells_known) > max_known:
            raise ValueError(
                f"{char_class.name} L{level} knows {max_known} spells, "
                f"got {len(spells_known)}"
            )
        # All must be on class list
        class_spell_names = {s.name for s in get_spells_for_class(char_class.name)}
        for name in spells_known:
            if class_spell_names and name not in class_spell_names:
                raise ValueError(
                    f"{name!r} is not on the {char_class.name} spell list"
                )
        return SpellSelection(
            cantrips=cantrips,
            spells_known=list(spells_known),
            spells_prepared=[],
            spellbook=[],
        )

    if cls in {"cleric", "druid", "paladin"}:
        # Prepared caster (full or half)
        max_prep = get_prepared_count(char_class.name, level, casting_mod)
        if spells_prepared is None:
            raise ValueError(f"{char_class.name} needs spells_prepared")
        if len(spells_prepared) > max_prep:
            raise ValueError(
                f"{char_class.name} L{level} can prepare {max_prep}, "
                f"got {len(spells_prepared)}"
            )
        return SpellSelection(
            cantrips=cantrips,
            spells_known=[],
            spells_prepared=list(spells_prepared),
            spellbook=[],
        )

    if cls == "wizard":
        max_book = get_spellbook_size(char_class.name, level)
        if spellbook is None:
            raise ValueError("Wizard needs spellbook")
        if len(spellbook) > max_book:
            raise ValueError(
                f"Wizard L{level} has {max_book} spells in book, "
                f"got {len(spellbook)}"
            )
        max_prep = get_prepared_count(char_class.name, level, casting_mod)
        prepared = list(spells_prepared or [])
        if len(prepared) > max_prep:
            raise ValueError(
                f"Wizard L{level} can prepare {max_prep}, got {len(prepared)}"
            )
        return SpellSelection(
            cantrips=cantrips,
            spells_known=[],
            spells_prepared=prepared,
            spellbook=list(spellbook),
        )

    raise ValueError(f"Unknown caster type: {char_class.name}")