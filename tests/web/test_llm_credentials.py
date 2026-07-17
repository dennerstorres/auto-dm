"""Phase 51b — BYOK credentials + settings endpoint tests.

Covers the security invariants: the plaintext key is never returned (grep
across every response), cross-user access is 404 (anti-enumeration), crypto
gating (503), flag gating (403), provider/model allowlist (422), and the
credential lifecycle (store / validate / remove).
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from auto_dm.web.config import get_settings


def _fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture(autouse=True)
def _byok_env(monkeypatch):
    """Enable BYOK with a real master key; open signup for the fixtures."""
    monkeypatch.setenv("INVITE_CODE", "")
    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "1")
    monkeypatch.setenv("AUTO_DM_CREDENTIALS_KEY", f"1:{_fernet_key()}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_get_settings_defaults_to_legacy(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/me/llm-settings", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "legacy"
    assert data["provider"] is None
    assert data["credentials"] == {}


async def test_put_settings_byok_roundtrip(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok", "provider": "anthropic", "model": "claude-sonnet-5",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "byok"
    assert data["provider"] == "anthropic"
    assert data["model"] == "claude-sonnet-5"

    resp = await client.get("/api/me/llm-settings", headers=headers)
    assert resp.json()["mode"] == "byok"


async def test_put_settings_legacy_clears_row(client, auth_token):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok", "provider": "openai", "model": "gpt-5.4-mini",
    })
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "legacy",
    })
    assert resp.status_code == 200
    assert resp.json()["mode"] == "legacy"


async def test_byok_only_account_cannot_select_system_llm(client, auth_token):
    _tok, user, headers = auth_token
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User

    async with get_session_factory()() as session:
        stored = await session.get(User, user["id"])
        stored.system_llm_access = False
        await session.commit()

    resp = await client.put(
        "/api/me/llm-settings", headers=headers, json={"mode": "legacy"}
    )
    assert resp.status_code == 403
    assert "chave própria" in resp.json()["detail"]


async def test_put_settings_rejects_unknown_provider(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok", "provider": "glm", "model": "x",
    })
    assert resp.status_code == 422


async def test_put_settings_rejects_disallowed_model(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok", "provider": "minimax", "model": "bogus",
    })
    assert resp.status_code == 422


async def test_put_settings_requires_provider_and_model_for_byok(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok",
    })
    assert resp.status_code == 422


async def test_put_settings_byok_forbidden_when_flag_off(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "0")
    get_settings.cache_clear()
    resp = await client.put("/api/me/llm-settings", headers=headers, json={
        "mode": "byok", "provider": "openai", "model": "gpt-5.4-mini",
    })
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Credentials: store / fetch / remove (never leak plaintext)
# ---------------------------------------------------------------------------

_PLAINTEXT = "sk-test-plaintext-never-leak-1234567890"


async def test_put_credential_returns_masked_only(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-credentials/anthropic", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    assert resp.status_code == 200, resp.text
    body = resp.text
    data = resp.json()
    assert data["validation_status"] == "unchecked"
    assert data["masked_suffix"].endswith(_PLAINTEXT[-4:])
    # The plaintext must never appear anywhere in the response.
    assert _PLAINTEXT not in body


async def test_get_settings_shows_stored_credential_masked(client, auth_token):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    resp = await client.get("/api/me/llm-settings", headers=headers)
    creds = resp.json()["credentials"]
    assert "openai" in creds
    assert creds["openai"]["validation_status"] == "unchecked"
    assert _PLAINTEXT not in resp.text


async def test_put_credential_overwrites_existing(client, auth_token):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": "sk-first-aaaaaaaa",
    })
    resp = await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": "sk-second-bbbbbbbb",
    })
    assert resp.status_code == 200
    assert resp.json()["masked_suffix"].endswith("bbbb")


async def test_delete_credential(client, auth_token):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    resp = await client.delete("/api/me/llm-credentials/openai", headers=headers)
    assert resp.status_code == 200
    resp = await client.get("/api/me/llm-settings", headers=headers)
    assert "openai" not in resp.json()["credentials"]


async def test_delete_missing_returns_404(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.delete("/api/me/llm-credentials/openai", headers=headers)
    assert resp.status_code == 404


async def test_put_credential_rejects_short_key(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": "short",
    })
    assert resp.status_code == 422


async def test_put_credential_unknown_provider(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.put("/api/me/llm-credentials/glm", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    assert resp.status_code == 422


async def test_credentials_require_auth(client):
    resp = await client.put("/api/me/llm-credentials/openai", json={"api_key": _PLAINTEXT})
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Crypto gating
# ---------------------------------------------------------------------------


async def test_put_credential_503_without_crypto_key(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    monkeypatch.setenv("AUTO_DM_CREDENTIALS_KEY", "")
    get_settings.cache_clear()
    resp = await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    assert resp.status_code == 503


async def test_put_credential_403_when_flag_off(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "0")
    get_settings.cache_clear()
    resp = await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Validation (monkeypatched — no real network)
# ---------------------------------------------------------------------------


async def test_validate_marks_valid(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/anthropic", headers=headers, json={
        "api_key": _PLAINTEXT,
    })

    import auto_dm.web.routes_llm as routes_llm

    def _ok(pid, key, *, timeout):
        assert pid == "anthropic"
        assert key == _PLAINTEXT
        return None

    monkeypatch.setattr(routes_llm, "validate_api_key", _ok, raising=False)
    # validate_api_key is imported lazily inside the handler; patch at source.
    import auto_dm.llm.registry as registry

    monkeypatch.setattr(registry, "validate_api_key", _ok)
    resp = await client.post("/api/me/llm-credentials/anthropic/validate", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["validation_status"] == "valid"

    resp = await client.get("/api/me/llm-settings", headers=headers)
    assert resp.json()["credentials"]["anthropic"]["validation_status"] == "valid"


async def test_validate_marks_invalid_on_auth_error(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/anthropic", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    import auto_dm.llm.registry as registry
    from auto_dm.llm.errors import ProviderAuthError

    def _fail(pid, key, *, timeout):
        raise ProviderAuthError(pid)

    monkeypatch.setattr(registry, "validate_api_key", _fail)
    resp = await client.post("/api/me/llm-credentials/anthropic/validate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["validation_status"] == "invalid"


async def test_validate_transient_error_keeps_status(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    await client.put("/api/me/llm-credentials/anthropic", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    import auto_dm.llm.registry as registry
    from auto_dm.llm.errors import ProviderUnavailableError

    monkeypatch.setattr(
        registry, "validate_api_key",
        lambda pid, key, *, timeout: (_ for _ in ()).throw(ProviderUnavailableError(pid)),
    )
    resp = await client.post("/api/me/llm-credentials/anthropic/validate", headers=headers)
    assert resp.status_code == 200
    # status stays unchecked (was never validated)
    assert resp.json()["validation_status"] == "unchecked"


async def test_validate_missing_credential_404(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.post("/api/me/llm-credentials/anthropic/validate", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Isolation between users (anti-enumeration)
# ---------------------------------------------------------------------------


async def test_user_cannot_see_or_delete_others_credential(client):
    """Cross-user access returns 404, not 403 — provider existence must not leak."""
    # user A stores a credential
    a = await client.post("/api/auth/signup", json={"username": "userA", "password": "pass1234A"})
    headers_a = {"Authorization": f"Bearer {a.json()['token']}"}
    await client.put("/api/me/llm-credentials/openai", headers=headers_a, json={
        "api_key": "sk-userA-secret-abcdef",
    })

    # user B tries to read/delete it
    b = await client.post("/api/auth/signup", json={"username": "userB", "password": "pass1234B"})
    headers_b = {"Authorization": f"Bearer {b.json()['token']}"}
    resp = await client.get("/api/me/llm-settings", headers=headers_b)
    assert "openai" not in resp.json()["credentials"]
    resp = await client.delete("/api/me/llm-credentials/openai", headers=headers_b)
    assert resp.status_code == 404
    # A's credential is untouched
    resp = await client.get("/api/me/llm-settings", headers=headers_a)
    assert "openai" in resp.json()["credentials"]


# ---------------------------------------------------------------------------
# Cascade: deleting the account wipes credentials
# ---------------------------------------------------------------------------


async def test_account_deletion_cascades_credentials(client, auth_token):
    _tok, _user, headers = auth_token
    user_id = _user["id"]
    await client.put("/api/me/llm-credentials/openai", headers=headers, json={
        "api_key": _PLAINTEXT,
    })
    # Admin deletes the user (admin route exists from earlier phases).
    # Rather than depend on an admin endpoint shape, delete the rows via the
    # session factory and assert the cascade FK behaviour is in place.
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserProviderCredential
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, user_id)
        await session.delete(u)
        await session.commit()
        leftover = (
            await session.execute(
                select(UserProviderCredential).where(
                    UserProviderCredential.user_id == user_id
                )
            )
        ).scalars().all()
        assert leftover == []
