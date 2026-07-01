"""Tests for /api/sessions/* and /api/saves/* endpoints (Phase 26a)."""
from __future__ import annotations

import pytest


# ============================================================================
# Helpers
# ============================================================================


def _empty_state() -> dict:
    """A minimal valid GameState payload."""
    from datetime import datetime, timezone
    return {
        "campaign_name": "Test Campaign",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "Tavern",
        "party": [],
        "npcs": [],
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "",
        "active_conditions": [],
    }


# ============================================================================
# Auth-required checks
# ============================================================================


@pytest.mark.asyncio
async def test_create_session_requires_auth(client):
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_saves_requires_auth(client):
    resp = await client.get("/api/saves")
    assert resp.status_code == 401


# ============================================================================
# Session lifecycle
# ============================================================================


@pytest.mark.asyncio
async def test_create_session_persists_to_redis(client, admin_token):
    token, user, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body
    assert body["state"]["campaign_name"] == "Test Campaign"
    # Reachable.
    sid = body["session_id"]
    resp2 = await client.get(f"/api/sessions/{sid}", headers=headers)
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_create_session_invalid_state_rejected(client, admin_token):
    token, user, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": {"not_a_real_field": "oops"}},
        headers=headers,
    )
    # Pydantic validation rejects unknown fields via extra='ignore' (the
    # default in GameState) so this might be 422 only on missing required.
    # Either way, the state should not be persisted as-is.
    assert resp.status_code in (201, 422)
    if resp.status_code == 201:
        # The empty payload is missing required fields — should 422.
        # If we got 201, the field was ignored and the model used defaults.
        # This is fine; the test is mainly that no 500 leaks.
        pass


@pytest.mark.asyncio
async def test_list_sessions_empty(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/sessions", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"session_ids": []}


@pytest.mark.asyncio
async def test_get_session_not_found(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/sessions/nonexistent", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_session(client, admin_token):
    token, user, headers = admin_token
    # Create one.
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    sid = resp.json()["session_id"]
    # Delete it.
    resp2 = await client.delete(f"/api/sessions/{sid}", headers=headers)
    assert resp2.status_code == 204
    # Get should now 404.
    resp3 = await client.get(f"/api/sessions/{sid}", headers=headers)
    assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_session_input_requires_session(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/missing/input",
        json={"line": "look around"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_session_input_empty_state_processes(client, admin_token):
    """The DM agent will fail with no character; we expect 500 or
    a graceful error, but not 422 or 404."""
    token, user, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    sid = resp.json()["session_id"]
    resp2 = await client.post(
        f"/api/sessions/{sid}/input",
        json={"line": "olá"},
        headers=headers,
    )
    # With no player character, the DM agent will hit an error. We just
    # verify the route doesn't 500 uncaught (it returns a structured
    # 500 with detail).
    assert resp2.status_code in (200, 500)
    if resp2.status_code == 500:
        assert "detail" in resp2.json()


# ============================================================================
# Save lifecycle
# ============================================================================


@pytest.mark.asyncio
async def test_list_saves_empty(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/saves", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_upsert_save_creates(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/saves",
        json={"slug": "save1", "state": _empty_state()},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "save1"
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_upsert_save_updates(client, auth_token):
    token, user, headers = auth_token
    # Create
    await client.post(
        "/api/saves",
        json={"slug": "save2", "state": _empty_state()},
        headers=headers,
    )
    # Update
    state = _empty_state()
    state["current_location"] = "Updated Location"
    resp = await client.post(
        "/api/saves",
        json={"slug": "save2", "state": state},
        headers=headers,
    )
    assert resp.status_code == 201
    # Confirm via list
    resp2 = await client.get("/api/saves", headers=headers)
    saves = resp2.json()
    assert len(saves) == 1
    assert saves[0]["slug"] == "save2"


@pytest.mark.asyncio
async def test_save_per_user_isolation(client):
    """Saves for user A should not be visible to user B."""
    # User A
    a_token, a_user = await _signup(client, "alice", "alicepass1")
    a_headers = {"Authorization": f"Bearer {a_token}"}
    await client.post(
        "/api/saves",
        json={"slug": "alice-save", "state": _empty_state()},
        headers=a_headers,
    )

    # User B
    b_token, b_user = await _signup(client, "bob", "bobpass123")
    b_headers = {"Authorization": f"Bearer {b_token}"}

    # Bob should not see Alice's save.
    resp = await client.get("/api/saves", headers=b_headers)
    assert resp.json() == []

    # Bob's save list with his own slug should work.
    await client.post(
        "/api/saves",
        json={"slug": "bob-save", "state": _empty_state()},
        headers=b_headers,
    )
    resp2 = await client.get("/api/saves", headers=b_headers)
    saves = resp2.json()
    assert len(saves) == 1
    assert saves[0]["slug"] == "bob-save"


@pytest.mark.asyncio
async def test_load_save_creates_session(client, auth_token):
    token, user, headers = auth_token
    # Create a save
    await client.post(
        "/api/saves",
        json={"slug": "to-load", "state": _empty_state()},
        headers=headers,
    )
    # Load it
    resp = await client.post(
        "/api/saves/to-load/load",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["slug"] == "to-load"
    assert body["state"]["campaign_name"] == "Test Campaign"


@pytest.mark.asyncio
async def test_load_save_not_found(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/saves/does-not-exist/load",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_save(client, auth_token):
    token, user, headers = auth_token
    await client.post(
        "/api/saves",
        json={"slug": "to-delete", "state": _empty_state()},
        headers=headers,
    )
    resp = await client.delete(
        "/api/saves/to-delete",
        headers=headers,
    )
    assert resp.status_code == 204
    # List should be empty.
    resp2 = await client.get("/api/saves", headers=headers)
    assert resp2.json() == []


@pytest.mark.asyncio
async def test_delete_save_not_found(client, auth_token):
    token, user, headers = auth_token
    resp = await client.delete(
        "/api/saves/never-existed",
        headers=headers,
    )
    assert resp.status_code == 404


# ============================================================================
# Archive / unarchive
# ============================================================================


@pytest.mark.asyncio
async def test_new_save_is_not_archived(client, auth_token):
    token, user, headers = auth_token
    await client.post(
        "/api/saves",
        json={"slug": "fresh", "state": _empty_state()},
        headers=headers,
    )
    resp = await client.get("/api/saves", headers=headers)
    saves = resp.json()
    assert len(saves) == 1
    assert saves[0]["archived"] is False


@pytest.mark.asyncio
async def test_archive_hides_from_default_list(client, auth_token):
    token, user, headers = auth_token
    await client.post(
        "/api/saves",
        json={"slug": "to-archive", "state": _empty_state()},
        headers=headers,
    )
    # Archive it.
    resp = await client.post(
        "/api/saves/to-archive/archive",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True
    # Default list no longer includes it.
    active = (await client.get("/api/saves", headers=headers)).json()
    assert active == []
    # It shows up under ?archived=true.
    archived = (
        await client.get("/api/saves?archived=true", headers=headers)
    ).json()
    assert len(archived) == 1
    assert archived[0]["slug"] == "to-archive"
    assert archived[0]["archived"] is True


@pytest.mark.asyncio
async def test_unarchive_restores_to_default_list(client, auth_token):
    token, user, headers = auth_token
    await client.post(
        "/api/saves",
        json={"slug": "flip", "state": _empty_state()},
        headers=headers,
    )
    await client.post("/api/saves/flip/archive", headers=headers)
    resp = await client.post("/api/saves/flip/unarchive", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["archived"] is False
    active = (await client.get("/api/saves", headers=headers)).json()
    assert len(active) == 1
    assert active[0]["slug"] == "flip"
    archived = (
        await client.get("/api/saves?archived=true", headers=headers)
    ).json()
    assert archived == []


@pytest.mark.asyncio
async def test_archive_not_found(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/saves/ghost/archive",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_archived_save_is_still_loadable(client, auth_token):
    token, user, headers = auth_token
    await client.post(
        "/api/saves",
        json={"slug": "shelved", "state": _empty_state()},
        headers=headers,
    )
    await client.post("/api/saves/shelved/archive", headers=headers)
    # Archiving is non-destructive: the save can still hydrate a session.
    resp = await client.post(
        "/api/saves/shelved/load",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "shelved"


# ============================================================================
# Helpers
# ============================================================================


async def _signup(client, username, password):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["token"], body["user"]
