"""Tests for the AI name-suggestion endpoint (Phase 35).

`POST /api/suggest-names` powers the ✨ buttons next to the wizard's
campaign/character name fields. It enforces the daily quota, tags the
LLM cost as ``kind="naming"``, and parses a JSON blob out of the model's
reply. These tests stub the provider with an in-process fake so no real
LLM call is made.
"""
from __future__ import annotations

import pytest

from auto_dm.llm.usage import UsageReport
from auto_dm.web.routes_setup import _clean_name, _extract_json_object


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clean_name_strips_quotes_and_whitespace():
    assert _clean_name('  "Aragorn"  ') == "Aragorn"
    assert _clean_name("'Vellumbra'") == "Vellumbra"


def test_clean_name_rejects_garbage():
    assert _clean_name(None) is None
    assert _clean_name(123) is None  # type: ignore[arg-type]
    assert _clean_name("   ") is None
    assert _clean_name('""') is None


def test_clean_name_caps_length():
    long = "x" * 200
    assert len(_clean_name(long) or "") == 80


def test_extract_json_object_handles_fences_and_prose():
    parsed = _extract_json_object('Sure! ```json\n{"campaign_name": "X"}\n```')
    assert parsed == {"campaign_name": "X"}

    parsed = _extract_json_object('blabla {"a": 1, "b": 2} trailing')
    assert parsed == {"a": 1, "b": 2}


def test_extract_json_object_raises_on_no_json():
    with pytest.raises(ValueError):
        _extract_json_object("no braces here")


# ---------------------------------------------------------------------------
# Provider stub
# ---------------------------------------------------------------------------


def _make_factory(content: str):
    """Build a provider_factory whose provider returns ``content``."""

    class _FakeProvider:
        name = "fake"
        config = None

        def chat_with_usage(self, messages):  # noqa: ANN001
            return content, UsageReport(
                prompt_tokens=10,
                completion_tokens=5,
                provider="fake",
                model="fake-model",
                source="api",
            )

    return lambda: _FakeProvider()


def _wire_provider(app_instance, factory):
    """Point both app.state and the global _state at ``factory``."""
    app_instance.state.provider_factory = factory
    import auto_dm.web.server as srv

    srv._state.provider_factory = factory  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_suggest_names_both(client, app_instance, auth_token):
    token, _user, headers = auth_token
    _wire_provider(
        app_instance,
        _make_factory(
            '{"campaign_name": "Crônicas da Aliança", '
            '"character_name": "Aragorn Filho de Arathorn"}'
        ),
    )
    resp = await client.post(
        "/api/suggest-names", json={"kind": "both"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["campaign_name"] == "Crônicas da Aliança"
    assert data["character_name"] == "Aragorn Filho de Arathorn"


@pytest.mark.asyncio
async def test_suggest_names_campaign_only(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(app_instance, _make_factory('{"campaign_name": "A Queda"}'))
    resp = await client.post(
        "/api/suggest-names", json={"kind": "campaign"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["campaign_name"] == "A Queda"
    assert data["character_name"] is None


@pytest.mark.asyncio
async def test_suggest_names_character_only(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(app_instance, _make_factory('{"character_name": "Lyra"}'))
    resp = await client.post(
        "/api/suggest-names", json={"kind": "character"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["character_name"] == "Lyra"


@pytest.mark.asyncio
async def test_suggest_names_strips_quotes_in_reply(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(
        app_instance,
        _make_factory('{"character_name": "\\"Maren da Colina\\""}'),
    )
    resp = await client.post(
        "/api/suggest-names", json={"kind": "character"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["character_name"] == "Maren da Colina"


@pytest.mark.asyncio
async def test_suggest_names_default_kind_is_both(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(
        app_instance,
        _make_factory('{"campaign_name": "C", "character_name": "P"}'),
    )
    # Empty body → default kind "both".
    resp = await client.post("/api/suggest-names", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["campaign_name"] == "C"
    assert data["character_name"] == "P"


@pytest.mark.asyncio
async def test_suggest_names_502_on_unparseable_reply(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(app_instance, _make_factory("the hero is named bob"))
    resp = await client.post(
        "/api/suggest-names", json={"kind": "character"}, headers=headers
    )
    assert resp.status_code == 502, resp.text


@pytest.mark.asyncio
async def test_suggest_names_502_when_required_key_missing(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    # Asked for both but model only returned a character name.
    _wire_provider(app_instance, _make_factory('{"character_name": "P"}'))
    resp = await client.post(
        "/api/suggest-names", json={"kind": "both"}, headers=headers
    )
    assert resp.status_code == 502, resp.text


@pytest.mark.asyncio
async def test_suggest_names_requires_auth(client):
    resp = await client.post("/api/suggest-names", json={"kind": "both"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_suggest_names_429_when_quota_exceeded(
    client, app_instance, auth_token, monkeypatch
):
    _token, _u, headers = auth_token
    _wire_provider(app_instance, _make_factory('{"campaign_name": "X"}'))

    async def _blocked(session, user, settings):  # noqa: ANN001
        return {
            "detail": "Limite diário de tokens atingido",
            "used": 999,
            "limit": 1000,
            "unit": "tokens",
            "reset_at": "2030-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr("auto_dm.web.routes_setup.check_quota", _blocked)
    resp = await client.post(
        "/api/suggest-names", json={"kind": "campaign"}, headers=headers
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"]["unit"] == "tokens"


@pytest.mark.asyncio
async def test_suggest_names_persists_naming_usage_event(
    client, app_instance, auth_token
):
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import UsageEvent, UsageKind

    _token, _u, headers = auth_token
    _wire_provider(
        app_instance,
        _make_factory('{"campaign_name": "C", "character_name": "P"}'),
    )
    resp = await client.post("/api/suggest-names", json={"kind": "both"}, headers=headers)
    assert resp.status_code == 200, resp.text

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        rows = (
            (
                await session.execute(
                    select(UsageEvent).where(UsageEvent.kind == UsageKind.NAMING.value)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].endpoint == "/api/suggest-names"
    assert rows[0].total_tokens > 0


@pytest.mark.asyncio
async def test_suggest_names_rejects_oversized_theme(client, app_instance, auth_token):
    _token, _u, headers = auth_token
    _wire_provider(app_instance, _make_factory('{"campaign_name": "C"}'))
    resp = await client.post(
        "/api/suggest-names",
        json={"kind": "campaign", "theme": "x" * 201},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
