"""Tests for the campaign-opening endpoints (auto narration on game start).

Covers:
- POST /api/sessions/{sid}/opening        (sync)
- POST /api/sessions/{sid}/opening/stream (SSE)

The opening is generated without player input: the DM picks a starting
location (via a ``move`` action) and the backend applies it to
``state.current_location``. Reloading an already-opened game (non-empty
narrative log) must NOT call the LLM again (idempotency).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

import pytest


# ============================================================================
# Stub provider: returns a fixed opening payload (narration + move action)
# ============================================================================


_OPENING_TEXT = (
    "Você acorda numa estrada poeirenta ao amanhecer.\n"
    "```action\n"
    '{"action_type": "move", "actor_id": "p1", '
    '"params": {"destination": "Estrada do Norte"}}\n'
    "```"
)


class _OpeningProvider:
    """Provider whose ``chat``/``stream`` return the scripted opening.

    Counts calls so the idempotency test can assert the LLM was skipped.
    """

    def __init__(self, text: str = _OPENING_TEXT, fail: Exception | None = None):
        self.text = text
        self.fail = fail
        self.call_count = 0

    def chat(self, messages) -> str:
        self.call_count += 1
        if self.fail:
            raise self.fail
        return self.text

    def stream(self, messages) -> Iterator[str]:
        self.call_count += 1
        if self.fail:
            raise self.fail
        # Yield in two chunks so we can assert accumulation.
        mid = len(self.text) // 2
        yield self.text[:mid]
        yield self.text[mid:]

    def count_tokens(self, messages) -> int:
        return 0


def _empty_state(*, narrative_log: list[dict] | None = None) -> dict:
    return {
        "campaign_name": "Test Campaign",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "",
        "party": [],
        "npcs": [],
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "p1",
        "active_conditions": [],
        "narrative_log": narrative_log or [],
    }


async def _install_stub(app_instance, **provider_kwargs) -> _OpeningProvider:
    import auto_dm.web.server as srv
    from auto_dm.web.sessions import SessionManager

    stub = _OpeningProvider(**provider_kwargs)
    factory = lambda: stub  # noqa: E731
    sm = SessionManager(provider_factory=factory)
    app_instance.state.session_manager = sm
    srv._state.session_manager = sm
    # Phase 51d-lite — the route resolver reads the *app-level*
    # ``provider_factory`` as a fallback when the user is in legacy mode,
    # so a test that swaps the per-session manager must also wire the
    # factory at the same level. Without this, the resolver falls back
    # to the conftest's stub and the test's scripted provider is never
    # invoked.
    app_instance.state.provider_factory = factory
    srv._state.provider_factory = factory
    return stub


def _parse_sse(body: bytes) -> list[dict]:
    text = body.decode("utf-8")
    events: list[dict] = []
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        for line in raw.split("\n"):
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    events.append({"type": "_raw", "data": payload})
    return events


# ============================================================================
# Sync endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_opening_requires_auth(client, app_instance):
    resp = await client.post("/api/sessions/missing/opening")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_opening_session_not_found(client, auth_token, app_instance):
    await _install_stub(app_instance)
    _, _, headers = auth_token
    resp = await client.post(
        "/api/sessions/does-not-exist/opening", headers=headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_opening_sync_returns_narration_and_sets_location(
    client, admin_token, app_instance
):
    stub = await _install_stub(app_instance)
    _, _, headers = admin_token
    sid = (await client.post(
        "/api/sessions", json={"state": _empty_state()}, headers=headers,
    )).json()["session_id"]

    resp = await client.post(f"/api/sessions/{sid}/opening", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "estrada poeirenta" in body["narration"]
    # The move action's destination was applied to current_location.
    assert body["state"]["current_location"] == "Estrada do Norte"
    # Logged as a single DM entry, no player entry.
    roles = [e["role"] for e in body["state"]["narrative_log"]]
    assert roles == ["dm"]
    assert stub.call_count == 1


@pytest.mark.asyncio
async def test_opening_sync_idempotent_when_log_nonempty(
    client, admin_token, app_instance
):
    """A loaded save (narrative_log already populated) must not regenerate."""
    pre = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "dm",
            "speaker": "DM",
            "content": "Abertura anterior já existente.",
        }
    ]
    stub = await _install_stub(app_instance)
    _, _, headers = admin_token
    sid = (await client.post(
        "/api/sessions",
        json={"state": _empty_state(narrative_log=pre)},
        headers=headers,
    )).json()["session_id"]

    resp = await client.post(f"/api/sessions/{sid}/opening", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["narration"] == "Abertura anterior já existente."
    # The LLM was NOT called.
    assert stub.call_count == 0


# ============================================================================
# Stream endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_opening_stream_emits_start_token_done_and_sets_location(
    client, admin_token, app_instance
):
    await _install_stub(app_instance)
    _, _, headers = admin_token
    sid = (await client.post(
        "/api/sessions", json={"state": _empty_state()}, headers=headers,
    )).json()["session_id"]

    resp = await client.post(
        f"/api/sessions/{sid}/opening/stream", json={}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.content)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    # Tokens accumulate into the opening narration.
    joined = "".join(e["data"] for e in events if e["type"] == "token")
    assert "estrada poeirenta" in joined
    # The done payload is the serialized GameState with location set.
    done_state = json.loads(events[-1]["data"])
    assert done_state["current_location"] == "Estrada do Norte"
    assert [e["role"] for e in done_state["narrative_log"]] == ["dm"]


@pytest.mark.asyncio
async def test_opening_stream_idempotent(client, admin_token, app_instance):
    """Non-empty log: only start + done, no tokens, no LLM call."""
    pre = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "dm",
            "speaker": "DM",
            "content": "Já narrado.",
        }
    ]
    stub = await _install_stub(app_instance)
    _, _, headers = admin_token
    sid = (await client.post(
        "/api/sessions",
        json={"state": _empty_state(narrative_log=pre)},
        headers=headers,
    )).json()["session_id"]

    resp = await client.post(
        f"/api/sessions/{sid}/opening/stream", json={}, headers=headers
    )
    events = _parse_sse(resp.content)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert "token" not in types
    assert stub.call_count == 0
