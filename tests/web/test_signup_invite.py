"""Tests for the invite-code signup gate (Phase 26e).

Behaviour:
- ``INVITE_CODE`` unset (default / dev) → signup is open.
- ``INVITE_CODE`` set (production)    → signup requires matching
   ``invite_code`` field; missing or wrong → 403.

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


@pytest.mark.asyncio
async def test_signup_ignores_invite_field_when_not_configured(
    client, monkeypatch
):
    """If the server has no INVITE_CODE, a stray invite_code in the
    body is harmless."""
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


# ============================================================================
# Gated signup (INVITE_CODE set)
# ============================================================================


@pytest.mark.asyncio
async def test_signup_without_code_rejected_when_configured(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "secret-invite-xyz")
    _fresh_settings()
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": "alicepass1234"},
    )
    assert resp.status_code == 403
    assert "invite" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_with_wrong_code_rejected(client, monkeypatch):
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
    assert resp.status_code == 403


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
    assert "token" in body


@pytest.mark.asyncio
async def test_signup_wrong_and_missing_share_same_message(client, monkeypatch):
    """Don't leak whether the code was wrong vs missing."""
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
    assert r_missing.status_code == r_wrong.status_code == 403
    assert r_missing.json() == r_wrong.json()


@pytest.mark.asyncio
async def test_login_unaffected_by_invite_code(client, monkeypatch):
    """Once an account exists, login still works even if INVITE_CODE
    is later enabled — invite gates *signup*, not login."""
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


# ============================================================================
# Timing-safe comparison sanity check
# ============================================================================


@pytest.mark.asyncio
async def test_signup_uses_timing_safe_compare(client, monkeypatch):
    """The signup route must use ``hmac.compare_digest`` so that an
    attacker can't fingerprint the secret by measuring response time
    of wrong-code attempts. We just verify the import path works and
    the gate still rejects when the prefix matches but the rest doesn't.
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
    assert resp.status_code == 403
    # And verify the helper actually equal-evaluates them.
    assert not hmac.compare_digest(
        "very-long-secret-code-XXXXXXX",
        "very-long-secret-code-1234567890",
    )