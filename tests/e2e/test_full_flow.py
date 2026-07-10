"""Phase 43: four canonical scenarios through the real HTTP/data stack."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.helpers import (
    abilities,
    assert_state_rev,
    create_session,
    game_state,
    play_turn,
    signup_login,
)


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_solo_wizard_save_logout_login_load(client):
    auth = await signup_login(client)
    headers = auth["headers"]
    options_response = await client.get("/api/character-options", headers=headers)
    assert options_response.status_code == 200, options_response.text
    options = options_response.json()
    wizard = next(item for item in options["classes"] if item["name"] == "Wizard")
    spells = wizard["spellcasting"]["spells"]
    limits = wizard["spellcasting"]["limits"]["3"]
    cantrips = [spell["name"] for spell in spells if spell["level"] == 0][
        : limits["cantrips_known"]
    ]
    spellbook = [spell["name"] for spell in spells if 1 <= spell["level"] <= 2][
        : limits["spellbook_size"]
    ]
    campaign = f"E2E Wizard {uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/api/sessions/with-character",
        headers=headers,
        json={
            "campaign_name": campaign,
            "player_character": {
                "name": "Elara",
                "race": "Human",
                "class": "Wizard",
                "subclass": "Evocation",
                "background": "Sage",
                "alignment": "NG",
                "level": 3,
                "stats_method": "standard_array",
                "skills": ["arcana", "history"],
                "spell_selection": {
                    "cantrips": cantrips,
                    "spellbook": spellbook,
                    "spells_prepared": spellbook[:4],
                },
            },
            "companions": ["kael", "garrick", "mira"],
            "narration_length": "curto",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    sid = created["session_id"]
    state = created["state"]
    assert len(state["party"]) == 4
    assert state["party"][0]["spellcasting"]["spell_slots"] == {"1": 4, "2": 2}

    for turn in range(3):
        result = await play_turn(client, headers, sid, f"[E2E_TURN] jogador {turn}")
        assert_state_rev(state, result["state"])
        state = result["state"]

    slug = f"wizard-{uuid.uuid4().hex[:8]}"
    saved = await client.post(
        "/api/saves", headers=headers, json={"slug": slug, "state": state}
    )
    assert saved.status_code == 201, saved.text

    # Logout is client-side token disposal. Prove the API rejects it, then log in again.
    assert (await client.get("/api/saves")).status_code == 401
    login = await client.post(
        "/api/auth/login",
        json={"username": auth["username"], "password": auth["password"]},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    loaded = await client.post(f"/api/saves/{slug}/load", headers=headers)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["state"]["campaign_name"] == campaign
    assert len(loaded.json()["state"]["party"]) == 4

    loaded_sid = loaded.json()["session_id"]
    for turn in range(2):
        result = await play_turn(client, headers, loaded_sid, f"[E2E_TURN] apos load {turn}")
    assert len(result["state"]["narrative_log"]) >= len(state["narrative_log"]) + 4


@pytest.mark.asyncio
async def test_sheets_reflect_hp_after_attack(client):
    headers = (await signup_login(client))["headers"]
    goblin = {
        "id": "goblin_e2e",
        "name": "Goblin E2E",
        "hp_current": 40,
        "hp_max": 40,
        "armor_class": 1,
        "speed": 30,
        "abilities": abilities(),
        "is_hostile": True,
    }
    state = game_state(npcs=[goblin])
    state.update(
        {"in_combat": True, "initiative_order": ["pc1"], "current_turn_index": 0}
    )
    sid = await create_session(client, headers, state)

    before = 40
    result = None
    # Natural 1 is the only miss against AC 1; bounded retries remove flakiness.
    for attempt in range(4):
        result = await play_turn(client, headers, sid, f"[E2E_ATTACK] tentativa {attempt}")
        hp = next(n for n in result["state"]["npcs"] if n["id"] == "goblin_e2e")[
            "hp_current"
        ]
        if hp < before:
            break
    assert hp < before, result["result"]

    sheet = await client.get(f"/api/sessions/{sid}", headers=headers)
    companions = await client.get(f"/api/sessions/{sid}/companions", headers=headers)
    assert sheet.status_code == 200, sheet.text
    assert companions.status_code == 200, companions.text
    persisted_hp = next(
        n for n in sheet.json()["state"]["npcs"] if n["id"] == "goblin_e2e"
    )["hp_current"]
    assert persisted_hp == hp
    assert companions.json()["companions"] == []


@pytest.mark.asyncio
async def test_shop_buy_insufficient_gold_402_then_happy_path(client):
    headers = (await signup_login(client))["headers"]
    vendor = {
        "id": "vendor_e2e",
        "name": "Meri",
        "hp_current": 10,
        "hp_max": 10,
        "armor_class": 10,
        "speed": 30,
        "abilities": abilities(),
        "is_hostile": False,
        "vendor": True,
        "shop_inventory": [{"item_id": "Potion of Healing", "price_gp": 50}],
    }
    poor_sid = await create_session(client, headers, game_state(gold=1, npcs=[vendor]))
    purchase = {"vendor_id": "vendor_e2e", "item_id": "Potion of Healing"}
    denied = await client.post(
        f"/api/sessions/{poor_sid}/inventory/buy", headers=headers, json=purchase
    )
    assert denied.status_code == 402, denied.text

    rich_sid = await create_session(client, headers, game_state(gold=100, npcs=[vendor]))
    bought = await client.post(
        f"/api/sessions/{rich_sid}/inventory/buy", headers=headers, json=purchase
    )
    assert bought.status_code == 200, bought.text
    assert bought.json()["result"]["gold_gp"] == 50
    inventory = await client.get(f"/api/sessions/{rich_sid}/inventory", headers=headers)
    assert inventory.status_code == 200
    assert any(
        item["name"] == "Potion of Healing"
        for group in inventory.json()["groups"].values()
        for item in group
    )


@pytest.mark.asyncio
async def test_travel_three_days_rolls_real_world_events(client):
    headers = (await signup_login(client))["headers"]
    # This seed is pinned against the real Phase 40 tables. The assertion checks
    # the engine outcome, rather than accepting a response invented by FakeDM.
    state = game_state(gold=0, campaign_seed="phase43-travel-30")
    sid = await create_session(client, headers, state)
    result = await play_turn(client, headers, sid, "[E2E_TRAVEL] viajar tres dias")
    mechanical = result["result"]["action_result"]["mechanical"]
    assert mechanical["travel_hours"] == 72.0
    assert mechanical["world_events"]
    assert result["state"]["elapsed_game_minutes"] == 1680
    kinds = {event["kind"] for event in mechanical["world_events"]}
    assert {"encounter", "weather", "loot"}.issubset(kinds)
    assert len(result["state"]["npcs"]) >= 2
    assert result["state"]["party"][0]["gold_gp"] > 0
