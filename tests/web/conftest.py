"""Shared fixtures for Phase 26 web tests."""
from __future__ import annotations

import os
from typing import AsyncIterator

import fakeredis.aioredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Force a known env so settings don't fail when no .env exists.
os.environ.setdefault("JWT_SECRET", "test-secret-must-be-at-least-32-bytes-long-yes")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
# Phase 51d-lite — the per-user resolver calls ``LLMConfig.from_env`` when
# the user is in legacy mode (the default during migration). The legacy
# env vars are dummy here — tests that actually invoke LLM stub the
# provider factory via SessionManager overrides; tests that don't invoke
# LLM just need the resolver to return a context, never to send a request.
os.environ.setdefault("AUTO_DM_PROVIDER", "minimax")
os.environ.setdefault("AUTO_DM_API_KEY", "test-key-not-real")
os.environ.setdefault("AUTO_DM_MODEL", "MiniMax-M3")

from auto_dm.web import db as web_db
from auto_dm.web import redis_client as web_redis
from auto_dm.web.config import get_settings
from auto_dm.web.models import Base
from auto_dm.web.server import create_app
from auto_dm.web.sessions import SessionManager


def _stub_provider_factory():
    """Returns a no-op LLM provider. Tests that exercise DM logic
    should stub the DMAgent directly; this just satisfies SessionManager."""

    class _Stub:
        pass

    return _Stub()


@pytest_asyncio.fixture
async def app_instance(monkeypatch):
    """Build a FastAPI app with an in-memory SQLite + fakeredis.

    We bypass the FastAPI lifespan (httpx ASGITransport doesn't run it
    by default) and initialize engine/redis/SessionManager manually.

    The ``monkeypatch`` parameter lets individual tests set/clear env
    vars (notably ``INVITE_CODE``) before settings are read.
    """
    # 1) Initialize SQLite engine + create tables.
    web_db.init_engine(
        database_url="sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    engine = web_db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) Initialize fakeredis (bypass init_redis which uses real client).
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    web_redis._client = fake

    # 3) Build a minimal provider factory.
    factory = _stub_provider_factory

    # 4) Build app and attach SessionManager.
    app = create_app(provider_factory=factory)
    sm = SessionManager(provider_factory=factory)
    app.state.session_manager = sm
    # Also seed the global _state in server module so route deps that
    # call get_app_state() can find it.
    import auto_dm.web.server as srv
    from auto_dm.web.server import AppState
    srv._state = AppState(session_manager=sm, provider_factory=factory)

    yield app

    # Cleanup
    await fake.aclose()
    await web_db.dispose_engine()
    web_redis._client = None
    srv._state = None
    # Reset the cached settings so other tests get fresh.
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    """An httpx AsyncClient wired to the FastAPI app via ASGITransport."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_token(client: AsyncClient):
    """Create an invited test user and return (token, user, headers).

    Most pre-entitlement tests exercise the historical system-LLM path, so
    this shared fixture grants that capability directly. Invite-specific
    tests create their own public users through the signup endpoint.
    """
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "testuser", "password": "testpass1234"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User

    async with get_session_factory()() as session:
        stored = await session.get(User, data["user"]["id"])
        stored.system_llm_access = True
        await session.commit()
    login = await client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "testpass1234"},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    return data["token"], data["user"], {"Authorization": f"Bearer {data['token']}"}


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, app_instance):
    """Create an admin user directly in the DB and log in.

    Signup always creates ``role=user`` (never admin), so we insert the
    admin row by hand and authenticate via /login. Returns
    (token, user, headers).
    """
    from auto_dm.web.auth import hash_password
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole

    factory = get_session_factory()
    async with factory() as session:
        session.add(
            User(
                username="rootadmin",
                password_hash=hash_password("adminpass1234"),
                role=UserRole.ADMIN.value,
            )
        )
        await session.commit()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "rootadmin", "password": "adminpass1234"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["token"], data["user"], {"Authorization": f"Bearer {data['token']}"}
