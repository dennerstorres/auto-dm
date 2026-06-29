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
    load_backgrounds,
    load_classes,
    load_conditions,
    load_diseases,
    load_gear,
    load_languages,
    load_magic_items,
    load_monsters,
    load_mounts,
    load_packs,
    load_poisons,
    load_races,
    load_spells,
    load_tools,
    load_traps,
    load_vehicles,
    load_weapons,
)
from auto_dm.phb.models import (
    Background,
    MagicItem,
    MagicItemType,
    Monster,
    Mount,
    PHBArmor,
    PHBCondition,
    PHBDisease,
    PHBEquipmentPack,
    PHBGear,
    PHBLanguage,
    PHBPoison,
    PHBSpell,
    PHBTool,
    PHBTrap,
    PHBWeapon,
    CharacterClass,
    Race,
    Rarity,
    Subclass,
    ToolCategory,
    GearCategory,
    Vehicle,
    VehicleType,
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
    global _monsters_cache
    global _backgrounds_cache, _tools_cache, _gear_cache, _packs_cache
    global _magic_items_cache, _mounts_cache, _vehicles_cache
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
    _monsters_cache = None
    _backgrounds_cache = None
    _tools_cache = None
    _gear_cache = None
    _packs_cache = None
    _magic_items_cache = None
    _mounts_cache = None
    _vehicles_cache = None


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
_monsters_cache: list[Monster] | None = None
_backgrounds_cache: list[Background] | None = None
_tools_cache: list[PHBTool] | None = None
_gear_cache: list[PHBGear] | None = None
_packs_cache: list[PHBEquipmentPack] | None = None
_magic_items_cache: list[MagicItem] | None = None
_mounts_cache: list[Mount] | None = None
_vehicles_cache: list[Vehicle] | None = None


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
# Subclasses (Phase 25b)
# ============================================================================


def get_subclasses_for(class_name: str) -> list[Subclass]:
    """Return all subclasses for a class (e.g. all Wizard schools).

    Empty list if the class doesn't exist or has no subclasses (Warlock
    Pact Boons etc. are tracked elsewhere).
    """
    cls = get_class(class_name)
    if cls is None:
        return []
    return list(cls.subclasses)


def get_subclass(class_name: str, subclass_name: str) -> Subclass | None:
    """Look up a specific subclass of a class (case-insensitive).

    Examples::

        get_subclass("Wizard", "School of Evocation")
        get_subclass("barbarian", "path of the berserker")
    """
    for sub in get_subclasses_for(class_name):
        if sub.name.strip().lower() == subclass_name.strip().lower():
            return sub
    return None


def get_all_subclasses() -> list[Subclass]:
    """Flatten all subclasses across every class (cached once per call)."""
    result: list[Subclass] = []
    for cls in get_classes():
        result.extend(cls.subclasses)
    return result


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


# ============================================================================
# Monsters
# ============================================================================


def get_monsters(
    *,
    cr_min: float | None = None,
    cr_max: float | None = None,
    type_: str | None = None,
    environment: str | None = None,
) -> list[Monster]:
    """Return all loaded monsters (cached), optionally filtered.

    Filters are AND-combined and all optional:
        cr_min, cr_max: inclusive challenge-rating range (use 0.25 for "1/4").
        type_: creature type slug ("dragon", "humanoid", "fiend", ...).
        environment: reserved for future filtering by environment tag
            (the PHB stat blocks don't carry environment tags, so this
            currently matches nothing — included for forward compatibility).
    """
    monsters = _all_monsters()
    if cr_min is not None:
        monsters = [m for m in monsters if m.challenge_rating >= cr_min]
    if cr_max is not None:
        monsters = [m for m in monsters if m.challenge_rating <= cr_max]
    if type_ is not None:
        type_lower = type_.strip().lower()
        monsters = [m for m in monsters if m.type.value == type_lower]
    if environment is not None:
        # Placeholder: PHB monsters don't carry environment tags in their
        # stat blocks. Filtering by environment is a no-op for now.
        pass
    return monsters


def get_monster(name: str) -> Monster | None:
    """Look up a monster by name (case-insensitive, partial match supported)."""
    return _by_name_partial(_all_monsters(), name)


def _all_monsters() -> list[Monster]:
    """Return the full cached monster list (no filters)."""
    global _monsters_cache
    if _monsters_cache is None:
        _monsters_cache = load_monsters(get_phb_root())
    return _monsters_cache


# ============================================================================
# Backgrounds (Phase 25c)
# ============================================================================


def get_backgrounds() -> list[Background]:
    """Return all loaded backgrounds (cached)."""
    global _backgrounds_cache
    if _backgrounds_cache is None:
        _backgrounds_cache = load_backgrounds(get_phb_root())
    return _backgrounds_cache


def get_background(name: str) -> Background | None:
    """Look up a background by name (case-insensitive)."""
    return _by_name(get_backgrounds(), name)


# ============================================================================
# Tools (Phase 25c)
# ============================================================================


def get_tools(category: ToolCategory | None = None) -> list[PHBTool]:
    """Return all tools, optionally filtered by category."""
    tools = _all_tools()
    if category is not None:
        tools = [t for t in tools if t.category == category]
    return tools


def get_tool(name: str) -> PHBTool | None:
    """Look up a tool by name (case-insensitive)."""
    return _by_name(get_tools(), name)


def _all_tools() -> list[PHBTool]:
    """Return the full cached tool list (no filters)."""
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = load_tools(get_phb_root())
    return _tools_cache


# ============================================================================
# Adventuring Gear (Phase 25c)
# ============================================================================


def get_gear(category: GearCategory | None = None) -> list[PHBGear]:
    """Return all gear, optionally filtered by category."""
    gear = _all_gear()
    if category is not None:
        gear = [g for g in gear if g.category == category]
    return gear


def get_gear_item(name: str) -> PHBGear | None:
    """Look up a gear item by name (case-insensitive)."""
    return _by_name(get_gear(), name)


def _all_gear() -> list[PHBGear]:
    """Return the full cached gear list (no filters)."""
    global _gear_cache
    if _gear_cache is None:
        _gear_cache = load_gear(get_phb_root())
    return _gear_cache


# ============================================================================
# Equipment Packs (Phase 25c)
# ============================================================================


def get_packs() -> list[PHBEquipmentPack]:
    """Return all equipment packs (cached)."""
    global _packs_cache
    if _packs_cache is None:
        _packs_cache = load_packs(get_phb_root())
    return _packs_cache


def get_pack(name: str) -> PHBEquipmentPack | None:
    """Look up a pack by name (case-insensitive)."""
    return _by_name(get_packs(), name)


# ============================================================================
# Magic Items (Phase 25d)
# ============================================================================


# DMG p. 144 — "Magic Item Award" by encounter / hoard tier. The keys
# are the (approximate) challenge / tier; values are rarity weights.
# Used by roll_magic_item(CR) for a best-effort random selection.
_LOOT_TABLE_RARITIES: list[tuple[float, list[Rarity]]] = [
    # CR 0-4: mostly common/uncommon
    (5.0, [Rarity.COMMON, Rarity.COMMON, Rarity.UNCOMMON]),
    # CR 5-10: uncommon/rare
    (11.0, [Rarity.COMMON, Rarity.UNCOMMON, Rarity.UNCOMMON, Rarity.RARE]),
    # CR 11-16: rare/very rare
    (17.0, [Rarity.UNCOMMON, Rarity.RARE, Rarity.RARE, Rarity.VERY_RARE]),
    # CR 17+: very rare / legendary
    (float("inf"), [Rarity.RARE, Rarity.VERY_RARE, Rarity.VERY_RARE, Rarity.LEGENDARY]),
]


def get_magic_items(
    rarity: Rarity | None = None,
    item_type: MagicItemType | None = None,
) -> list[MagicItem]:
    """Return all magic items, optionally filtered by rarity and/or type."""
    items = _all_magic_items()
    if rarity is not None:
        items = [m for m in items if m.rarity == rarity]
    if item_type is not None:
        items = [m for m in items if m.item_type == item_type]
    return items


def get_magic_item(name: str) -> MagicItem | None:
    """Look up a magic item by name (case-insensitive, partial match)."""
    return _by_name_partial(get_magic_items(), name)


def _all_magic_items() -> list[MagicItem]:
    """Return the full cached magic-item list (no filters)."""
    global _magic_items_cache
    if _magic_items_cache is None:
        _magic_items_cache = load_magic_items(get_phb_root())
    return _magic_items_cache


def roll_magic_item(encounter_cr: float) -> MagicItem | None:
    """Pick a random magic item appropriate for an encounter of ``encounter_cr``.

    Uses the DMG rarity-by-tier table (simplified). Returns ``None`` if
    no items match the chosen rarity.
    """
    import random as _random

    rarity_pool: list[Rarity] = [Rarity.COMMON]
    for max_cr, pool in _LOOT_TABLE_RARITIES:
        if encounter_cr <= max_cr:
            rarity_pool = pool
            break

    chosen_rarity = _random.choice(rarity_pool)
    candidates = get_magic_items(rarity=chosen_rarity)
    if not candidates:
        return None
    return _random.choice(candidates)


# ============================================================================
# Mounts (Phase 25e)
# ============================================================================


def get_mounts() -> list[Mount]:
    """Return all loaded mounts (cached)."""
    global _mounts_cache
    if _mounts_cache is None:
        _mounts_cache = load_mounts(get_phb_root())
    return _mounts_cache


def get_mount(name: str) -> Mount | None:
    """Look up a mount by name (case-insensitive, partial match)."""
    return _by_name_partial(get_mounts(), name)


# ============================================================================
# Vehicles (Phase 25e)
# ============================================================================


def get_vehicles(vehicle_type: VehicleType | None = None) -> list[Vehicle]:
    """Return all loaded vehicles, optionally filtered by type (land/water)."""
    vehicles = _all_vehicles()
    if vehicle_type is not None:
        vehicles = [v for v in vehicles if v.vehicle_type == vehicle_type]
    return vehicles


def get_vehicle(name: str) -> Vehicle | None:
    """Look up a vehicle by name (case-insensitive, partial match)."""
    return _by_name_partial(get_vehicles(), name)


def _all_vehicles() -> list[Vehicle]:
    """Return the full cached vehicle list (no filters)."""
    global _vehicles_cache
    if _vehicles_cache is None:
        _vehicles_cache = load_vehicles(get_phb_root())
    return _vehicles_cache