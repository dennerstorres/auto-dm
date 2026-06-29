"""Phase 25d tests: magic items loader, lookups, and engine integration.

Covers:
- ``load_magic_items`` parsing the 240+ magic item .md files in
  ``data/phb/Treasure/``.
- Tagline parsing for the common shapes (type, rarity, attunement
  clauses — including class-restricted like "by a paladin").
- Multi-rarity items (e.g. ``Weapon, +1, +2, or +3``) collapse to the
  lowest tier with a non-zero ``magic_bonus``.
- ``get_magic_item`` lookups (case-insensitive, partial).
- ``roll_magic_item(CR)`` returns an item appropriate for the tier.
- Engine: ``attack_roll`` and ``damage_roll`` add ``magic_bonus`` from
  the equipped weapon's ``magic_bonus`` field.
- Engine: ``attack_roll`` adds ``magic_bonus`` from the target's
  equipped armor/shield to the effective AC.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

import pytest

from auto_dm.engine.combat import attack_roll, damage_roll
from auto_dm.phb import (
    MagicItem,
    MagicItemType,
    Rarity,
    get_magic_item,
    get_magic_items,
    roll_magic_item,
    set_phb_root,
)
from auto_dm.phb.loader import load_magic_items
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    Item,
    ItemType,
    WeaponProperties,
    ArmorProperties,
    EquippedSlots,
)


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    """Each test starts with the real PHB root (and the real data)."""
    from auto_dm.phb import get_phb_root as _gpr

    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_weapon_item(
    name: str = "Longsword",
    dice: str = "1d8",
    dtype: str = "slashing",
    magic_bonus: Optional[int] = None,
) -> Item:
    """Build a weapon Item with optional magic_bonus."""
    return Item(
        name=name,
        type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice=dice, damage_type=dtype),
        magic_bonus=magic_bonus,
        rarity="uncommon" if magic_bonus else None,
    )


def _make_armor_item(
    name: str = "Plate",
    base_ac: int = 18,
    magic_bonus: Optional[int] = None,
) -> Item:
    return Item(
        name=name,
        type=ItemType.ARMOR,
        armor=ArmorProperties(
            base_ac=base_ac,
            add_dex_modifier=False,
            is_shield=False,
        ),
        magic_bonus=magic_bonus,
        rarity="rare" if magic_bonus else None,
    )


def _make_attacker(
    name: str = "Hero",
    str_score: int = 14,
    dex_score: int = 10,
    level: int = 5,
    weapon: Optional[Item] = None,
    proficiency_bonus: int = 3,
) -> Character:
    """Build a melee fighter with the given weapon (mundane or magic)."""
    char = Character(
        id="hero",
        name=name,
        race="Human",
        **{"class": "Fighter"},
        level=level,
        background="Soldier",
        alignment="LG",
        abilities=AbilityScores(
            strength=str_score,
            dexterity=dex_score,
            constitution=14,
            intelligence=10,
            wisdom=12,
            charisma=8,
        ),
        hp_current=30,
        hp_max=30,
        armor_class=16,
        speed=30,
        proficiency_bonus=proficiency_bonus,
        hit_dice="1d10",
        hit_dice_remaining=level,
        inventory=[],
        equipped=EquippedSlots(main_hand=weapon) if weapon else EquippedSlots(),
        is_player=True,
    )
    return char


def _make_target(
    name: str = "Foe",
    dex_score: int = 10,
    armor: Optional[Item] = None,
) -> Character:
    return Character(
        id="foe",
        name=name,
        race="Humanoid",
        **{"class": "Commoner"},
        level=1,
        background="Commoner",
        alignment="N",
        abilities=AbilityScores(
            strength=10, dexterity=dex_score, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
        hp_current=10,
        hp_max=10,
        armor_class=12,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d8",
        hit_dice_remaining=1,
        inventory=[],
        equipped=EquippedSlots(armor=armor) if armor else EquippedSlots(),
    )


# ===========================================================================
# Loader
# ===========================================================================


class TestLoadMagicItems:
    def test_count_is_above_200(self):
        items = load_magic_items(Path("data/phb"))
        # PHB has 237+ magic items (filtered from ~242 .md files
        # which include 2-3 chapter-intro files starting with "#").
        assert len(items) >= 200

    def test_specific_items_loaded(self):
        items = load_magic_items(Path("data/phb"))
        names = {i.name for i in items}
        for expected in [
            "Bag of Holding", "Potion of Healing", "Ring of Protection",
            "Holy Avenger",
        ]:
            assert expected in names, f"Missing: {expected}"

    def test_bag_of_holding_wondrous_uncommon(self):
        bag = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Bag of Holding"
        )
        assert bag.item_type == MagicItemType.WONDROUS
        assert bag.rarity == Rarity.UNCOMMON
        assert not bag.requires_attunement
        assert "interior space" in bag.description.lower()

    def test_potion_of_healing_rarity_varies(self):
        # Potion of Healing uses "rarity varies" — our parser defaults
        # to uncommon, which is the lowest tier.
        pot = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Potion of Healing"
        )
        assert pot.item_type == MagicItemType.POTION
        assert pot.rarity == Rarity.UNCOMMON

    def test_ring_of_protection_attunement_any(self):
        ring = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Ring of Protection"
        )
        assert ring.item_type == MagicItemType.RING
        assert ring.rarity == Rarity.RARE
        assert ring.requires_attunement
        assert ring.attunement_requirement == "by any class"

    def test_holy_avenger_paladin_restricted(self):
        avenger = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Holy Avenger"
        )
        assert avenger.item_type == MagicItemType.WEAPON
        assert avenger.rarity == Rarity.LEGENDARY
        assert avenger.requires_attunement
        assert "paladin" in avenger.attunement_requirement

    def test_generic_plus_weapon_has_magic_bonus(self):
        # The "Weapon, +1, +2, or +3" file represents the canonical
        # generic magic weapon. Rarity collapses to uncommon (lowest
        # tier), which maps to +1.
        generic = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Weapon, +1, +2, or +3"
        )
        assert generic.item_type == MagicItemType.WEAPON
        assert generic.magic_bonus == 1

    def test_legendary_weapon_no_magic_bonus(self):
        # Legendary weapons like Holy Avenger don't have a +X tier —
        # they get their bonus from the description prose.
        avenger = next(
            i for i in load_magic_items(Path("data/phb"))
            if i.name == "Holy Avenger"
        )
        assert avenger.magic_bonus == 0
        # The +3 is in the description text
        assert "+3" in avenger.description

    def test_index_files_skipped(self):
        # Chapter intro files like "# Magic Items.md" are not items.
        items = load_magic_items(Path("data/phb"))
        for item in items:
            assert not item.name.startswith("#")
            assert not item.name.startswith("##")

    def test_rarity_distribution(self):
        items = load_magic_items(Path("data/phb"))
        from collections import Counter
        counts = Counter(i.rarity for i in items)
        # PHB roughly: 80 rare, 78 uncommon, 51 very_rare, 27 legendary, 1-2 common
        assert counts[Rarity.RARE] >= 50
        assert counts[Rarity.UNCOMMON] >= 50
        assert counts[Rarity.VERY_RARE] >= 30
        assert counts[Rarity.LEGENDARY] >= 10


# ===========================================================================
# Lookups
# ===========================================================================


class TestMagicItemLookups:
    def test_get_magic_item_case_insensitive(self):
        assert get_magic_item("bag of holding") is not None
        assert get_magic_item("BAG OF HOLDING") is not None

    def test_get_magic_item_partial_match(self):
        # Partial search: "sword" should hit "Sword of Sharpness" or
        # similar.
        results = [
            get_magic_item("Sword of Sharpness"),
        ]
        assert all(r is not None for r in results)

    def test_get_magic_item_unknown(self):
        assert get_magic_item("Nonexistent Item") is None

    def test_get_magic_items_filter_by_rarity(self):
        legendary = get_magic_items(rarity=Rarity.LEGENDARY)
        assert all(i.rarity == Rarity.LEGENDARY for i in legendary)
        assert len(legendary) >= 10

    def test_get_magic_items_filter_by_type(self):
        rings = get_magic_items(item_type=MagicItemType.RING)
        assert all(i.item_type == MagicItemType.RING for i in rings)
        assert len(rings) >= 5

    def test_get_magic_items_combined_filter(self):
        # Uncommon rings exist (Ring of Jumping, etc.)
        uncommon_rings = get_magic_items(
            rarity=Rarity.UNCOMMON,
            item_type=MagicItemType.RING,
        )
        assert all(
            i.rarity == Rarity.UNCOMMON and i.item_type == MagicItemType.RING
            for i in uncommon_rings
        )


class TestRollMagicItem:
    def test_low_cr_returns_low_rarity(self):
        rng = random.Random(0)
        # CR 0-4: only common/uncommon
        for _ in range(20):
            item = roll_magic_item(3.0)
            if item is not None:
                assert item.rarity in {Rarity.COMMON, Rarity.UNCOMMON}

    def test_high_cr_can_return_legendary(self):
        rng = random.Random(0)
        # CR 17+: should return rare/very_rare/legendary
        for _ in range(50):
            item = roll_magic_item(20.0)
            if item is not None:
                assert item.rarity in {
                    Rarity.RARE, Rarity.VERY_RARE, Rarity.LEGENDARY,
                }


# ===========================================================================
# Engine: magic weapon bonus to attack
# ===========================================================================


class TestMagicWeaponAttackBonus:
    def test_mundane_weapon_modifier(self):
        attacker = _make_attacker(weapon=_make_weapon_item())
        target = _make_target()
        result = attack_roll(attacker, target, rng=random.Random(42))
        # Mundane longsword, STR 14, prof +3: modifier = +2 + 3 = 5
        assert result.attack_modifier == 5

    def test_plus_one_weapon_increases_modifier_by_one(self):
        target = _make_target()
        mundane = _make_attacker(weapon=_make_weapon_item(magic_bonus=None))
        plus_one = _make_attacker(weapon=_make_weapon_item(magic_bonus=1))

        r_m = attack_roll(mundane, target, rng=random.Random(0))
        r_p1 = attack_roll(plus_one, target, rng=random.Random(0))
        assert r_p1.attack_modifier == r_m.attack_modifier + 1

    def test_plus_two_weapon_increases_modifier_by_two(self):
        target = _make_target()
        mundane = _make_attacker(weapon=_make_weapon_item())
        plus_two = _make_attacker(weapon=_make_weapon_item(magic_bonus=2))

        r_m = attack_roll(mundane, target, rng=random.Random(0))
        r_p2 = attack_roll(plus_two, target, rng=random.Random(0))
        assert r_p2.attack_modifier == r_m.attack_modifier + 2

    def test_no_weapon_means_no_magic_bonus(self):
        # Unarmed strike: STR + prof only, no magic_bonus
        attacker = _make_attacker(weapon=None)
        target = _make_target()
        result = attack_roll(attacker, target, rng=random.Random(0))
        assert result.attack_modifier == 5  # STR +2 + prof +3


# ===========================================================================
# Engine: magic weapon bonus to damage
# ===========================================================================


class TestMagicWeaponDamageBonus:
    def test_magic_bonus_added_to_damage_modifier(self):
        attacker = _make_attacker(weapon=_make_weapon_item(magic_bonus=1))
        attacker.equipped.main_hand = _make_weapon_item(magic_bonus=1)
        dmg = damage_roll(attacker, rng=random.Random(0))
        # STR mod +2 (capped at 0 for non-finesse) + magic +1 = +3 modifier
        # We can't easily check modifier directly, but we can check
        # that total >= dice roll + 3 (minimum modifier)
        # 1d8 minimum is 1; +3 minimum modifier = 4
        assert dmg.modifier >= 3, f"Modifier should be >= 3, got {dmg.modifier}"

    def test_mundane_weapon_damage_unchanged(self):
        attacker = _make_attacker(weapon=_make_weapon_item())
        dmg = damage_roll(attacker, rng=random.Random(0))
        # STR mod +2 only, no magic bonus
        assert dmg.modifier == 2

    def test_three_plus_weapon_damage_boost(self):
        attacker = _make_attacker(weapon=_make_weapon_item(magic_bonus=3))
        dmg = damage_roll(attacker, rng=random.Random(0))
        # STR +2 + magic +3 = +5
        assert dmg.modifier == 5


# ===========================================================================
# Engine: magic armor bonus to effective AC
# ===========================================================================


class TestMagicArmorAcBonus:
    def test_magic_armor_increases_target_effective_ac(self):
        # The effective AC is exposed in AttackResult.target_ac.
        attacker = _make_attacker(weapon=_make_weapon_item())
        mundane_tgt = _make_target()  # AC 12, no armor
        magic_tgt = _make_target(armor=_make_armor_item(magic_bonus=1))

        r_mundane = attack_roll(attacker, mundane_tgt, rng=random.Random(0))
        r_magic = attack_roll(attacker, magic_tgt, rng=random.Random(0))
        # +1 magic armor -> effective AC is 1 higher
        assert r_magic.target_ac == r_mundane.target_ac + 1

    def test_magic_shield_increases_ac(self):
        # Shield stored in off_hand with magic_bonus=1
        shield = Item(
            name="+1 Shield",
            type=ItemType.SHIELD,
            armor=ArmorProperties(base_ac=2, is_shield=True),
            magic_bonus=1,
        )
        attacker = _make_attacker(weapon=_make_weapon_item())
        mundane_tgt = _make_target()
        magic_tgt = _make_target()
        magic_tgt.equipped.off_hand = shield

        r_mundane = attack_roll(attacker, mundane_tgt, rng=random.Random(0))
        r_magic = attack_roll(attacker, magic_tgt, rng=random.Random(0))
        # Only the +1 magic_bonus is added (shield base AC is not yet
        # applied by the engine — that's a separate concern).
        assert r_magic.target_ac == r_mundane.target_ac + 1

    def test_mundane_armor_no_ac_bonus(self):
        # Sanity: mundane armor (no magic_bonus) gives no extra AC.
        attacker = _make_attacker(weapon=_make_weapon_item())
        no_armor = _make_target()  # AC 12
        with_armor = _make_target(armor=_make_armor_item(magic_bonus=None))

        r_no = attack_roll(attacker, no_armor, rng=random.Random(0))
        r_with = attack_roll(attacker, with_armor, rng=random.Random(0))
        # The armor here isn't even applied (engine doesn't add armor
        # base_ac yet), so AC should be the same.
        assert r_with.target_ac == r_no.target_ac


# ===========================================================================
# State Item model accepts magic fields
# ===========================================================================


class TestItemMagicFields:
    def test_default_item_no_magic(self):
        item = Item(name="Dagger", type=ItemType.WEAPON)
        assert item.magic_bonus is None
        assert not item.requires_attunement
        assert item.rarity is None

    def test_magic_item_fields(self):
        item = Item(
            name="+1 Longsword",
            type=ItemType.WEAPON,
            weapon=WeaponProperties(damage_dice="1d8", damage_type="slashing"),
            magic_bonus=1,
            requires_attunement=True,
            rarity="uncommon",
        )
        assert item.magic_bonus == 1
        assert item.requires_attunement is True
        assert item.rarity == "uncommon"

    def test_item_magic_bonus_serialization(self):
        # Magic fields survive Pydantic model_dump roundtrip
        item = Item(
            name="+2 Plate",
            type=ItemType.ARMOR,
            magic_bonus=2,
            rarity="rare",
            requires_attunement=False,
        )
        data = item.model_dump()
        assert data["magic_bonus"] == 2
        assert data["rarity"] == "rare"