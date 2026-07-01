"""Tests for user roles + admin features (Phase 29).

Covers:
- ``UserOut.role`` exposed on signup/login/me.
- Signup always creates ``role=user`` (cannot escalate via the body).
- ``POST /api/sessions`` (Criar jogo vazio) is admin-only.
- Admin router: regular users get 403; admin can list all saves (with
  owner), inspect any save's state + narrative log read-only, and delete
  any user's save.
- Startup admin seed (``_seed_admin``) + ``_ensure_user_role`` migration.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ============================================================================
# Helpers
# ============================================================================


def _empty_state() -> dict:
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


def _state_with_log() -> dict:
    """A GameState with a couple of narrative-log entries."""
    state = _empty_state()
    state["narrative_log"] = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "dm",
            "speaker": "DM",
            "content": "Você vê uma porta.",
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "player",
            "speaker": "Jogador",
            "content": "Abro a porta.",
        },
    ]
    return state


async def _signup(client, username: str, password: str = "testpass1234"):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["token"], body["user"], {"Authorization": f"Bearer {body['token']}"}


# ============================================================================
# UserOut.role + signup hardcodes user role
# ============================================================================


@pytest.mark.asyncio
async def test_signup_returns_user_role(client):
    _, user, _ = await _signup(client, "plainuser")
    assert user["role"] == "user"


@pytest.mark.asyncio
async def test_login_returns_role(client):
    await _signup(client, "loginuser")
    resp = await client.post(
        "/api/auth/login",
        json={"username": "loginuser", "password": "testpass1234"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "user"


@pytest.mark.asyncio
async def test_me_returns_role(client):
    _, _, headers = await _signup(client, "meuser")
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


@pytest.mark.asyncio
async def test_admin_token_fixture_is_admin(admin_token):
    _, user, _ = admin_token
    assert user["role"] == "admin"


@pytest.mark.asyncio
async def test_signup_cannot_escalate_role(client):
    """Sending role:'admin' in the body is ignored — always user."""
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "sneaky", "password": "testpass1234", "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "user"


# ============================================================================
# POST /api/sessions (Criar jogo vazio) is admin-only
# ============================================================================


@pytest.mark.asyncio
async def test_create_session_forbidden_for_user(client, auth_token):
    _, _, headers = auth_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_session_allowed_for_admin(client, admin_token):
    _, _, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    assert resp.status_code == 201


# ============================================================================
# Admin router access control
# ============================================================================


@pytest.mark.asyncio
async def test_admin_routes_require_auth(client):
    assert (await client.get("/api/admin/saves")).status_code == 401


@pytest.mark.asyncio
async def test_admin_routes_forbidden_for_user(client, auth_token):
    _, _, headers = auth_token
    assert (await client.get("/api/admin/saves", headers=headers)).status_code == 403
    assert (
        await client.get("/api/admin/saves/1/slug", headers=headers)
    ).status_code == 403
    assert (
        await client.delete("/api/admin/saves/1/slug", headers=headers)
    ).status_code == 403


# ============================================================================
# Admin: list all saves (cross-user, with owner)
# ============================================================================


@pytest.mark.asyncio
async def test_admin_lists_all_users_saves(client, admin_token):
    _, _, admin_headers = admin_token
    # Two regular users, each with a save.
    _, alice, alice_h = await _signup(client, "alice")
    _, bob, bob_h = await _signup(client, "bob")
    await client.post(
        "/api/saves",
        json={"slug": "alice-save", "state": _empty_state()},
        headers=alice_h,
    )
    await client.post(
        "/api/saves",
        json={"slug": "bob-save", "state": _empty_state()},
        headers=bob_h,
    )
    # Alice only sees her own.
    alice_view = (await client.get("/api/saves", headers=alice_h)).json()
    assert {s["slug"] for s in alice_view} == {"alice-save"}
    # Admin sees both, with owning usernames.
    admin_view = (await client.get("/api/admin/saves", headers=admin_headers)).json()
    by_slug = {s["slug"]: s for s in admin_view}
    assert set(by_slug) >= {"alice-save", "bob-save"}
    assert by_slug["alice-save"]["username"] == "alice"
    assert by_slug["alice-save"]["user_id"] == alice["id"]
    assert by_slug["bob-save"]["username"] == "bob"


# ============================================================================
# Admin: read-only save inspection (state + narrative_log, no session/LLM)
# ============================================================================


@pytest.mark.asyncio
async def test_admin_inspect_returns_narrative_log(client, admin_token):
    _, _, admin_headers = admin_token
    _, alice, alice_h = await _signup(client, "alice2")
    await client.post(
        "/api/saves",
        json={"slug": "logged", "state": _state_with_log()},
        headers=alice_h,
    )
    resp = await client.get(
        f"/api/admin/saves/{alice['id']}/logged",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice2"
    assert body["slug"] == "logged"
    assert body["archived"] is False
    assert "state" in body
    # The narrative log is replayed for the read-only view.
    roles = [e["role"] for e in body["narrative_log"]]
    assert roles == ["dm", "player"]
    assert body["narrative_log"][0]["content"] == "Você vê uma porta."


@pytest.mark.asyncio
async def test_admin_inspect_not_found(client, admin_token):
    _, _, admin_headers = admin_token
    resp = await client.get("/api/admin/saves/9999/ghost", headers=admin_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_inspect_works_on_archived(client, admin_token):
    _, _, admin_headers = admin_token
    _, alice, alice_h = await _signup(client, "alice3")
    await client.post(
        "/api/saves",
        json={"slug": "shelved", "state": _empty_state()},
        headers=alice_h,
    )
    await client.post("/api/saves/shelved/archive", headers=alice_h)
    resp = await client.get(
        f"/api/admin/saves/{alice['id']}/shelved",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


# ============================================================================
# Admin: delete any user's save (archived or not)
# ============================================================================


@pytest.mark.asyncio
async def test_admin_deletes_other_users_save(client, admin_token):
    _, _, admin_headers = admin_token
    _, alice, alice_h = await _signup(client, "alice4")
    await client.post(
        "/api/saves",
        json={"slug": "killme", "state": _empty_state()},
        headers=alice_h,
    )
    resp = await client.delete(
        f"/api/admin/saves/{alice['id']}/killme",
        headers=admin_headers,
    )
    assert resp.status_code == 204
    # Alice can no longer see it.
    assert (await client.get("/api/saves", headers=alice_h)).json() == []


@pytest.mark.asyncio
async def test_admin_deletes_archived_save(client, admin_token):
    _, _, admin_headers = admin_token
    _, alice, alice_h = await _signup(client, "alice5")
    await client.post(
        "/api/saves",
        json={"slug": "archived-kill", "state": _empty_state()},
        headers=alice_h,
    )
    await client.post("/api/saves/archived-kill/archive", headers=alice_h)
    resp = await client.delete(
        f"/api/admin/saves/{alice['id']}/archived-kill",
        headers=admin_headers,
    )
    assert resp.status_code == 204
    archived = (
        await client.get("/api/saves?archived=true", headers=alice_h)
    ).json()
    assert archived == []


# ============================================================================
# Startup admin seed + role migration
# ============================================================================


@pytest.mark.asyncio
async def test_seed_admin_creates_when_password_set(app_instance, monkeypatch):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole
    from auto_dm.web.server import _seed_admin

    settings = type("S", (), {})()
    settings.admin_username = "seedadmin"
    settings.admin_password = "seedpass1234"

    await _seed_admin(settings)

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(User).where(User.role == UserRole.ADMIN.value)
        )
        admin = result.scalar_one_or_none()
    assert admin is not None
    assert admin.username == "seedadmin"


@pytest.mark.asyncio
async def test_seed_admin_skips_when_no_password(app_instance):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole
    from auto_dm.web.server import _seed_admin

    settings = type("S", (), {})()
    settings.admin_username = "nosetadmin"
    settings.admin_password = None

    await _seed_admin(settings)

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(User).where(User.role == UserRole.ADMIN.value)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_seed_admin_is_idempotent(app_instance):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole
    from auto_dm.web.server import _seed_admin

    settings = type("S", (), {})()
    settings.admin_username = "idemadmin"
    settings.admin_password = "idempass1234"

    await _seed_admin(settings)
    await _seed_admin(settings)  # second call must not duplicate.

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select, func

        count = await session.scalar(
            select(func.count()).select_from(
                select(User).where(User.role == UserRole.ADMIN.value).subquery()
            )
        )
    assert count == 1


def test_ensure_user_role_is_idempotent(app_instance):
    """Calling the migration on a table that already has the column is a no-op."""
    from auto_dm.web.db import get_engine
    from auto_dm.web.server import _ensure_user_role
    import anyio

    async def _run():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_ensure_user_role)

    anyio.run(_run)
