"""Phase 51 — LLM catalog endpoint tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _open_signup(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    from auto_dm.web.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_catalog_lists_five_providers(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/llm/catalog", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = {p["id"] for p in data["providers"]}
    assert ids == {"minimax", "openai", "anthropic", "gemini", "deepseek"}


async def test_catalog_never_exposes_base_url(client, auth_token):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/llm/catalog", headers=headers)
    data = resp.json()
    for p in data["providers"]:
        assert set(p) == {"id", "label", "models", "default_model"}
        assert "base_url" not in p


async def test_catalog_reflects_byok_flag(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    from auto_dm.web.config import get_settings

    # Explicitly override the developer .env.
    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "0")
    get_settings.cache_clear()
    resp = await client.get("/api/llm/catalog", headers=headers)
    assert resp.json()["byok_enabled"] is False

    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "1")
    get_settings.cache_clear()
    resp = await client.get("/api/llm/catalog", headers=headers)
    assert resp.json()["byok_enabled"] is True


async def test_catalog_requires_auth(client):
    resp = await client.get("/api/llm/catalog")
    assert resp.status_code in (401, 403)
