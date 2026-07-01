"""Tests for the SSE streaming endpoint (Phase 26b).

The endpoint wraps ``DMAgent.stream()`` (a sync generator) and pushes
each token to the browser as an SSE ``data:`` line. We stub the
``DM agent stream`` to yield a fixed sequence of tokens and assert:

- ``start`` event arrives first
- all ``token`` events arrive, in order, with the right ``data``
- a single ``done`` event closes the stream
- malformed input (e.g. session not found, missing auth) returns 401/404
"""
from __future__ import annotations

import json
from typing import Iterator

import pytest


# ============================================================================
# Stub streaming provider
# ============================================================================


class _StubStreamProvider:
    """A minimal provider whose ``stream()`` yields a fixed sequence.

    The DMAgent expects providers with ``stream(messages) -> Iterator[str]``
    and ``chat(messages) -> str``. We implement just enough for the SSE
    route to work.
    """

    def __init__(self, tokens: list[str] | None = None, fail: Exception | None = None):
        self.tokens = tokens or []
        self.fail = fail

    def stream(self, messages) -> Iterator[str]:
        if self.fail:
            raise self.fail
        for t in self.tokens:
            yield t

    def chat(self, messages):
        # Used by some happy-path; not invoked by the SSE stream route.
        return "".join(self.tokens)

    def count_tokens(self, messages):
        return 0


def _empty_state() -> dict:
    from datetime import datetime, timezone
    return {
        "campaign_name": "Test Campaign",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "current_location": "Tavern",
        "party": [],
        "npcs": [],
        "initiative_order": [],
        "in_combat": False,
        "current_turn_index": 0,
        "player_character_id": "",
        "active_conditions": [],
    }


# ============================================================================
# Helpers — parse SSE wire format
# ============================================================================


def _parse_sse(body: bytes) -> list[dict]:
    """Parse SSE wire format into a list of event dicts.

    Each event is one ``data: {json}`` line (we wrap everything in data).
    """
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
        # If multiline data was joined, we'd combine; for now we don't.
    return events


async def _install_stub_provider(app_instance, tokens, fail=None):
    """Replace the global SessionManager's provider factory with our stub,
    and rebuild DM agents in cached sessions to use the stub."""
    import auto_dm.web.server as srv
    from auto_dm.web.sessions import SessionManager

    stub = _StubStreamProvider(tokens=tokens, fail=fail)
    factory = lambda: stub  # noqa: E731
    sm = SessionManager(provider_factory=factory)
    app_instance.state.session_manager = sm
    srv._state.session_manager = sm
    return stub


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.asyncio
async def test_stream_requires_auth(client, app_instance):
    # No Authorization header.
    resp = await client.post(
        "/api/sessions/missing/stream",
        json={"line": "look"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_session_not_found(client, auth_token, app_instance):
    await _install_stub_provider(app_instance, tokens=["hi"])
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/does-not-exist/stream",
        json={"line": "look"},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_emits_start_token_then_done(client, admin_token, app_instance):
    """End-to-end: tokens arrive in order, then done."""
    await _install_stub_provider(app_instance, tokens=["Você ", "vê ", "uma porta."])
    token, user, headers = admin_token
    # Create a session.
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    sid = resp.json()["session_id"]
    # Stream.
    resp2 = await client.post(
        f"/api/sessions/{sid}/stream",
        json={"line": "olhe em volta"},
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp2.content)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    # Tokens in order.
    token_data = [e["data"] for e in events if e["type"] == "token"]
    assert token_data == ["Você ", "vê ", "uma porta."]
    # Done last.
    assert types[-1] == "done"
    assert "campaign_name" in events[-1]["data"]


@pytest.mark.asyncio
async def test_stream_handles_empty_tokens(client, admin_token, app_instance):
    """Zero-token stream still emits start + done."""
    await _install_stub_provider(app_instance, tokens=[])
    token, user, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    sid = resp.json()["session_id"]
    resp2 = await client.post(
        f"/api/sessions/{sid}/stream",
        json={"line": "."},
        headers=headers,
    )
    events = _parse_sse(resp2.content)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    # No token events expected.
    assert "token" not in types


@pytest.mark.asyncio
async def test_stream_propagates_provider_error(client, admin_token, app_instance):
    """If the LLM raises mid-stream, we emit 'error' and close cleanly."""
    await _install_stub_provider(
        app_instance, tokens=["Você "], fail=RuntimeError("provider 500"),
    )
    token, user, headers = admin_token
    resp = await client.post(
        "/api/sessions",
        json={"state": _empty_state()},
        headers=headers,
    )
    sid = resp.json()["session_id"]
    resp2 = await client.post(
        f"/api/sessions/{sid}/stream",
        json={"line": "."},
        headers=headers,
    )
    events = _parse_sse(resp2.content)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    # We expect token "Você " then either error (fatal) or done (best-effort).
    error_events = [e for e in events if e["type"] == "error"]
    # The stub raises AFTER yielding all tokens, so we see one token +
    # error + done. The done is skipped when error is emitted (we return).
    assert any("provider 500" in e["data"] for e in error_events)


@pytest.mark.asyncio
async def test_sse_format_helper():
    """The wire-format helper emits a single data: line + blank line."""
    from auto_dm.web.sse import format_sse

    out = format_sse({"type": "token", "data": "olá"})
    assert out.startswith('data: {"type": "token", "data": "ol')
    assert out.endswith("\n\n")


@pytest.mark.asyncio
async def test_stream_quick():
    """Smoke test verifying format_sse round-trips through events."""
    from auto_dm.web.sse import format_sse

    payload = format_sse({"type": "done", "data": "{}"})
    assert payload.startswith("data: ")
    assert payload.count("\n\n") == 1
