"""Phase 42c — preferences endpoint + merge helper tests."""
from __future__ import annotations

import pytest

from auto_dm.web.preferences import DEFAULT_PREFERENCES, merge_defaults, validate_patch


# The dev ``.env`` sets ``INVITE_CODE``, which would make the regular
# ``auth_token`` fixture 403 on signup. Disable the gate locally.
@pytest.fixture(autouse=True)
def _open_signup(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    from auto_dm.web.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_merge_defaults_none_yields_full_defaults():
    assert merge_defaults(None) == DEFAULT_PREFERENCES


def test_merge_defaults_backfills_missing_keys():
    # Stored blob from before 'auto_play' existed.
    stored = {"tts": {"enabled": True, "voice": "pt-BR-FranciscaNeural"}}
    merged = merge_defaults(stored)
    assert merged["tts"]["auto_play"] is False  # back-filled
    assert merged["tts"]["enabled"] is True  # preserved
    assert merged["music"] == DEFAULT_PREFERENCES["music"]  # whole section added


def test_validate_patch_clamps_volume():
    patch = validate_patch({"music": {"volume": 1.5}})
    assert patch["music"]["volume"] == 1.0
    patch = validate_patch({"music": {"volume": -0.3}})
    assert patch["music"]["volume"] == 0.0


def test_validate_patch_rejects_bad_rate():
    with pytest.raises(ValueError):
        validate_patch({"tts": {"rate": "fast"}})


def test_validate_patch_rejects_oversized_voice():
    with pytest.raises(ValueError):
        validate_patch({"tts": {"voice": "x" * 81}})


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


async def test_get_preferences_returns_defaults_for_new_user(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/me/preferences", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tts"] == DEFAULT_PREFERENCES["tts"]
    assert data["music"] == DEFAULT_PREFERENCES["music"]


async def test_patch_tts_voice_persists(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.patch(
        "/api/me/preferences",
        json={"tts": {"voice": "pt-BR-FranciscaNeural", "auto_play": True}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tts"]["voice"] == "pt-BR-FranciscaNeural"
    assert resp.json()["tts"]["auto_play"] is True

    # Round-trip via GET.
    get = await client.get("/api/me/preferences", headers=headers)
    assert get.json()["tts"]["voice"] == "pt-BR-FranciscaNeural"
    assert get.json()["tts"]["auto_play"] is True


async def test_patch_music_volume_clamped(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.patch(
        "/api/me/preferences",
        json={"music": {"volume": 1.5, "enabled": True, "src": "https://x/y.mp3"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["music"]["volume"] == 1.0
    assert resp.json()["music"]["enabled"] is True


async def test_me_includes_preferences_block(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert "tts" in prefs and "music" in prefs
    assert prefs["music"]["volume"] == 0.4


async def test_patch_invalid_rate_returns_422(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.patch(
        "/api/me/preferences",
        json={"tts": {"rate": "fast"}},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_preferences_401_without_auth(client):
    assert (await client.get("/api/me/preferences")).status_code == 401
    assert (await client.patch("/api/me/preferences", json={})).status_code == 401
