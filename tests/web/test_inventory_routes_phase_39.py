"""Phase 39b — inventory & shop route tests (SPEC §12.2).

Covers auth/ownership (401/404), the grouped inventory view, equip/
unequip with AC diff + proficiency warnings, drop with stacking,
attunement cap (422), buy (402 without gold, 404 vendor, 422 not a
vendor / not in stock) and sell at half price.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# Same invite-gate bypass as the other web test files (Phase 26e).
@pytest.fixture(autouse=True)
def _open_signup_for_phase39(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    from auto_dm.web.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
# State helpers
# ============================================================================


def _abilities():
    return {
        "strength": 16, "dexterity": 14, "constitution": 14,
        "intelligence": 10, "wisdom": 10, "charisma": 10,
    }


def _chain_mail():
    return {
        "name": "Chain Mail", "type": "armor", "value_gp": 75.0,
        "armor": {"base_ac": 16, "add_dex_modifier": False},
    }


def _plate():
    return {
        "name": "Plate", "type": "armor", "value_gp": 1500.0,
        "armor": {"base_ac": 18, "add_dex_modifier": False},
    }


def _potion(quantity=1):
    return {
        "name": "Potion of Healing", "type": "consumable",
        "value_gp": 50.0, "quantity": quantity,
    }


def _attunable(name):
    return {"name": name, "type": "misc", "requires_attunement": True}


def _make_char(cid, name, klass, *, is_player, inventory=(), gold=0.0):
    return {
        "id": cid, "name": name, "race": "Human", "class": klass,
        "level": 3, "background": "Soldier", "alignment": "TN",
        "abilities": _abilities(),
        "hp_current": 20, "hp_max": 20, "armor_class": 12, "speed": 30,
        "proficiency_bonus": 2, "hit_dice": "1d10", "hit_dice_remaining": 3,
        "is_player": is_player,
        "inventory": list(inventory),
        "gold_gp": gold,
    }


def _vendor(stock, *, vendor=True, npc_id="vendor_meri"):
    return {
        "id": npc_id, "name": "Meri", "hp_current": 10, "hp_max": 10,
        "armor_class": 10, "speed": 30, "abilities": _abilities(),
        "is_hostile": False, "vendor": vendor, "shop_inventory": stock,
    }


def _state(player_inventory=(), gold=100.0, npcs=(), companions=()):
    return {
        "campaign_name": "Inventory Test",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "Market",
        "party": [
            _make_char(
                "pc1", "Aldo", "Fighter",
                is_player=True, inventory=player_inventory, gold=gold,
            ),
            *companions,
        ],
        "npcs": list(npcs),
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "pc1",
    }


async def _create_session(client, headers, state) -> str:
    resp = await client.post("/api/sessions", json={"state": state}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ============================================================================
# Auth + ownership
# ============================================================================


@pytest.mark.asyncio
async def test_get_inventory_requires_auth(client):
    resp = await client.get("/api/sessions/x/inventory")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_equip_requires_auth(client):
    resp = await client.post(
        "/api/sessions/x/inventory/equip",
        json={"item_id": "Chain Mail", "slot": "armor"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_inventory_404_when_session_missing(client, auth_token):
    _, _, headers = auth_token
    resp = await client.get("/api/sessions/nope/inventory", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_user_session_404(client, admin_token, auth_token):
    _, _, admin_headers = admin_token
    _, _, user_headers = auth_token
    sid = await _create_session(client, admin_headers, _state())
    resp = await client.get(f"/api/sessions/{sid}/inventory", headers=user_headers)
    assert resp.status_code == 404


# ============================================================================
# Inventory view
# ============================================================================


@pytest.mark.asyncio
async def test_inventory_view_groups_and_gold(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_chain_mail(), _potion(3)], gold=42.5),
    )
    resp = await client.get(f"/api/sessions/{sid}/inventory", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["character_id"] == "pc1"
    assert body["gold_gp"] == 42.5
    assert body["attuned_items"] == []
    assert "armor" in body["groups"]
    assert "consumable" in body["groups"]
    assert body["groups"]["consumable"][0]["quantity"] == 3
    assert "main_hand" in body["slots"]
    assert body["equipped"]["armor"] is None


# ============================================================================
# Equip / unequip
# ============================================================================


@pytest.mark.asyncio
async def test_equip_armor_returns_ac_diff_and_persists(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_chain_mail()]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Chain Mail", "slot": "armor"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["ok"] is True
    assert body["result"]["ac_after"] == 16
    assert body["result"]["ac_delta"] == 4
    assert body["character"]["equipped"]["armor"]["name"] == "Chain Mail"
    # Mutation persisted to the session.
    state = (await client.get(f"/api/sessions/{sid}", headers=headers)).json()["state"]
    player = state["party"][0]
    assert player["armor_class"] == 16


@pytest.mark.asyncio
async def test_equip_missing_item_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state())
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Excalibur", "slot": "main_hand"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_equip_invalid_slot_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_chain_mail()]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Chain Mail", "slot": "head"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_equip_no_proficiency_warns(client, admin_token):
    """Wizard companion equips Plate: 200 with a warning (5e doesn't block)."""
    _, _, headers = admin_token
    wizard = _make_char(
        "comp_wiz", "Lyra", "Wizard", is_player=False, inventory=[_plate()],
    )
    sid = await _create_session(client, headers, _state(companions=[wizard]))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Plate", "slot": "armor", "character_id": "comp_wiz"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any("proficiência" in w for w in body["result"]["warnings"])
    assert body["character"]["id"] == "comp_wiz"
    assert body["character"]["armor_class"] == 18


@pytest.mark.asyncio
async def test_character_id_not_in_party_404(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state())
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Chain Mail", "slot": "armor", "character_id": "ghost"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unequip_restores_ac(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_chain_mail()]),
    )
    await client.post(
        f"/api/sessions/{sid}/inventory/equip",
        json={"item_id": "Chain Mail", "slot": "armor"},
        headers=headers,
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/unequip",
        json={"slot": "armor"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ac_after"] == 12
    assert body["character"]["equipped"]["armor"] is None


@pytest.mark.asyncio
async def test_unequip_empty_slot_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state())
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/unequip",
        json={"slot": "armor"},
        headers=headers,
    )
    assert resp.status_code == 422


# ============================================================================
# Drop
# ============================================================================


@pytest.mark.asyncio
async def test_drop_decrements_stack(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_potion(4)]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/drop",
        json={"item_id": "Potion of Healing", "quantity": 1},
        headers=headers,
    )
    assert resp.status_code == 200
    inv = resp.json()["character"]["inventory"]
    assert inv[0]["quantity"] == 3


@pytest.mark.asyncio
async def test_drop_all_removes_entry(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_potion(2)]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/drop",
        json={"item_id": "Potion of Healing", "quantity": 2},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["character"]["inventory"] == []


@pytest.mark.asyncio
async def test_drop_more_than_owned_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_potion(1)]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/drop",
        json={"item_id": "Potion of Healing", "quantity": 5},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_drop_missing_item_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state())
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/drop",
        json={"item_id": "Potion of Healing"},
        headers=headers,
    )
    assert resp.status_code == 422


# ============================================================================
# Attunement
# ============================================================================


@pytest.mark.asyncio
async def test_attune_and_unattune(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_attunable("Ring of Protection")]),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/attune",
        json={"item_id": "Ring of Protection"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["character"]["attuned_items"] == ["Ring of Protection"]

    resp = await client.post(
        f"/api/sessions/{sid}/inventory/unattune",
        json={"item_id": "Ring of Protection"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["character"]["attuned_items"] == []


@pytest.mark.asyncio
async def test_fourth_attunement_422(client, admin_token):
    _, _, headers = admin_token
    items = [_attunable(f"Wondrous {i}") for i in range(4)]
    sid = await _create_session(client, headers, _state(player_inventory=items))
    for i in range(3):
        resp = await client.post(
            f"/api/sessions/{sid}/inventory/attune",
            json={"item_id": f"Wondrous {i}"},
            headers=headers,
        )
        assert resp.status_code == 200
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/attune",
        json={"item_id": "Wondrous 3"},
        headers=headers,
    )
    assert resp.status_code == 422


# ============================================================================
# Shop: catalog, buy, sell
# ============================================================================


@pytest.mark.asyncio
async def test_shop_catalog_resolves_items(client, admin_token):
    _, _, headers = admin_token
    vendor = _vendor([
        {"item_id": "Longsword", "price_gp": 15.0},
        {"item_id": "Alphabet Soup", "price_gp": 1.0},
    ])
    sid = await _create_session(client, headers, _state(npcs=[vendor], gold=20.0))
    resp = await client.get(
        f"/api/sessions/{sid}/shop/vendor_meri", headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vendor_name"] == "Meri"
    assert body["gold_gp"] == 20.0
    assert body["stock"][0]["item"]["name"] == "Longsword"
    assert body["stock"][0]["price_gp"] == 15.0
    # Unknown catalog entries surface with item=None (frontend hides buy).
    assert body["stock"][1]["item"] is None


@pytest.mark.asyncio
async def test_shop_vendor_not_found_404(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state())
    resp = await client.get(f"/api/sessions/{sid}/shop/ghost", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_shop_non_vendor_npc_422(client, admin_token):
    _, _, headers = admin_token
    npc = _vendor([], vendor=False)
    sid = await _create_session(client, headers, _state(npcs=[npc]))
    resp = await client.get(
        f"/api/sessions/{sid}/shop/vendor_meri", headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_buy_happy_path(client, admin_token):
    _, _, headers = admin_token
    vendor = _vendor([{"item_id": "Longsword", "price_gp": 15.0}])
    sid = await _create_session(client, headers, _state(npcs=[vendor], gold=100.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "vendor_meri", "item_id": "Longsword"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["gold_gp"] == 85.0
    names = [i["name"] for i in body["character"]["inventory"]]
    assert "Longsword" in names


@pytest.mark.asyncio
async def test_buy_insufficient_gold_402(client, admin_token):
    _, _, headers = admin_token
    vendor = _vendor([{"item_id": "Longsword", "price_gp": 15.0}])
    sid = await _create_session(client, headers, _state(npcs=[vendor], gold=5.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "vendor_meri", "item_id": "Longsword"},
        headers=headers,
    )
    assert resp.status_code == 402
    # No mutation persisted.
    state = (await client.get(f"/api/sessions/{sid}", headers=headers)).json()["state"]
    assert state["party"][0]["gold_gp"] == 5.0
    assert state["party"][0]["inventory"] == []


@pytest.mark.asyncio
async def test_buy_from_non_vendor_422(client, admin_token):
    _, _, headers = admin_token
    npc = _vendor([{"item_id": "Longsword", "price_gp": 15.0}], vendor=False)
    sid = await _create_session(client, headers, _state(npcs=[npc], gold=100.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "vendor_meri", "item_id": "Longsword"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_buy_vendor_not_found_404(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(client, headers, _state(gold=100.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "ghost", "item_id": "Longsword"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_buy_item_not_in_stock_422(client, admin_token):
    _, _, headers = admin_token
    vendor = _vendor([{"item_id": "Longsword", "price_gp": 15.0}])
    sid = await _create_session(client, headers, _state(npcs=[vendor], gold=100.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "vendor_meri", "item_id": "Greataxe"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_buy_quantity_stacks_and_multiplies_cost(client, admin_token):
    _, _, headers = admin_token
    vendor = _vendor([{"item_id": "Dagger", "price_gp": 2.0}])
    sid = await _create_session(client, headers, _state(npcs=[vendor], gold=10.0))
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/buy",
        json={"vendor_id": "vendor_meri", "item_id": "Dagger", "quantity": 3},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["gold_gp"] == 4.0


@pytest.mark.asyncio
async def test_sell_at_half_price(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_potion(1)], gold=0.0),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/sell",
        json={"item_id": "Potion of Healing"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["gold_gp"] == 25.0
    assert body["character"]["inventory"] == []


@pytest.mark.asyncio
async def test_sell_more_than_owned_422(client, admin_token):
    _, _, headers = admin_token
    sid = await _create_session(
        client, headers, _state(player_inventory=[_potion(1)], gold=0.0),
    )
    resp = await client.post(
        f"/api/sessions/{sid}/inventory/sell",
        json={"item_id": "Potion of Healing", "quantity": 3},
        headers=headers,
    )
    assert resp.status_code == 422
