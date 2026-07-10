"""HTTP helpers shared by Phase 43 real-stack scenarios."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


async def signup_login(client):
    username = f"e2e_{uuid.uuid4().hex[:12]}"
    password = "phase43-password"
    response = await client.post(
        "/api/auth/signup", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    signup = response.json()
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["id"] == signup["user"]["id"]
    return {
        "headers": {"Authorization": f"Bearer {login.json()['token']}"},
        "username": username,
        "password": password,
    }


async def play_turn(client, headers, session_id: str, text: str):
    response = await client.post(
        f"/api/sessions/{session_id}/input", json={"line": text}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def assert_state_rev(previous: dict, current: dict) -> None:
    """Assert the observable narrative revision advanced exactly forward."""
    assert len(current["narrative_log"]) > len(previous["narrative_log"])


def abilities() -> dict[str, int]:
    return {
        "strength": 18,
        "dexterity": 14,
        "constitution": 14,
        "intelligence": 10,
        "wisdom": 10,
        "charisma": 10,
    }


def character(*, gold: float = 0) -> dict:
    return {
        "id": "pc1",
        "name": "Aldo",
        "race": "Human",
        "class": "Fighter",
        "level": 3,
        "background": "Soldier",
        "alignment": "TN",
        "abilities": abilities(),
        "hp_current": 28,
        "hp_max": 28,
        "armor_class": 14,
        "speed": 30,
        "proficiency_bonus": 2,
        "hit_dice": "1d10",
        "hit_dice_remaining": 3,
        "is_player": True,
        "gold_gp": gold,
        "inventory": [
            {
                "name": "Longsword",
                "type": "weapon",
                "value_gp": 15,
                "weapon": {"damage_dice": "1d8", "damage_type": "slashing"},
            }
        ],
        "equipped": {
            "main_hand": {
                "name": "Longsword",
                "type": "weapon",
                "value_gp": 15,
                "weapon": {"damage_dice": "1d8", "damage_type": "slashing"},
            }
        },
    }


def game_state(*, gold: float = 0, npcs=(), campaign_seed: str = "phase43") -> dict:
    return {
        "campaign_name": f"Phase 43 {uuid.uuid4().hex[:8]}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "Mercado",
        "party": [character(gold=gold)],
        "npcs": list(npcs),
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "pc1",
        "campaign_seed": campaign_seed,
    }


async def create_session(client, headers, state: dict) -> str:
    # The raw session endpoint is intentionally admin-only. Seed through the
    # public save/load boundary so validation, Postgres and Redis are all used.
    slug = f"fixture-{uuid.uuid4().hex[:12]}"
    saved = await client.post(
        "/api/saves", json={"slug": slug, "state": state}, headers=headers
    )
    assert saved.status_code == 201, saved.text
    response = await client.post(f"/api/saves/{slug}/load", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]
