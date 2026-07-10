"""Contracts for the Phase 44 design-system foundation."""
from pathlib import Path
import re
from types import SimpleNamespace

import httpx
import pytest

from auto_dm.web import server


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def test_design_system_stylesheets_are_loaded_before_legacy_css() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    foundation = [
        "/css/tokens.css?v=63",
        "/css/base.css?v=63",
        "/css/components.css?v=63",
        "/css/utilities.css?v=63",
        "/style.css?v=63",
    ]

    positions = [html.index(path) for path in foundation]
    assert positions == sorted(positions)
    assert html.index("/style.css?v=63") < html.index("/css/landing.css?v=63")


def test_tokens_match_design_specification() -> None:
    css = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    expected_tokens = {
        "--brand-crimson": "#98292e",
        "--brand-gold": "#d1a34a",
        "--brand-parchment": "#eee7d7",
        "--ink-950": "#0a0c0f",
        "--text-on-dark": "#f5f0e6",
        "--status-danger": "#d45555",
        "--radius-lg": "8px",
        "--content-editorial": "1216px",
    }

    for token, value in expected_tokens.items():
        assert f"{token}: {value};" in css


def test_component_styles_do_not_introduce_literal_colors() -> None:
    for filename in ("base.css", "components.css", "landing.css", "utilities.css"):
        css = (STATIC_DIR / "css" / filename).read_text(encoding="utf-8")
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None, filename


def test_landing_and_auth_use_design_system_components() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'class="button button-primary hero-primary"' in html
    assert 'class="button button-secondary hero-secondary"' in html
    assert 'class="segmented-control auth-tabs"' in html
    assert html.count('class="field auth-field"') == 3
    auth_markup = html[
        html.index('<div id="auth-dialog"') : html.index("<!-- New / load game screen -->")
    ]
    assert auth_markup.count('class="field-control"') == 3


def test_landing_uses_local_lucide_icons_instead_of_interface_emoji() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    sprite = (STATIC_DIR / "assets" / "icons" / "lucide.svg").read_text(
        encoding="utf-8"
    )

    for icon in ("arrow-right", "chevron-down", "scroll-text", "sparkles", "swords", "x"):
        assert f'id="{icon}"' in sprite
        assert f'/assets/icons/lucide.svg#{icon}' in html

    for old_icon in ("⚔", "✦", "♜"):
        assert old_icon not in html


def test_landing_and_auth_colors_come_from_tokens() -> None:
    landing_css = (STATIC_DIR / "css" / "landing.css").read_text(encoding="utf-8")

    assert re.search(r"#[0-9a-fA-F]{3,8}\b", landing_css) is None
    assert "var(--brand-crimson)" in landing_css
    assert "var(--text-on-dark)" in landing_css


def test_landing_styles_are_removed_from_legacy_stylesheet() -> None:
    legacy_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert "Public landing page" not in legacy_css
    assert ".landing-hero" not in legacy_css


@pytest.mark.asyncio
async def test_component_reference_is_available_in_development(monkeypatch) -> None:
    settings = SimpleNamespace(frontend_url="", environment="development")
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    app = server.create_app(provider_factory=lambda: object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/design-system")

    assert response.status_code == 200
    assert "Referência de componentes" in response.text


@pytest.mark.asyncio
async def test_component_reference_is_hidden_in_production(monkeypatch) -> None:
    settings = SimpleNamespace(frontend_url="", environment="production")
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    app = server.create_app(provider_factory=lambda: object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/design-system")

    assert response.status_code == 404
