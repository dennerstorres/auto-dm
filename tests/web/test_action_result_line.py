"""The feed's "Ação" line must be readable prose, never a JSON dump.

The frontend has no unit harness, so — like the Phase 52 checks — these
assert the static wiring: the renderer exists, it is what the turn handler
calls, and the raw ``JSON.stringify(action_result)`` that used to land in
the message list is gone.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def test_action_result_is_not_stringified_into_the_feed() -> None:
    app_js = read_static("app.js")

    assert "JSON.stringify(r.action_result)" not in app_js
    assert "JSON.stringify(result)" not in app_js


def test_formatter_exists_and_is_used_by_the_turn_handler() -> None:
    app_js = read_static("app.js")

    assert "function formatActionResult(result)" in app_js

    classic = app_js[app_js.index("async function sendInputClassic("):]
    classic = classic[: classic.index("function returnToLobby")]
    assert "formatActionResult(r.action_result)" in classic
    # An empty result must not push a blank entry into the message list.
    assert "if (ar) appendLog" in classic


def test_formatter_leads_with_the_engine_message() -> None:
    """`ActionResult.message` is the authoritative prose; it comes first."""
    app_js = read_static("app.js")

    fn = app_js[app_js.index("function formatActionResult(result)"):]
    fn = fn[: fn.index("\n}\n")]
    assert 'result.message || ""' in fn
    assert "parts.push(message)" in fn
    # Chips are appended after it, joined by a separator.
    assert 'parts.join(" · ")' in fn


def test_only_allowlisted_mechanical_fields_reach_the_feed() -> None:
    """`mechanical` mirrors the prose; dumping all of it is what we removed."""
    app_js = read_static("app.js")

    assert "const MECHANICAL_CHIPS = [" in app_js
    chips = app_js[app_js.index("const MECHANICAL_CHIPS = ["):]
    chips = chips[: chips.index("\n];")]
    for key in ("damage_rolls", "attacks_remaining", "error"):
        assert f'"{key}"' in chips
    # Fields the message already states must not be re-emitted as chips.
    for redundant in ("attack_roll", "attack_total", "target_ac", "is_hit", "damage_type"):
        assert f'"{redundant}"' not in chips

    fn = app_js[app_js.index("function formatActionResult(result)"):]
    fn = fn[: fn.index("\n}\n")]
    assert "for (const [key, render] of MECHANICAL_CHIPS)" in fn


def test_app_js_cache_bust_was_bumped() -> None:
    html = read_static("index.html")

    assert "/app.js?v=68" not in html
    assert "/app.js?v=69" in html
