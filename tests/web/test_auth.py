"""Tests for /api/auth/* endpoints (Phase 26a)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_signup_returns_token(client):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": "supersecret"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "token" in body
    assert body["user"]["username"] == "alice"
    assert "id" in body["user"]
    assert "expires_in_minutes" in body


@pytest.mark.asyncio
async def test_signup_duplicate_username_rejected(client):
    await client.post(
        "/api/auth/signup",
        json={"username": "bob", "password": "abcdefgh"},
    )
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "bob", "password": "different"},
    )
    assert resp.status_code == 409
    assert "taken" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_short_password_rejected(client):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "short", "password": "x"},
    )
    assert resp.status_code == 422  # pydantic validation


@pytest.mark.asyncio
async def test_signup_invalid_username_rejected(client):
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "has space", "password": "abcdefgh"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client):
    await client.post(
        "/api/auth/signup",
        json={"username": "carol", "password": "carolpass"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "carolpass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert body["user"]["username"] == "carol"


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client):
    await client.post(
        "/api/auth/signup",
        json={"username": "dave", "password": "davepass1"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "wrongpass"},
    )
    assert resp.status_code == 401
    # Same error message whether user exists or not.
    assert "invalid" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_nonexistent_user_rejected(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "doesntmatter"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_user(auth_token):
    token, user, headers = auth_token
    from httpx import ASGITransport, AsyncClient
    from auto_dm.web.server import create_app
    from auto_dm.web.sessions import SessionManager
    from auto_dm.web import db as web_db
    from auto_dm.web import redis_client as web_redis
    import fakeredis.aioredis

    # The auth_token fixture used a different client; we need a fresh
    # app to call /me. Simpler: pass the token through a new client.
    # We rebuild app with the same in-memory DB.
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    web_redis._client = fake

    def factory():
        class _S:
            pass

        return _S()

    app = create_app(provider_factory=factory)
    app.state.session_manager = SessionManager(provider_factory=factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/auth/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == user["username"]
    await fake.aclose()
    web_redis._client = None
    await web_db.dispose_engine()


@pytest.mark.asyncio
async def test_me_invalid_token_rejected(client):
    resp = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_password_hashing_unique_per_call():
    """Bcrypt salts random — same password hashes differently."""
    from auto_dm.web.auth import hash_password
    a = hash_password("samepass")
    b = hash_password("samepass")
    assert a != b


@pytest.mark.asyncio
async def test_password_verify_roundtrip():
    from auto_dm.web.auth import hash_password, verify_password
    h = hash_password("mypassword")
    assert verify_password("mypassword", h) is True
    assert verify_password("wrongpassword", h) is False
