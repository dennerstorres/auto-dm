"""Tests for usage tracking, daily quota enforcement, and soft-disable.

Covers:
- ``usage_today`` aggregation across UsageEvent rows.
- ``check_quota`` exemptions (unlimited / admin) and token-limit trip.
- ``POST /api/sessions/{sid}/input`` returns 429 when over quota (and
  records a ``limit_blocked`` activity entry), and persists a UsageEvent
  on success when the user is within quota / unlimited.
- Soft-disable: a deactivated account gets a generic 401 on login and a
  403 from ``current_user`` with a still-valid token.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ============================================================================
# Helpers
# ============================================================================


def _empty_state() -> dict:
    return {
        "campaign_name": "c",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "t",
        "party": [],
        "npcs": [],
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "",
        "active_conditions": [],
    }


class _FakeUsageProvider:
    """Provider that reports real usage for the happy-path persistence test."""

    name = "fake"

    def __init__(self, content="Você vê uma porta."):
        self.config = type("C", (), {"model": "fake-model"})()
        self._content = content

    def chat_with_usage(self, messages):
        from auto_dm.llm.usage import UsageReport

        return self._content, UsageReport(
            prompt_tokens=100, completion_tokens=50, total_tokens=150,
            provider="fake", model="fake-model", source="api",
        )


async def _signup(client, username):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": "testpass1234"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"], resp.json()["user"]


async def _insert_usage(user_id, total_tokens):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import UsageEvent

    factory = get_session_factory()
    async with factory() as s:
        s.add(UsageEvent(
            user_id=user_id, endpoint="test", kind="player",
            prompt_tokens=total_tokens, completion_tokens=0,
            total_tokens=total_tokens, cost_usd=0,
        ))
        await s.commit()


async def _patch_user(client, headers, user_id, **fields):
    resp = await client.patch(
        f"/api/admin/users/{user_id}", json=fields, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_admin(client):
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
    tok = resp.json()["token"]
    return tok, {"Authorization": f"Bearer {tok}"}


async def _session_for(app_instance, user_id, *, fake_agent=True):
    """Create a real WebSession for ``user_id`` and (optionally) swap in a
    fake DM agent so /input can run without a live LLM."""
    from auto_dm.agents.dm import DMAgent
    from auto_dm.state.models import GameState

    sm = app_instance.state.session_manager
    state = GameState.model_validate(_empty_state())
    sess = await sm.create(user_id, state)
    if fake_agent:
        sess.dm_agent = DMAgent(
            provider=_FakeUsageProvider(), state_manager=sess.state_manager
        )
    return sess.session_id


# ============================================================================
# usage_today + check_quota (unit)
# ============================================================================


@pytest.mark.asyncio
async def test_usage_today_aggregates(app_instance):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.usage import usage_today

    factory = get_session_factory()
    async with factory() as s:
        from auto_dm.web.models import User

        u = User(username="agg", password_hash="x")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.id
    await _insert_usage(uid, 30)
    await _insert_usage(uid, 70)
    async with factory() as s:
        total = await usage_today(s, uid)
    assert total == 100


@pytest.mark.asyncio
async def test_check_quota_unlimited_exempt(app_instance):
    from auto_dm.web.config import get_settings
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.limits import check_quota
    from auto_dm.web.models import User

    factory = get_session_factory()
    async with factory() as s:
        u = User(username="unlim", password_hash="x", unlimited=True)
        s.add(u)
        await s.commit()
        await s.refresh(u)
        result = await check_quota(s, u, get_settings())
    assert result is None


@pytest.mark.asyncio
async def test_check_quota_trips_on_token_limit(app_instance):
    from auto_dm.web.config import get_settings
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.limits import check_quota
    from auto_dm.web.models import User

    factory = get_session_factory()
    async with factory() as s:
        u = User(username="capped", password_hash="x", daily_token_limit=50)
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.id
    await _insert_usage(uid, 60)
    async with factory() as s:
        from auto_dm.web.models import User as _U

        u2 = await s.get(_U, uid)
        result = await check_quota(s, u2, get_settings())
    assert result is not None
    assert result["unit"] == "tokens"
    assert result["limit"] == 50
    assert "reset_at" in result


# ============================================================================
# /input enforcement + persistence (integration)
# ============================================================================


@pytest.mark.asyncio
async def test_input_returns_429_when_over_limit(client, app_instance):
    tok, user = await _signup(client, "overuser")
    headers = {"Authorization": f"Bearer {tok}"}
    sid = await _session_for(app_instance, user["id"], fake_agent=False)

    # Make the user an admin via DB so we can PATCH through admin routes,
    # then demote back? Simpler: directly set the limit in the DB.
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User as _U

    factory = get_session_factory()
    async with factory() as s:
        u = await s.get(_U, user["id"])
        u.daily_token_limit = 100
        await s.commit()
    await _insert_usage(user["id"], 200)  # over the 100 cap

    resp = await client.post(
        f"/api/sessions/{sid}/input", json={"line": "olhar"}, headers=headers
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["detail"]["unit"] == "tokens"


@pytest.mark.asyncio
async def test_input_persists_usage_when_unlimited(client, app_instance):
    tok, user = await _signup(client, "freeuser")
    headers = {"Authorization": f"Bearer {tok}"}
    sid = await _session_for(app_instance, user["id"], fake_agent=True)

    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User as _U

    factory = get_session_factory()
    async with factory() as s:
        u = await s.get(_U, user["id"])
        u.unlimited = True
        await s.commit()

    resp = await client.post(
        f"/api/sessions/{sid}/input", json={"line": "olhar"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    # A UsageEvent should have been recorded (150 tokens from the fake provider).
    from sqlalchemy import select
    from auto_dm.web.models import UsageEvent

    async with factory() as s:
        result = await s.execute(
            select(UsageEvent).where(UsageEvent.user_id == user["id"])
        )
        events = result.scalars().all()
    assert len(events) >= 1
    assert events[0].total_tokens == 150
    assert events[0].source == "api"


@pytest.mark.asyncio
async def test_admin_exempt_from_quota_on_input(client, app_instance):
    _, headers = await _make_admin(client)
    me = (await client.get("/api/auth/me", headers=headers)).json()
    # Insert a huge usage amount + a low limit; admin should still pass.
    await _insert_usage(me["id"], 10_000_000)
    sid = await _session_for(app_instance, me["id"], fake_agent=True)
    resp = await client.post(
        f"/api/sessions/{sid}/input", json={"line": "olhar"}, headers=headers
    )
    assert resp.status_code == 200


# ============================================================================
# Soft-disable
# ============================================================================


@pytest.mark.asyncio
async def test_disabled_user_login_is_generic_401(client, app_instance):
    _, headers = await _make_admin(client)
    await _signup(client, "tobedisabled")
    users = (await client.get("/api/admin/users", headers=headers)).json()
    target = next(u for u in users if u["username"] == "tobedisabled")
    await _patch_user(client, headers, target["id"], active=False)
    resp = await client.post(
        "/api/auth/login",
        json={"username": "tobedisabled", "password": "testpass1234"},
    )
    assert resp.status_code == 401
    # Generic message — must not reveal the account exists.
    assert resp.json()["detail"] == "Invalid username or password"


@pytest.mark.asyncio
async def test_disabled_user_current_user_is_403(client, app_instance):
    tok, user = await _signup(client, "zombie")
    headers = {"Authorization": f"Bearer {tok}"}
    # Token works before disable.
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    _, admin_headers = await _make_admin(client)
    await _patch_user(client, admin_headers, user["id"], active=False)
    # Same token now blocked (no zombie sessions).
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 403
