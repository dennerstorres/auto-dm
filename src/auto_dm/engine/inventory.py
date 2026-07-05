"""Inventory & equipment engine (Phase 39).

Pure state-mutating functions — no LLM, no I/O. The web layer calls
these and the DM only narrates the result.

Conventions (inherited from ``character/builder.py``):

- ``Character.inventory`` is the full list of possessions; equipped
  items STAY in the inventory. ``Character.equipped`` holds per-slot
  copies matched back to inventory entries by name.
- Stored ``Character.armor_class`` = armor base AC + capped DEX mod
  (or unarmored / Unarmored Defense) + 2 for a physical shield in the
  off hand. Magic +1/+2/+3 bonuses are NOT baked in — ``attack_roll``
  adds them dynamically from the equipped slots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from auto_dm.engine.defenses import unarmored_defense_ac
from auto_dm.state.models import (
    Ability,
    Character,
    Item,
    ItemType,
    NPC,
    ShopItem,
)

# PHB p. 138: a creature can be attuned to at most 3 magic items.
MAX_ATTUNED_ITEMS = 3

# PHB default: used equipment sells for half the listed price.
SELL_RATE = 0.5

# Default prices for magic items without an explicit value, scaled by
# rarity (SPEC §12.2 / DMG p. 130 ballpark).
RARITY_PRICE_GP: dict[str, float] = {
    "common": 100.0,
    "uncommon": 500.0,
    "rare": 5_000.0,
    "very_rare": 50_000.0,
    "legendary": 500_000.0,
}

EQUIP_SLOTS = (
    "main_hand", "off_hand", "armor",
    "amulet", "ring_1", "ring_2", "cloak", "boots",
)

# Slots that accept "wondrous"-style items (no weapon/armor properties).
_TRINKET_SLOTS = {"amulet", "ring_1", "ring_2", "cloak", "boots"}


@dataclass
class InventoryResult:
    """Outcome of an inventory operation.

    ``errors`` non-empty means the state was NOT mutated. ``warnings``
    flag legal-but-suboptimal choices (no proficiency, not attuned...)
    that the DM may want to narrate.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ac_before: int = 0
    ac_after: int = 0
    gold_gp: float = 0.0  # character's balance after the operation

    @property
    def ac_delta(self) -> int:
        return self.ac_after - self.ac_before


def _fail(*errors: str) -> InventoryResult:
    return InventoryResult(ok=False, errors=list(errors))


# ============================================================================
# Lookup helpers
# ============================================================================


def find_item(character: Character, name: str) -> Optional[Item]:
    """Find an inventory item by name (case-insensitive exact match)."""
    lowered = name.strip().lower()
    for item in character.inventory:
        if item.name.lower() == lowered:
            return item
    return None


def _is_stackable(item: Item) -> bool:
    """Weapons/armor are discrete objects; everything else stacks by name."""
    return item.weapon is None and item.armor is None


# ============================================================================
# Armor class recompute
# ============================================================================


def compute_armor_class(character: Character) -> int:
    """Recompute stored AC from equipped armor/shield (see module doc)."""
    dex_mod = character.abilities.modifier(Ability.DEX)
    armor_item = character.equipped.armor
    if armor_item is not None and armor_item.armor is not None:
        ap = armor_item.armor
        ac = ap.base_ac
        if ap.add_dex_modifier:
            capped = dex_mod
            if ap.max_dex_bonus is not None:
                capped = min(capped, ap.max_dex_bonus)
            ac += capped
    else:
        # Unarmored: 10 + DEX, or Unarmored Defense when better
        # (Barbarian DEX+CON / Monk DEX+WIS — the PHB lets you pick).
        ac = max(10 + dex_mod, unarmored_defense_ac(character))
    off = character.equipped.off_hand
    if off is not None and off.armor is not None and off.armor.is_shield:
        ac += 2
    return ac


def refresh_armor_class(character: Character) -> None:
    character.armor_class = compute_armor_class(character)


# ============================================================================
# Add / remove
# ============================================================================


def add_item(character: Character, item: Item, quantity: int = 1) -> InventoryResult:
    """Add an item (stacking consumables/gear by name)."""
    if quantity < 1:
        return _fail("quantity deve ser >= 1")
    existing = find_item(character, item.name)
    if existing is not None and _is_stackable(existing) and _is_stackable(item):
        existing.quantity += quantity
    else:
        new_item = item.model_copy(deep=True)
        new_item.quantity = quantity
        character.inventory.append(new_item)
    return InventoryResult(
        ok=True,
        ac_before=character.armor_class,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


def remove_item(character: Character, name: str, quantity: int = 1) -> InventoryResult:
    """Remove ``quantity`` of an item; unequips/unattunes when depleted."""
    if quantity < 1:
        return _fail("quantity deve ser >= 1")
    item = find_item(character, name)
    if item is None:
        return _fail(f"item não está no inventário: {name}")
    if quantity > item.quantity:
        return _fail(
            f"quantidade insuficiente de {item.name}: tem {item.quantity}, pediu {quantity}"
        )
    ac_before = character.armor_class
    item.quantity -= quantity
    if item.quantity == 0:
        character.inventory.remove(item)
        # Clear any equipped slot holding this item and drop attunement.
        for slot in EQUIP_SLOTS:
            equipped = getattr(character.equipped, slot)
            if equipped is not None and equipped.name.lower() == item.name.lower():
                setattr(character.equipped, slot, None)
        character.attuned_items = [
            n for n in character.attuned_items if n.lower() != item.name.lower()
        ]
        refresh_armor_class(character)
    return InventoryResult(
        ok=True,
        ac_before=ac_before,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


# ============================================================================
# Equip / unequip
# ============================================================================


def _slot_accepts(slot: str, item: Item) -> Optional[str]:
    """Return an error message if the item can't go in the slot."""
    is_shield = item.armor is not None and item.armor.is_shield
    is_body_armor = item.armor is not None and not is_shield
    is_weapon = item.weapon is not None
    if slot == "armor":
        if not is_body_armor:
            return f"{item.name} não é uma armadura"
    elif slot == "main_hand":
        if not is_weapon:
            return f"{item.name} não é uma arma"
    elif slot == "off_hand":
        if not (is_weapon or is_shield):
            return f"{item.name} não é arma nem escudo"
    elif slot in _TRINKET_SLOTS:
        if is_weapon or item.armor is not None:
            return f"{item.name} não pode ser equipado no slot {slot}"
    else:
        return f"slot inválido: {slot}"
    return None


def _proficiency_warnings(character: Character, slot: str, item: Item) -> list[str]:
    """Non-blocking proficiency checks derived from the PHB class table.

    5e doesn't forbid wearing gear without proficiency — it imposes
    penalties (disadvantage, no spellcasting) — so these are warnings.
    """
    from auto_dm.phb import get_armor, get_class, get_weapon

    warnings: list[str] = []
    cls = get_class(character.class_)
    if cls is None:
        return warnings
    if item.armor is not None:
        phb_armor = get_armor(item.name)
        if phb_armor is not None:
            profs = " ".join(cls.proficiencies.armor).lower()
            category = phb_armor.category.value  # light/medium/heavy/shield
            needed = "shields" if category == "shield" else f"{category} armor"
            if needed not in profs and "all armor" not in profs:
                warnings.append(
                    f"{character.name} não tem proficiência com {needed} "
                    f"({item.name}): desvantagem em testes, saves e ataques "
                    "de STR/DEX, e não pode conjurar magias"
                )
    elif item.weapon is not None:
        phb_weapon = get_weapon(item.name)
        if phb_weapon is not None:
            profs = " ".join(cls.proficiencies.weapons).lower()
            group = "simple" if phb_weapon.category.value.startswith("simple") else "martial"
            by_group = f"{group} weapons" in profs
            by_name = item.name.lower() in profs or f"{item.name.lower()}s" in profs
            if not (by_group or by_name):
                warnings.append(
                    f"{character.name} não tem proficiência com {item.name}: "
                    "não soma bônus de proficiência no ataque"
                )
    return warnings


def equip_item(character: Character, slot: str, item_name: str) -> InventoryResult:
    """Equip an inventory item into a slot, recomputing AC.

    The previous occupant simply stays in the inventory (equipping
    never removes items from it).
    """
    if slot not in EQUIP_SLOTS:
        return _fail(f"slot inválido: {slot}")
    item = find_item(character, item_name)
    if item is None:
        return _fail(f"item não está no inventário: {item_name}")
    slot_error = _slot_accepts(slot, item)
    if slot_error is not None:
        return _fail(slot_error)

    warnings = _proficiency_warnings(character, slot, item)
    if (
        slot == "off_hand"
        and item.weapon is not None
        and not item.weapon.light
    ):
        warnings.append(
            f"{item.name} não é leve: two-weapon fighting exige armas light (PHB p. 195)"
        )
    main = character.equipped.main_hand
    if (
        slot == "off_hand"
        and main is not None
        and main.weapon is not None
        and main.weapon.two_handed
    ):
        warnings.append(
            f"{main.name} exige as duas mãos: não é possível atacar com ela "
            f"enquanto {item.name} ocupa a mão inábil"
        )
    if item.requires_attunement and item.name not in character.attuned_items:
        warnings.append(
            f"{item.name} requer sintonização: os efeitos mágicos só valem após attune"
        )

    ac_before = character.armor_class
    setattr(character.equipped, slot, item)
    refresh_armor_class(character)
    return InventoryResult(
        ok=True,
        warnings=warnings,
        ac_before=ac_before,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


def unequip_item(character: Character, slot: str) -> InventoryResult:
    """Clear a slot (the item stays in the inventory), recomputing AC."""
    if slot not in EQUIP_SLOTS:
        return _fail(f"slot inválido: {slot}")
    if getattr(character.equipped, slot) is None:
        return _fail(f"slot já está vazio: {slot}")
    ac_before = character.armor_class
    setattr(character.equipped, slot, None)
    refresh_armor_class(character)
    return InventoryResult(
        ok=True,
        ac_before=ac_before,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


# ============================================================================
# Attunement (PHB p. 138)
# ============================================================================


def attune_item(character: Character, item_name: str) -> InventoryResult:
    item = find_item(character, item_name)
    if item is None:
        return _fail(f"item não está no inventário: {item_name}")
    if not item.requires_attunement:
        return _fail(f"{item.name} não requer sintonização")
    if item.name in character.attuned_items:
        return _fail(f"{item.name} já está sintonizado")
    if len(character.attuned_items) >= MAX_ATTUNED_ITEMS:
        return _fail(
            f"limite de {MAX_ATTUNED_ITEMS} itens sintonizados atingido (PHB p. 138)"
        )
    character.attuned_items.append(item.name)
    return InventoryResult(
        ok=True,
        ac_before=character.armor_class,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


def unattune_item(character: Character, item_name: str) -> InventoryResult:
    lowered = item_name.strip().lower()
    if all(n.lower() != lowered for n in character.attuned_items):
        return _fail(f"item não está sintonizado: {item_name}")
    character.attuned_items = [
        n for n in character.attuned_items if n.lower() != lowered
    ]
    return InventoryResult(
        ok=True,
        ac_before=character.armor_class,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


# ============================================================================
# Prices + shop
# ============================================================================


def price_for_item(item: Item) -> float:
    """Listed price: explicit value first, then rarity table, else 0."""
    if item.value_gp > 0:
        return item.value_gp
    if item.rarity:
        return RARITY_PRICE_GP.get(item.rarity, 0.0)
    return 0.0


def resolve_catalog_item(name: str) -> Optional[Item]:
    """Resolve a catalog name against the PHB tables into a state Item.

    Tries weapons, armor, gear, then magic items. Returns None when the
    name isn't in any table (the vendor stocks something unknown).
    """
    from auto_dm.character.builder import _armor_to_item, _weapon_to_item
    from auto_dm.phb import get_armor, get_gear_item, get_magic_item, get_weapon

    weapon = get_weapon(name)
    if weapon is not None:
        return _weapon_to_item(weapon)
    armor = get_armor(name)
    if armor is not None:
        return _armor_to_item(armor)
    gear = get_gear_item(name)
    if gear is not None:
        return Item(
            name=gear.name,
            type=ItemType.MISC,
            weight=gear.weight,
            value_gp=gear.cost_gp,
            description=gear.description,
        )
    magic = get_magic_item(name)
    if magic is not None:
        item_type = {
            "weapon": ItemType.WEAPON,
            "armor": ItemType.ARMOR,
            "shield": ItemType.SHIELD,
            "potion": ItemType.CONSUMABLE,
            "scroll": ItemType.CONSUMABLE,
        }.get(magic.item_type.value, ItemType.MISC)
        return Item(
            name=magic.name,
            type=item_type,
            description=magic.description,
            magic_bonus=magic.magic_bonus or None,
            requires_attunement=magic.requires_attunement,
            rarity=magic.rarity.value,
        )
    return None


def _find_shop_entry(vendor: NPC, item_id: str) -> Optional[ShopItem]:
    lowered = item_id.strip().lower()
    for entry in vendor.shop_inventory:
        if entry.item_id.lower() == lowered:
            return entry
    return None


def buy_item(
    character: Character,
    vendor: NPC,
    item_id: str,
    quantity: int = 1,
) -> InventoryResult:
    """Buy from a vendor NPC: checks gold, transfers item(s)."""
    if quantity < 1:
        return _fail("quantity deve ser >= 1")
    if not vendor.vendor:
        return _fail(f"{vendor.name} não é um vendedor")
    entry = _find_shop_entry(vendor, item_id)
    if entry is None:
        return _fail(f"{vendor.name} não vende {item_id}")
    cost = entry.price_gp * quantity
    if character.gold_gp < cost:
        return _fail(
            f"ouro insuficiente: precisa de {cost:g} gp, tem {character.gold_gp:g} gp"
        )
    item = resolve_catalog_item(entry.item_id)
    if item is None:
        return _fail(f"item desconhecido no catálogo: {entry.item_id}")
    character.gold_gp -= cost
    add_item(character, item, quantity)
    return InventoryResult(
        ok=True,
        ac_before=character.armor_class,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )


def sell_item(character: Character, item_name: str, quantity: int = 1) -> InventoryResult:
    """Sell from the inventory at ``SELL_RATE`` of the listed price."""
    if quantity < 1:
        return _fail("quantity deve ser >= 1")
    item = find_item(character, item_name)
    if item is None:
        return _fail(f"item não está no inventário: {item_name}")
    if quantity > item.quantity:
        return _fail(
            f"quantidade insuficiente de {item.name}: tem {item.quantity}, pediu {quantity}"
        )
    unit_price = price_for_item(item)
    proceeds = unit_price * SELL_RATE * quantity
    removal = remove_item(character, item_name, quantity)
    if not removal.ok:
        return removal
    character.gold_gp += proceeds
    return InventoryResult(
        ok=True,
        warnings=removal.warnings,
        ac_before=removal.ac_before,
        ac_after=character.armor_class,
        gold_gp=character.gold_gp,
    )
