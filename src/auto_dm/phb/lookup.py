"""Lazy, cached lookup API for PHB content.

The first call to any loader walks the PHB markdown files; subsequent
calls return the cached result. The cache is keyed on the PHB root path
so tests can override the location via ``set_phb_root()``.

Usage:
    from auto_dm.phb import get_race, get_spell, get_weapon

    dwarf = get_race("Dwarf")
    if dwarf:
        for trait in dwarf.traits:
            print(trait.name)
"""
from __future__ import annotations

from pathlib import Path

from auto_dm.phb.loader import (
    load_armor,
    load_classes,
    load_conditions,
    load_diseases,
    load_languages,
    load_poisons,
    load_races,
    load_spells,
    load_traps,
    load_weapons,
)
from auto_dm.phb.models import (
    PHBArmor,
    PHBCondition,
    PHBDisease,
    PHBLanguage,
    PHBPoison,
    PHBSpell,
    PHBTrap,
    PHBWeapon,
    CharacterClass,
    Race,
)


# ============================================================================
# Root path resolution
# ============================================================================

_DEFAULT_PHB_ROOT = Path(__file__).resolve().parents[3] / "data" / "phb"

_phb_root: Path | None = None


def get_phb_root() -> Path:
    """Return the configured PHB root, falling back to the default."""
    global _phb_root
    if _phb_root is None:
        _phb_root = _DEFAULT_PHB_ROOT
    return _phb_root


def set_phb_root(path: Path) -> None:
    """Override the PHB root and clear caches.

    Primarily for tests with fixture PHB trees.
    """
    global _phb_root
    _phb_root = path
    _clear_caches()


def _clear_caches() -> None:
    # Reset cache slots to None rather than deleting — the `global` name
    # must remain defined for subsequent calls to find it.
    global _races_cache, _classes_cache, _spells_cache
    global _weapons_cache, _armor_cache, _conditions_cache
    global _languages_cache, _poisons_cache, _traps_cache, _diseases_cache
    _races_cache = None
    _classes_cache = None
    _spells_cache = None
    _weapons_cache = None
    _armor_cache = None
    _conditions_cache = None
    _languages_cache = None
    _poisons_cache = None
    _traps_cache = None
    _diseases_cache = None


# ============================================================================
# Cache slots
# ============================================================================

_races_cache: list[Race] | None = None
_classes_cache: list[CharacterClass] | None = None
_spells_cache: list[PHBSpell] | None = None
_weapons_cache: list[PHBWeapon] | None = None
_armor_cache: list[PHBArmor] | None = None
_conditions_cache: list[PHBCondition] | None = None
_languages_cache: list[PHBLanguage] | None = None
_poisons_cache: list[PHBPoison] | None = None
_traps_cache: list[PHBTrap] | None = None
_diseases_cache: list[PHBDisease] | None = None


# ============================================================================
# Internal helpers
# ============================================================================


def _by_name(items: list, name: str):
    """Find an item by exact name match (case-insensitive)."""
    target = name.strip().lower()
    for item in items:
        if getattr(item, "name", "").strip().lower() == target:
            return item
    return None


def _by_name_partial(items: list, name: str):
    """Find by exact name first, then partial / contains match (case-insensitive)."""
    target = name.strip().lower()
    # Exact match wins
    exact = _by_name(items, name)
    if exact is not None:
        return exact
    # Fall back to substring match (target contained in name)
    for item in items:
        if target in getattr(item, "name", "").strip().lower():
            return item
    return None


# ============================================================================
# Races
# ============================================================================


def get_races() -> list[Race]:
    """Return all loaded races (cached)."""
    global _races_cache
    if _races_cache is None:
        _races_cache = load_races(get_phb_root())
    return _races_cache


def get_race(name: str) -> Race | None:
    """Look up a race by name (case-insensitive)."""
    return _by_name(get_races(), name)


# ============================================================================
# Classes
# ============================================================================


def get_classes() -> list[CharacterClass]:
    """Return all loaded classes (cached)."""
    global _classes_cache
    if _classes_cache is None:
        _classes_cache = load_classes(get_phb_root())
    return _classes_cache


def get_class(name: str) -> CharacterClass | None:
    """Look up a class by name (case-insensitive)."""
    return _by_name(get_classes(), name)


# ============================================================================
# Spells
# ============================================================================


def get_spells() -> list[PHBSpell]:
    """Return all loaded spells (cached)."""
    global _spells_cache
    if _spells_cache is None:
        _spells_cache = load_spells(get_phb_root())
    return _spells_cache


def get_spell(name: str) -> PHBSpell | None:
    """Look up a spell by name (case-insensitive)."""
    return _by_name(get_spells(), name)


def get_spells_for_class(class_name: str) -> list[PHBSpell]:
    """Return all spells on a given class's list."""
    target = class_name.strip().lower()
    return [s for s in get_spells() if target in [c.lower() for c in s.classes]]


# ============================================================================
# Equipment
# ============================================================================


def get_weapons() -> list[PHBWeapon]:
    """Return all loaded weapons (cached)."""
    global _weapons_cache
    if _weapons_cache is None:
        _weapons_cache = load_weapons(get_phb_root())
    return _weapons_cache


def get_weapon(name: str) -> PHBWeapon | None:
    """Look up a weapon by name (case-insensitive, partial match supported)."""
    return _by_name_partial(get_weapons(), name)


def get_armor_list() -> list[PHBArmor]:
    """Return all loaded armor (cached)."""
    global _armor_cache
    if _armor_cache is None:
        _armor_cache = load_armor(get_phb_root())
    return _armor_cache


def get_armor(name: str) -> PHBArmor | None:
    """Look up armor by name (case-insensitive, partial match supported)."""
    return _by_name_partial(get_armor_list(), name)


# ============================================================================
# Conditions
# ============================================================================


def get_conditions() -> list[PHBCondition]:
    """Return all loaded conditions (cached)."""
    global _conditions_cache
    if _conditions_cache is None:
        _conditions_cache = load_conditions(get_phb_root())
    return _conditions_cache


def get_condition(name: str) -> PHBCondition | None:
    """Look up a condition by name (case-insensitive)."""
    return _by_name(get_conditions(), name)


# ============================================================================
# Languages
# ============================================================================


def get_languages() -> list[PHBLanguage]:
    """Return all loaded languages (cached)."""
    global _languages_cache
    if _languages_cache is None:
        _languages_cache = load_languages(get_phb_root())
    return _languages_cache


def get_language(name: str) -> PHBLanguage | None:
    """Look up a language by name (case-insensitive)."""
    return _by_name(get_languages(), name)


# ============================================================================
# Poisons, Traps, Diseases
# ============================================================================


def get_poisons() -> list[PHBPoison]:
    """Return all loaded poisons (cached)."""
    global _poisons_cache
    if _poisons_cache is None:
        _poisons_cache = load_poisons(get_phb_root())
    return _poisons_cache


def get_poison(name: str) -> PHBPoison | None:
    """Look up a poison by name (case-insensitive, partial match supported)."""
    return _by_name_partial(get_poisons(), name)


def get_traps() -> list[PHBTrap]:
    """Return all loaded traps (cached)."""
    global _traps_cache
    if _traps_cache is None:
        _traps_cache = load_traps(get_phb_root())
    return _traps_cache


def get_trap(name: str) -> PHBTrap | None:
    """Look up a trap by name (case-insensitive, partial match supported)."""
    return _by_name_partial(get_traps(), name)


def get_diseases() -> list[PHBDisease]:
    """Return all loaded diseases (cached)."""
    global _diseases_cache
    if _diseases_cache is None:
        _diseases_cache = load_diseases(get_phb_root())
    return _diseases_cache


def get_disease(name: str) -> PHBDisease | None:
    """Look up a disease by name (case-insensitive)."""
    return _by_name(get_diseases(), name)