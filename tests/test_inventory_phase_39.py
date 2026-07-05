"""Phase 39a — inventory & equipment engine tests.

Covers: model fields (gold_gp/attuned_items/vendor/shop_inventory)
back-compat, AC recompute (armored/unarmored/Unarmored Defense/shield),
equip/unequip with slot + proficiency validation, consumable stacking,
attunement cap (PHB p. 138), buy/sell with the 50% sell rate.
"""
from __future__ import annotations

from auto_dm.engine.inventory import (
    MAX_ATTUNED_ITEMS,
    SELL_RATE,
    add_item,
    attune_item,
    buy_item,
    compute_armor_class,
    equip_item,
    find_item,
    price_for_item,
    remove_item,
    resolve_catalog_item,
    sell_item,
    unattune_item,
    unequip_item,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Item,
    ItemType,
    NPC,
    ShopItem,
)


def make_character(
    class_: str = "Fighter",
    *,
    dexterity: int = 14,
    constitution: int = 14,
    wisdom: int = 10,
    gold_gp: float = 0.0,
) -> Character:
    ch = Character(
        id="pc1", name="Test", race="Human", class_=class_,
        level=3, background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=dexterity, constitution=constitution,
            intelligence=10, wisdom=wisdom, charisma=10,
        ),
        hp_current=28, hp_max=28, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=3,
        gold_gp=gold_gp,
    )
    ch.armor_class = compute_armor_class(ch)
    return ch


def make_potion(quantity: int = 1) -> Item:
    return Item(
        name="Potion of Healing", type=ItemType.CONSUMABLE,
        value_gp=50.0, quantity=quantity,
    )


def give(ch: Character, name: str) -> Item:
    """Resolve a PHB catalog item and put it in the inventory."""
    item = resolve_catalog_item(name)
    assert item is not None, f"PHB catalog missing: {name}"
    ch.inventory.append(item)
    return item


def make_vendor(*stock: ShopItem) -> NPC:
    return NPC(
        id="vendor1", name="Merchant", hp_current=10, hp_max=10,
        armor_class=10, speed=30, abilities=AbilityScores.all_seven(),
        is_hostile=False, vendor=True, shop_inventory=list(stock),
    )


# ============================================================================
# Model fields + back-compat
# ============================================================================


class TestModelFields:
    def test_defaults(self):
        ch = make_character()
        assert ch.gold_gp == 0.0
        assert ch.attuned_items == []
        npc = make_vendor()
        assert npc.vendor is True
        assert NPC(
            id="n", name="N", hp_current=1, hp_max=1, armor_class=10,
            speed=30, abilities=AbilityScores.all_seven(),
        ).vendor is False

    def test_old_save_without_new_fields_loads(self):
        ch = make_character()
        data = ch.model_dump(by_alias=True)
        del data["gold_gp"]
        del data["attuned_items"]
        restored = Character.model_validate(data)
        assert restored.gold_gp == 0.0
        assert restored.attuned_items == []

    def test_gold_roundtrip_via_json(self):
        ch = make_character(gold_gp=47.5)
        ch.attuned_items = ["Ring of Protection"]
        restored = Character.model_validate_json(ch.model_dump_json(by_alias=True))
        assert restored.gold_gp == 47.5
        assert restored.attuned_items == ["Ring of Protection"]

    def test_shop_item_roundtrip(self):
        npc = make_vendor(ShopItem(item_id="Longsword", price_gp=15.0))
        restored = NPC.model_validate_json(npc.model_dump_json())
        assert restored.shop_inventory[0].item_id == "Longsword"
        assert restored.shop_inventory[0].restock_daily is False


# ============================================================================
# AC recompute
# ============================================================================


class TestComputeArmorClass:
    def test_unarmored_10_plus_dex(self):
        ch = make_character(dexterity=14)
        assert compute_armor_class(ch) == 12

    def test_monk_unarmored_defense(self):
        ch = make_character("Monk", dexterity=16, wisdom=14)
        assert compute_armor_class(ch) == 10 + 3 + 2

    def test_barbarian_unarmored_defense(self):
        ch = make_character("Barbarian", dexterity=12, constitution=16)
        assert compute_armor_class(ch) == 10 + 1 + 3

    def test_heavy_armor_ignores_dex(self):
        ch = make_character(dexterity=18)
        give(ch, "Chain Mail")
        res = equip_item(ch, "armor", "Chain Mail")
        assert res.ok
        assert ch.armor_class == 16

    def test_medium_armor_caps_dex_at_2(self):
        ch = make_character(dexterity=18)  # +4 capped to +2
        give(ch, "Scale Mail")
        equip_item(ch, "armor", "Scale Mail")
        assert ch.armor_class == 14 + 2

    def test_light_armor_full_dex(self):
        ch = make_character(dexterity=18)
        give(ch, "Leather")
        equip_item(ch, "armor", "Leather")
        assert ch.armor_class == 11 + 4

    def test_shield_adds_2(self):
        ch = make_character(dexterity=14)
        give(ch, "Shield")
        res = equip_item(ch, "off_hand", "Shield")
        assert res.ok
        assert ch.armor_class == 12 + 2
        assert res.ac_delta == 2

    def test_magic_bonus_not_baked_into_stored_ac(self):
        # Combat adds magic bonuses dynamically; stored AC uses base only.
        ch = make_character(dexterity=14)
        item = give(ch, "Chain Mail")
        item.magic_bonus = 1
        equip_item(ch, "armor", "Chain Mail")
        assert ch.armor_class == 16


# ============================================================================
# Equip / unequip
# ============================================================================


class TestEquip:
    def test_equip_armor_reports_ac_delta(self):
        ch = make_character(dexterity=14)  # AC 12 unarmored
        give(ch, "Chain Mail")
        res = equip_item(ch, "armor", "Chain Mail")
        assert res.ok
        assert res.ac_before == 12
        assert res.ac_after == 16
        assert res.ac_delta == 4

    def test_item_stays_in_inventory_when_equipped(self):
        ch = make_character()
        give(ch, "Longsword")
        equip_item(ch, "main_hand", "Longsword")
        assert find_item(ch, "Longsword") is not None
        assert ch.equipped.main_hand.name == "Longsword"

    def test_wrong_slot_armor_in_main_hand(self):
        ch = make_character()
        give(ch, "Chain Mail")
        res = equip_item(ch, "main_hand", "Chain Mail")
        assert not res.ok
        assert ch.equipped.main_hand is None

    def test_wrong_slot_weapon_in_armor(self):
        ch = make_character()
        give(ch, "Longsword")
        res = equip_item(ch, "armor", "Longsword")
        assert not res.ok

    def test_item_not_in_inventory(self):
        ch = make_character()
        res = equip_item(ch, "main_hand", "Excalibur")
        assert not res.ok

    def test_invalid_slot(self):
        ch = make_character()
        give(ch, "Longsword")
        res = equip_item(ch, "head", "Longsword")
        assert not res.ok

    def test_no_proficiency_warns_but_equips(self):
        ch = make_character("Wizard")
        give(ch, "Plate")
        res = equip_item(ch, "armor", "Plate")
        assert res.ok
        assert any("proficiência" in w for w in res.warnings)
        assert ch.equipped.armor is not None

    def test_proficient_class_no_warning(self):
        ch = make_character("Fighter")
        give(ch, "Plate")
        res = equip_item(ch, "armor", "Plate")
        assert res.ok
        assert res.warnings == []

    def test_weapon_proficiency_warning(self):
        ch = make_character("Wizard")
        give(ch, "Greatsword")
        res = equip_item(ch, "main_hand", "Greatsword")
        assert res.ok
        assert any("proficiência" in w for w in res.warnings)

    def test_wizard_dagger_is_proficient(self):
        ch = make_character("Wizard")
        give(ch, "Dagger")
        res = equip_item(ch, "main_hand", "Dagger")
        assert res.ok
        assert res.warnings == []

    def test_offhand_non_light_weapon_warns(self):
        ch = make_character()
        give(ch, "Longsword")
        res = equip_item(ch, "off_hand", "Longsword")
        assert res.ok
        assert any("light" in w for w in res.warnings)

    def test_unattuned_magic_item_warns(self):
        ch = make_character()
        ch.inventory.append(Item(
            name="Cloak of Displacement", type=ItemType.MISC,
            requires_attunement=True, rarity="rare",
        ))
        res = equip_item(ch, "cloak", "Cloak of Displacement")
        assert res.ok
        assert any("sintoniza" in w for w in res.warnings)

    def test_unequip_recomputes_ac(self):
        ch = make_character(dexterity=14)
        give(ch, "Chain Mail")
        equip_item(ch, "armor", "Chain Mail")
        res = unequip_item(ch, "armor")
        assert res.ok
        assert ch.armor_class == 12
        assert res.ac_delta == -4

    def test_unequip_empty_slot_fails(self):
        ch = make_character()
        res = unequip_item(ch, "armor")
        assert not res.ok


# ============================================================================
# Add / remove + stacking
# ============================================================================


class TestAddRemove:
    def test_consumables_stack_by_name(self):
        ch = make_character()
        add_item(ch, make_potion(), 3)
        add_item(ch, make_potion(), 1)
        item = find_item(ch, "Potion of Healing")
        assert item.quantity == 4
        assert len([i for i in ch.inventory if i.name == item.name]) == 1

    def test_weapons_do_not_stack(self):
        ch = make_character()
        sword = resolve_catalog_item("Longsword")
        add_item(ch, sword)
        add_item(ch, sword)
        assert len([i for i in ch.inventory if i.name == "Longsword"]) == 2

    def test_use_one_potion_from_stack(self):
        ch = make_character()
        add_item(ch, make_potion(), 4)
        res = remove_item(ch, "Potion of Healing", 1)
        assert res.ok
        assert find_item(ch, "Potion of Healing").quantity == 3

    def test_remove_depletes_entry(self):
        ch = make_character()
        add_item(ch, make_potion(), 2)
        remove_item(ch, "Potion of Healing", 2)
        assert find_item(ch, "Potion of Healing") is None

    def test_remove_more_than_owned_fails(self):
        ch = make_character()
        add_item(ch, make_potion(), 1)
        res = remove_item(ch, "Potion of Healing", 2)
        assert not res.ok
        assert find_item(ch, "Potion of Healing").quantity == 1

    def test_remove_missing_item_fails(self):
        ch = make_character()
        assert not remove_item(ch, "Potion of Healing").ok

    def test_remove_equipped_item_clears_slot_and_attunement(self):
        ch = make_character(dexterity=14)
        item = give(ch, "Chain Mail")
        item.requires_attunement = True
        equip_item(ch, "armor", "Chain Mail")
        attune_item(ch, "Chain Mail")
        res = remove_item(ch, "Chain Mail", 1)
        assert res.ok
        assert ch.equipped.armor is None
        assert ch.attuned_items == []
        assert ch.armor_class == 12  # back to unarmored


# ============================================================================
# Attunement
# ============================================================================


def _attunable(name: str) -> Item:
    return Item(name=name, type=ItemType.MISC, requires_attunement=True)


class TestAttunement:
    def test_attune_ok(self):
        ch = make_character()
        ch.inventory.append(_attunable("Ring of Protection"))
        res = attune_item(ch, "Ring of Protection")
        assert res.ok
        assert ch.attuned_items == ["Ring of Protection"]

    def test_fourth_attunement_fails(self):
        ch = make_character()
        for i in range(MAX_ATTUNED_ITEMS + 1):
            ch.inventory.append(_attunable(f"Wondrous {i}"))
        for i in range(MAX_ATTUNED_ITEMS):
            assert attune_item(ch, f"Wondrous {i}").ok
        res = attune_item(ch, f"Wondrous {MAX_ATTUNED_ITEMS}")
        assert not res.ok
        assert len(ch.attuned_items) == MAX_ATTUNED_ITEMS

    def test_attune_non_attunement_item_fails(self):
        ch = make_character()
        add_item(ch, make_potion())
        assert not attune_item(ch, "Potion of Healing").ok

    def test_attune_twice_fails(self):
        ch = make_character()
        ch.inventory.append(_attunable("Ring of Protection"))
        attune_item(ch, "Ring of Protection")
        assert not attune_item(ch, "Ring of Protection").ok

    def test_unattune(self):
        ch = make_character()
        ch.inventory.append(_attunable("Ring of Protection"))
        attune_item(ch, "Ring of Protection")
        assert unattune_item(ch, "Ring of Protection").ok
        assert ch.attuned_items == []

    def test_unattune_not_attuned_fails(self):
        ch = make_character()
        assert not unattune_item(ch, "Ring of Protection").ok


# ============================================================================
# Prices, buy, sell
# ============================================================================


class TestShop:
    def test_price_prefers_explicit_value(self):
        item = Item(name="X", value_gp=15.0, rarity="rare")
        assert price_for_item(item) == 15.0

    def test_price_falls_back_to_rarity(self):
        item = Item(name="X", rarity="uncommon")
        assert price_for_item(item) == 500.0

    def test_buy_happy_path(self):
        ch = make_character(gold_gp=100.0)
        vendor = make_vendor(ShopItem(item_id="Longsword", price_gp=15.0))
        res = buy_item(ch, vendor, "Longsword")
        assert res.ok
        assert ch.gold_gp == 85.0
        assert res.gold_gp == 85.0
        assert find_item(ch, "Longsword") is not None

    def test_buy_insufficient_gold(self):
        ch = make_character(gold_gp=10.0)
        vendor = make_vendor(ShopItem(item_id="Longsword", price_gp=15.0))
        res = buy_item(ch, vendor, "Longsword")
        assert not res.ok
        assert any("insuficiente" in e for e in res.errors)
        assert ch.gold_gp == 10.0
        assert find_item(ch, "Longsword") is None

    def test_buy_from_non_vendor_fails(self):
        ch = make_character(gold_gp=100.0)
        vendor = make_vendor(ShopItem(item_id="Longsword", price_gp=15.0))
        vendor.vendor = False
        assert not buy_item(ch, vendor, "Longsword").ok

    def test_buy_item_not_in_stock_fails(self):
        ch = make_character(gold_gp=100.0)
        vendor = make_vendor(ShopItem(item_id="Longsword", price_gp=15.0))
        assert not buy_item(ch, vendor, "Greataxe").ok

    def test_buy_unknown_catalog_item_fails(self):
        ch = make_character(gold_gp=100.0)
        vendor = make_vendor(ShopItem(item_id="Alphabet Soup", price_gp=1.0))
        res = buy_item(ch, vendor, "Alphabet Soup")
        assert not res.ok
        assert ch.gold_gp == 100.0

    def test_buy_quantity_multiplies_cost(self):
        ch = make_character(gold_gp=100.0)
        vendor = make_vendor(ShopItem(item_id="Dagger", price_gp=2.0))
        res = buy_item(ch, vendor, "Dagger", quantity=3)
        assert res.ok
        assert ch.gold_gp == 94.0

    def test_sell_at_half_price(self):
        ch = make_character(gold_gp=0.0)
        add_item(ch, make_potion(), 1)  # value 50 gp
        res = sell_item(ch, "Potion of Healing")
        assert res.ok
        assert ch.gold_gp == 50.0 * SELL_RATE
        assert find_item(ch, "Potion of Healing") is None

    def test_sell_partial_stack(self):
        ch = make_character(gold_gp=0.0)
        add_item(ch, make_potion(), 4)
        res = sell_item(ch, "Potion of Healing", quantity=2)
        assert res.ok
        assert ch.gold_gp == 50.0
        assert find_item(ch, "Potion of Healing").quantity == 2

    def test_sell_more_than_owned_fails(self):
        ch = make_character(gold_gp=0.0)
        add_item(ch, make_potion(), 1)
        res = sell_item(ch, "Potion of Healing", quantity=2)
        assert not res.ok
        assert ch.gold_gp == 0.0

    def test_sell_missing_item_fails(self):
        ch = make_character()
        assert not sell_item(ch, "Potion of Healing").ok

    def test_sell_equipped_item_unequips_and_recomputes_ac(self):
        ch = make_character(dexterity=14)
        give(ch, "Chain Mail")  # value 75 gp in PHB
        equip_item(ch, "armor", "Chain Mail")
        res = sell_item(ch, "Chain Mail")
        assert res.ok
        assert ch.equipped.armor is None
        assert ch.armor_class == 12
        assert ch.gold_gp > 0


# ============================================================================
# Catalog resolution
# ============================================================================


class TestResolveCatalog:
    def test_weapon(self):
        item = resolve_catalog_item("Longsword")
        assert item is not None
        assert item.type == ItemType.WEAPON
        assert item.weapon is not None

    def test_armor(self):
        item = resolve_catalog_item("Chain Mail")
        assert item is not None
        assert item.armor is not None
        assert item.armor.base_ac == 16

    def test_shield(self):
        item = resolve_catalog_item("Shield")
        assert item is not None
        assert item.armor.is_shield

    def test_unknown_returns_none(self):
        assert resolve_catalog_item("Alphabet Soup") is None
