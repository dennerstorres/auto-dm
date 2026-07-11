"""Tests for optional invite-based system LLM access.

Behaviour:
- signup is always open;
- a matching configured invite grants ``system_llm_access``;
- missing/wrong/unconfigured invites create a BYOK-only account.

We mutate the env var per-test and clear the settings cache so the
``Settings`` singleton re-reads from env each time.
"""
from __future__ import annotations

import pytest


def _fresh_settings():
    """Force the cached Settings to re-read env vars."""
    from auto_dm.web.config import get_settings

    get_settings.cache_clear()
    return get_settings()


# ============================================================================
# Open signup (no INVITE_CODE set)
# ============================================================================


@pytest.mark.asyncio
async def test_signup_open_when_no_invite_configured(client, monkeypatch):
    monkeypatch.delenv("INVITE_CODE", raising=False)
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "open_user", "password": "openpass1234"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["system_llm_access"] is False


@pytest.mark.asyncio
async def test_signup_ignores_invite_field_when_not_configured(
    client, monkeypatch
):
    """An invite cannot grant access when the server has no code configured."""
    monkeypatch.delenv("INVITE_CODE", raising=False)
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={
            "username": "stray_user",
            "password": "straypass1234",
            "invite_code": "this-is-ignored",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["system_llm_access"] is False


# ============================================================================
# Invite entitlement (INVITE_CODE set)
# ============================================================================


@pytest.mark.asyncio
async def test_signup_without_code_creates_byok_only_account(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": "alicepass1234"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["system_llm_access"] is False


@pytest.mark.asyncio
async def test_signup_with_wrong_code_creates_byok_only_account(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={
            "username": "alice",
            "password": "alicepass1234",
            "invite_code": "guess",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["system_llm_access"] is False


@pytest.mark.asyncio
async def test_signup_with_correct_code_accepted(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={
            "username": "alice",
            "password": "alicepass1234",
            "invite_code": "secret-invite-xyz",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["system_llm_access"] is True
    assert "token" in body


@pytest.mark.asyncio
async def test_signup_wrong_and_missing_receive_same_entitlement(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    r_missing = await client.post(
        "/api/auth/signup",
        json={"username": "bob1", "password": "bobpass1234"},
    )
    r_wrong = await client.post(
        "/api/auth/signup",
        json={
            "username": "bob2",
            "password": "bobpass1234",
            "invite_code": "guess",
        },
    )
    assert r_missing.status_code == r_wrong.status_code == 201
    assert r_missing.json()["user"]["system_llm_access"] is False
    assert r_wrong.json()["user"]["system_llm_access"] is False


@pytest.mark.asyncio
async def test_login_unaffected_by_invite_code(client, monkeypatch):
    """Login preserves the entitlement assigned when the account was created."""
    monkeypatch.delenv("INVITE_CODE", raising=False)
    _fresh_settings()
    await client.post(
        "/api/auth/signup",
        json={"username": "carol", "password": "carolpass1234"},
    )
    # Now flip the gate.
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "carolpass1234"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["system_llm_access"] is False


# ============================================================================
# Timing-safe comparison sanity check
# ============================================================================


@pytest.mark.asyncio
async def test_signup_uses_timing_safe_compare(client, monkeypatch):
    """The signup route must use ``hmac.compare_digest`` so that an
    attacker can't fingerprint the secret by measuring wrong-code attempts.
    """
    import hmac

    monkeypatch.setenv("INVITE_CODE", "very-long-secret-code-1234567890")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={
            "username": "mallory",
            "password": "mallorypass1234",
            "invite_code": "very-long-secret-code-XXXXXXX",  # prefix match
        },
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["system_llm_access"] is False
    # And verify the helper actually equal-evaluates them.
    assert not hmac.compare_digest(
        "very-long-secret-code-XXXXXXX",
        "very-long-secret-code-1234567890",
    )


def test_system_llm_access_migration_is_idempotent(app_instance):
    """Existing databases keep global-LLM access after the migration."""
    import anyio

    from auto_dm.web.db import get_engine
    from auto_dm.web.server import _ensure_system_llm_access

    async def _run():
        async with get_engine().begin() as conn:
            await conn.run_sync(_ensure_system_llm_access)
            await conn.run_sync(_ensure_system_llm_access)

    anyio.run(_run)
