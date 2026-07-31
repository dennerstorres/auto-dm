"""Phase 52 — static contracts for the pending-check dice panel.

The frontend has no unit harness, so these assert the wiring that makes
the feature work end-to-end: the banner markup exists, the panel is
rendered from state, and a resolved roll is chained back to the DM (the
step whose absence was the original bug — the roll landed in the feed and
nothing else happened).
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def test_dice_panel_has_the_pending_check_banner() -> None:
    html = read_static("index.html")

    assert 'id="pending-check"' in html
    assert 'id="pending-check-label"' in html
    assert 'id="pending-check-dc"' in html
    assert 'id="pending-check-reason"' in html
    assert 'id="pending-check-mode"' in html
    # Hidden until the DM actually asks for something.
    assert 'class="pending-check"' in html
    banner = html[html.index('id="pending-check"'):]
    assert "hidden" in banner[: banner.index("</div>")]


def test_banner_is_inside_the_roll_panel() -> None:
    html = read_static("index.html")

    panel_start = html.index('class="roll-panel"')
    panel_end = html.index('id="roll-btn"', panel_start)
    assert panel_start < html.index('id="pending-check"') < panel_end


def test_banner_styles_are_tokenized() -> None:
    css = read_static("css/game.css")

    assert "#game-screen .pending-check {" in css
    # Phase 44 design-system rule: no raw hex in screen stylesheets.
    section = css[css.index("#game-screen .pending-check {"):]
    assert "var(--brand-gold)" in section


def test_pending_check_is_read_from_state_and_rendered() -> None:
    app_js = read_static("app.js")

    assert "function getPendingCheck()" in app_js
    assert "currentGameState.pending_check" in app_js
    assert "function renderPendingCheck()" in app_js
    # A resolved request must not keep the banner open.
    assert "pending.resolved" in app_js
    # Rendered as part of the normal tools refresh.
    assert "renderPendingCheck();" in app_js


def test_roll_result_is_chained_back_to_the_dm() -> None:
    app_js = read_static("app.js")

    roll = app_js[app_js.index("async function rollCheck("):]
    roll = roll[: roll.index("function describeCheckResolution")]
    assert "res.resolution" in roll
    # The whole point of the phase: a resolved check continues the scene.
    assert "sendInputClassic(resolution.narration_line)" in roll
    # ...and a free roll must not spend an LLM call.
    assert "if (resolution && resolution.narration_line)" in roll


def test_feed_announces_a_newly_requested_check() -> None:
    app_js = read_static("app.js")

    assert "r.pending_check" in app_js
    assert "Teste pedido:" in app_js


def test_preview_distinguishes_answering_from_a_free_roll() -> None:
    app_js = read_static("app.js")

    assert "responde ao pedido do Mestre" in app_js
    assert "rolagem avulsa" in app_js


def test_follow_up_narration_is_rendered_and_spoken_once() -> None:
    """The DM's follow-up call was paid for but never shown to the player."""
    app_js = read_static("app.js")

    classic = app_js[app_js.index("async function sendInputClassic("):]
    classic = classic[: classic.index("function returnToLobby")]
    assert "r.follow_up" in classic
    # Rendered after the action it narrates.
    assert classic.index("r.action_result") < classic.index('if (r.follow_up)')
    # One TTS clip for the whole turn, not two talking over each other.
    assert "[r.narration, r.follow_up].filter(Boolean).join" in classic
    assert classic.count("maybeAutoPlayTTS(") == 1
