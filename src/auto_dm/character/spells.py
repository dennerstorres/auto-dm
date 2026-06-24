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
# Spell slot tables (PHB, levels 1-5)
# ============================================================================

# Format: {class -> {level -> {slot_level: count}}}
# Half casters (Paladin, Ranger) only get slots at L2+.
_SPELL_SLOTS: dict[str, dict[int, dict[int, int]]] = {
    "bard": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "cleric": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "druid": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "sorcerer": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    "warlock": {
        1: {1: 1},
        2: {1: 2},
        3: {1: 2, 2: 2},
        4: {1: 3, 2: 2},
        5: {1: 3, 2: 2, 3: 1},
    },
    "wizard": {
        1: {1: 2},
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    },
    # Half casters (L2+)
    "paladin": {
        1: {},
        2: {1: 2},
        3: {1: 3},
        4: {1: 3},
        5: {1: 4, 2: 2},
    },
    "ranger": {
        1: {},
        2: {1: 2},
        3: {1: 3},
        4: {1: 3},
        5: {1: 4, 2: 2},
    },
}

# Cantrips known by class and level
_CANTRIPS_KNOWN: dict[str, dict[int, int]] = {
    "bard":     {1: 2, 2: 2, 3: 2, 4: 3, 5: 3},
    "cleric":   {1: 3, 2: 3, 3: 3, 4: 4, 5: 4},
    "druid":    {1: 2, 2: 2, 3: 2, 4: 3, 5: 3},
    "sorcerer": {1: 4, 2: 4, 3: 4, 4: 5, 5: 5},
    "warlock":  {1: 2, 2: 2, 3: 2, 4: 3, 5: 3},
    "wizard":   {1: 3, 2: 3, 3: 3, 4: 4, 5: 4},
}

# Spells known (for "known" casters: bard, sorcerer, warlock)
# Prepared casters (cleric, druid, paladin, wizard) compute prepared count
# from casting ability mod + level (wizard from spellbook size).
_SPELLS_KNOWN: dict[str, dict[int, int]] = {
    "bard":     {1: 4, 2: 5, 3: 6, 4: 7, 5: 8},
    "sorcerer": {1: 2, 2: 3, 3: 4, 4: 5, 5: 6},
    "warlock":  {1: 2, 2: 3, 3: 4, 4: 5, 5: 6},
}

# Spellbook size for Wizard (always full class list, but only N prepared
# from it: INT mod + wizard level, min 1).
_WIZARD_SPELLBOOK: dict[int, int] = {
    1: 6,  # +2 per wizard level beyond 1
    2: 8,
    3: 10,
    4: 12,
    5: 14,
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

    Level must be 1-5 (MVP).
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