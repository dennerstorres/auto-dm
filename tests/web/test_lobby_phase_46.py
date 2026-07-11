"""Contracts for the campaign lobby and migration-free save summaries."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace

from auto_dm.web.routes_admin import AdminSaveOut
from auto_dm.web.routes_game import SaveOut
from auto_dm.web.save_metadata import extract_save_metadata


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def state_json(*, player_id: str = "hero") -> str:
    return json.dumps(
        {
            "campaign_name": "Crônicas da Aliança",
            "current_location": "Ruínas de Eldoria",
            "player_character_id": player_id,
            "party": [
                {"id": "companion", "name": "Borin", "level": 2},
                {
                    "id": "hero",
                    "name": "Nara",
                    "level": 4,
                    "is_player": True,
                },
            ],
        }
    )


def save_stub(**overrides):
    values = {
        "slug": "cronicas-alianca",
        "user_id": 7,
        "state": state_json(),
        "archived": False,
        "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc),
        "user": SimpleNamespace(username="nara"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_extract_save_metadata_uses_explicit_player_id() -> None:
    metadata = extract_save_metadata(state_json())

    assert metadata == {
        "campaign_name": "Crônicas da Aliança",
        "character_name": "Nara",
        "character_level": 4,
        "current_location": "Ruínas de Eldoria",
    }


def test_extract_save_metadata_falls_back_to_player_flag() -> None:
    metadata = extract_save_metadata(state_json(player_id="missing"))

    assert metadata["character_name"] == "Nara"
    assert metadata["character_level"] == 4


def test_extract_save_metadata_tolerates_legacy_or_malformed_state() -> None:
    assert extract_save_metadata("not-json") == {
        "campaign_name": "",
        "character_name": "",
        "character_level": None,
        "current_location": "",
    }
    partial = extract_save_metadata('{"campaign_name": " Antiga ", "party": []}')
    assert partial["campaign_name"] == "Antiga"
    assert partial["character_name"] == ""


def test_regular_and_admin_save_payloads_include_lobby_metadata() -> None:
    save = save_stub()

    regular = SaveOut.from_save(save).model_dump()
    admin = AdminSaveOut.from_save(save).model_dump()

    for payload in (regular, admin):
        assert payload["campaign_name"] == "Crônicas da Aliança"
        assert payload["character_name"] == "Nara"
        assert payload["character_level"] == 4
        assert payload["current_location"] == "Ruínas de Eldoria"
    assert admin["username"] == "nara"
    assert admin["user_id"] == 7


def test_lobby_has_tabs_and_all_async_states() -> None:
    html = read_static("index.html")

    assert 'class="tabs lobby-tabs" role="tablist"' in html
    assert 'id="lobby-active-tab"' in html
    assert 'id="lobby-archived-tab"' in html
    assert html.count('role="tabpanel"') >= 2
    for element_id in (
        "lobby-loading",
        "lobby-error",
        "lobby-retry",
        "lobby-empty",
        "archived-empty",
        "saves-list",
        "archived-list",
    ):
        assert f'id="{element_id}"' in html
    assert 'id="archive-toggle"' not in html


def test_lobby_primary_creation_actions_go_directly_to_wizard() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")

    assert 'id="wizard-btn"' in html
    assert 'id="empty-wizard-btn"' in html
    assert 'document.getElementById("empty-wizard-btn").onclick = openWizard' in app_js
    assert 'document.getElementById("wizard-btn").onclick = openWizard' in app_js


def test_lobby_renderer_exposes_required_metadata_and_featured_action() -> None:
    app_js = read_static("app.js")

    for contract in (
        '"Continuar aventura"',
        's.campaign_name || s.slug',
        's.character_name',
        's.character_level',
        's.current_location',
        'formatSaveDate(s.updated_at)',
        'className = "campaign-row"',
        'isAdmin() && s.username',
    ):
        assert contract in app_js
    assert 'new Intl.DateTimeFormat("pt-BR"' in app_js


def test_lobby_stylesheet_is_scoped_tokenized_and_responsive() -> None:
    html = read_static("index.html")
    css = read_static("css/lobby.css")

    assert '/css/lobby.css?v=65' in html
    assert html.index('/css/shell.css?v=65') < html.index('/css/lobby.css?v=65')
    assert html.index('/css/lobby.css?v=65') < html.index('/css/landing.css?v=65')
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None
    assert ".campaign-row" in css
    assert "grid-template-columns: minmax(0, 1fr) auto" in css
    assert "@media (max-width: 759px)" in css
    assert "@media (max-width: 479px)" in css


def test_preferences_and_admin_remain_in_shell_not_lobby_content() -> None:
    html = read_static("index.html")
    lobby_start = html.index('id="lobby-screen"')
    lobby_end = html.index("</section>", lobby_start)
    lobby_opening = html[lobby_start:lobby_end]

    assert 'id="prefs-btn"' not in lobby_opening
    assert 'id="admin-panel-btn"' not in lobby_opening
    assert 'id="shell-admin-btn"' in html
    assert 'id="prefs-btn"' in html
