"""Static responsive and interaction contracts for the Phase 48 game table."""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def game_markup() -> str:
    html = read_static("index.html")
    start = html.index('<section id="game-screen"')
    end = html.index("<!-- Admin panel", start)
    return html[start:end]


def test_game_styles_are_isolated_tokenized_and_loaded_last() -> None:
    html = read_static("index.html")
    css = read_static("css/game.css")

    assert '/css/game.css?v=65' in html
    assert html.index('/css/wizard.css?v=65') < html.index('/css/game.css?v=65')
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None
    assert 'body[data-screen="game-screen"]' in css
    for token in (
        "var(--ink-900)",
        "var(--brand-crimson)",
        "var(--brand-gold)",
        "var(--status-success)",
        "var(--status-warning)",
        "var(--status-danger)",
    ):
        assert token in css


def test_narrative_is_dominant_with_stable_desktop_sidebar() -> None:
    html = game_markup()
    css = read_static("css/game.css")

    assert 'id="output" class="output" role="log"' in html
    assert 'aria-label="Narrativa da campanha"' in html
    assert 'id="table-tools" class="table-tools"' in html
    assert "grid-template-columns: minmax(0, 1fr) 368px" in css
    assert "grid-column: 1" in css
    assert "grid-column: 2" in css
    assert "grid-row: 2 / 4" in css
    assert "max-height: none" in css


def test_log_has_distinct_master_player_system_and_companion_hierarchy() -> None:
    app_js = read_static("app.js")
    css = read_static("css/game.css")

    assert 'w.textContent = who === "DM" ? "Mestre" : who' in app_js
    for selector in (
        ".entry.narration",
        ".entry.player",
        ".entry.system",
        ".entry.companion",
        ".typing-indicator",
    ):
        assert selector in css
    assert "font-family: var(--font-display)" in css
    assert "minmax(0, 68ch)" in css


def test_composer_is_predictable_accessible_and_supports_multiline_input() -> None:
    html = game_markup()
    app_js = read_static("app.js")
    css = read_static("css/game.css")

    assert '<label class="sr-only" for="cmd">Descreva sua ação</label>' in html
    assert '<textarea\n                        id="cmd"' in html
    assert 'aria-describedby="game-composer-hint"' in html
    assert 'id="send-btn" class="button button-primary"' in html
    assert "Enter envia · Shift + Enter quebra a linha" in html
    assert 'e.key === "Enter" && !e.shiftKey' in app_js
    assert "e.preventDefault()" in app_js
    assert ".game-composer" in css
    assert "grid-row: 3" in css
    assert "min-height: 48px" in css


def test_party_cards_surface_vitals_conditions_resources_and_quick_actions() -> None:
    html = game_markup()
    app_js = read_static("app.js")
    css = read_static("css/game.css")

    assert 'id="party-overview"' in html
    assert "function compactCharacterResources(character)" in app_js
    assert "function renderPartyOverview(chars)" in app_js
    for contract in (
        'class="party-hp-track"',
        'class="party-ac"',
        'class="party-card-condition"',
        'class="party-resource"',
        'data-open-sheet=',
        'data-command=',
    ):
        assert contract in app_js
    for resource in ("Fúria", "Ki", "Feitiçaria", "Mãos Curadoras", "Slots"):
        assert resource in app_js
    assert ".party-card.is-critical" in css
    assert ".party-card.is-active" in css


def test_table_controls_share_quick_action_and_icon_patterns() -> None:
    html = game_markup()
    app_js = read_static("app.js")

    for command in ("/look", "/status", "/inventory", "/conditions"):
        assert f'data-command="{command}"' in html
    for icon in ("volume-2", "music", "users", "dices", "terminal", "send"):
        assert f'lucide.svg#{icon}' in html
    assert "🔊" not in html
    assert "🎵" not in html
    assert 'document.querySelectorAll(".command-chip, .game-quick-action[data-command]")' in app_js
    assert 'id="shop-buttons"' in html
    assert 'id="roll-btn"' in html


def test_session_states_cover_turn_thinking_quota_offline_readonly_and_expiry() -> None:
    html = game_markup()
    app_js = read_static("app.js")

    assert 'id="game-session-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    for state, label in (
        ('"readonly"', "Somente leitura"),
        ('"expired"', "Sessão expirada"),
        ('"offline"', "Aguardando conexão"),
        ('"quota"', "Cota diária atingida"),
        ('"thinking"', "Mestre pensando…"),
        ('"ready"', "Seu turno"),
    ):
        assert state in app_js
        assert label in app_js
    assert "function syncGameSessionState()" in app_js
    assert 'window.addEventListener("offline", () =>' in app_js
    assert "quotaReached = true" in app_js
    assert "sessionExpired = true" in app_js


def test_mobile_uses_dedicated_tabs_and_safe_drawers_without_page_overflow() -> None:
    html = game_markup()
    app_js = read_static("app.js")
    css = read_static("css/game.css")

    assert 'role="tablist" aria-label="Áreas da mesa"' in html
    for panel in ("narrative", "party", "roll", "commands"):
        assert f'data-game-panel="{panel}"' in html
    assert "function selectGamePanel(panel" in app_js
    assert 'event.key !== "Escape"' in app_js
    assert ".is-mobile-open" in css
    assert '@media (max-width: 1023px)' in css
    assert '@media (max-width: 479px)' in css
    assert '@media (max-width: 359px)' in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "max-height: min(82dvh, 720px)" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "overflow: hidden" in css
