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
    """Create a test user and return (token, user, headers)."""
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "testuser", "password": "testpass1234"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["token"], data["user"], {"Authorization": f"Bearer {data['token']}"}
