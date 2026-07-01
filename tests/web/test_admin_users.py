"""Tests for admin user-management endpoints (Phase 30).

Covers the ``/api/admin/users`` CRUD + protections, the usage summary,
and the per-user activity/usage series. Quota enforcement and soft-
disable are in ``test_usage_limits.py``.
"""
from __future__ import annotations

import pytest


# ============================================================================
# Helpers
# ============================================================================


async def _make_admin(client):
    """Insert an admin directly and log in (signup can't create admins)."""
    from auto_dm.web.auth import hash_password
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole

    factory = get_session_factory()
    async with factory() as s:
        s.add(User(
            username="rootadmin",
            password_hash=hash_password("adminpass1234"),
            role=UserRole.ADMIN.value,
        ))
        await s.commit()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "rootadmin", "password": "adminpass1234"},
    )
    assert resp.status_code == 200, resp.text
    tok = resp.json()["token"]
    return tok, {"Authorization": f"Bearer {tok}"}


async def _signup(client, username="plainuser"):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": "testpass1234"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


async def _db_user_count():
    from sqlalchemy import func, select
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User

    factory = get_session_factory()
    async with factory() as s:
        result = await s.execute(select(func.count(User.id)))
        return int(result.scalar_one())


# ============================================================================
# Access control
# ============================================================================


@pytest.mark.asyncio
async def test_admin_users_requires_admin(client):
    tok = await _signup(client, "uu1")
    resp = await client.get(
        "/api/admin/users", headers={"Authorization": f"Bearer {tok}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_users_requires_auth(client):
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 401


# ============================================================================
# List + create
# ============================================================================


@pytest.mark.asyncio
async def test_list_users_returns_all(client):
    await _signup(client, "alice")
    await _signup(client, "bob")
    _, headers = await _make_admin(client)
    resp = await client.get("/api/admin/users", headers=headers)
    assert resp.status_code == 200
    names = {u["username"] for u in resp.json()}
    assert {"alice", "bob", "rootadmin"} <= names
    # Aggregations present.
    u0 = resp.json()[0]
    assert "tokens_today" in u0 and "cost_month" in u0
    assert "active" in u0 and "unlimited" in u0


@pytest.mark.asyncio
async def test_create_user(client):
    _, headers = await _make_admin(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "newbie", "password": "newbiepass1"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "newbie"
    assert body["role"] == "user"
    assert body["active"] is True


@pytest.mark.asyncio
async def test_create_user_duplicate_409(client):
    _, headers = await _make_admin(client)
    await client.post(
        "/api/admin/users",
        json={"username": "dup", "password": "duppass1234"},
        headers=headers,
    )
    resp = await client.post(
        "/api/admin/users",
        json={"username": "dup", "password": "duppass1234"},
        headers=headers,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_user_admin_role(client):
    _, headers = await _make_admin(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "boss", "password": "bosspass1234", "role": "admin"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "admin"


# ============================================================================
# Detail + patch
# ============================================================================


@pytest.mark.asyncio
async def test_get_user_detail(client):
    await _signup(client, "carol")
    _, headers = await _make_admin(client)
    users = (await client.get("/api/admin/users", headers=headers)).json()
    carol = next(u for u in users if u["username"] == "carol")
    resp = await client.get(f"/api/admin/users/{carol['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "carol"


@pytest.mark.asyncio
async def test_get_user_404(client):
    _, headers = await _make_admin(client)
    resp = await client.get("/api/admin/users/99999", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_user_limits(client):
    await _signup(client, "dave")
    _, headers = await _make_admin(client)
    users = (await client.get("/api/admin/users", headers=headers)).json()
    dave = next(u for u in users if u["username"] == "dave")
    resp = await client.patch(
        f"/api/admin/users/{dave['id']}",
        json={"daily_token_limit": 5000, "unlimited": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["daily_token_limit"] == 5000
    assert body["unlimited"] is False


@pytest.mark.asyncio
async def test_patch_unlimited_exempt(client):
    await _signup(client, "erin")
    _, headers = await _make_admin(client)
    users = (await client.get("/api/admin/users", headers=headers)).json()
    erin = next(u for u in users if u["username"] == "erin")
    resp = await client.patch(
        f"/api/admin/users/{erin['id']}",
        json={"unlimited": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["unlimited"] is True


# ============================================================================
# Reset password
# ============================================================================


@pytest.mark.asyncio
async def test_reset_password_then_login(client):
    await _signup(client, "frank")
    _, headers = await _make_admin(client)
    users = (await client.get("/api/admin/users", headers=headers)).json()
    frank = next(u for u in users if u["username"] == "frank")
    resp = await client.post(
        f"/api/admin/users/{frank['id']}/reset-password",
        json={"new_password": "brand-new-pass-9"},
        headers=headers,
    )
    assert resp.status_code == 204
    # Old password no longer works.
    old = await client.post(
        "/api/auth/login", json={"username": "frank", "password": "testpass1234"}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/auth/login",
        json={"username": "frank", "password": "brand-new-pass-9"},
    )
    assert new.status_code == 200


# ============================================================================
# Delete + protections
# ============================================================================


@pytest.mark.asyncio
async def test_delete_user_cascade(client):
    await _signup(client, "gina")
    _, headers = await _make_admin(client)
    users = (await client.get("/api/admin/users", headers=headers)).json()
    gina = next(u for u in users if u["username"] == "gina")
    resp = await client.delete(f"/api/admin/users/{gina['id']}", headers=headers)
    assert resp.status_code == 204
    # Gone.
    resp2 = await client.get(f"/api/admin/users/{gina['id']}", headers=headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_cannot_delete_self(client):
    _, headers = await _make_admin(client)
    me = (await client.get("/api/auth/me", headers=headers)).json()
    resp = await client.delete(f"/api/admin/users/{me['id']}", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cannot_disable_last_admin(client):
    _, headers = await _make_admin(client)
    me = (await client.get("/api/auth/me", headers=headers)).json()
    resp = await client.patch(
        f"/api/admin/users/{me['id']}",
        json={"active": False},
        headers=headers,
    )
    # Self-deactivation is blocked outright.
    assert resp.status_code == 409


# ============================================================================
# Activity + summary
# ============================================================================


@pytest.mark.asyncio
async def test_user_activity_lists_login(client):
    _, headers = await _make_admin(client)
    me = (await client.get("/api/auth/me", headers=headers)).json()
    resp = await client.get(
        f"/api/admin/users/{me['id']}/activity", headers=headers
    )
    assert resp.status_code == 200
    events = resp.json()["activity"]
    assert any(e["event_type"] == "login" for e in events)


@pytest.mark.asyncio
async def test_usage_summary(client):
    _, headers = await _make_admin(client)
    resp = await client.get("/api/admin/usage/summary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    for k in ("cost_usd", "tokens", "active_users", "disabled_users", "top_users"):
        assert k in body
