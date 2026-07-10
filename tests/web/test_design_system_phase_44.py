"""Contracts for the Phase 44 design-system foundation."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from auto_dm.web import server


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def test_design_system_stylesheets_are_loaded_before_legacy_css() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    expected = [
        "/css/tokens.css?v=56",
        "/css/base.css?v=56",
        "/css/components.css?v=56",
        "/css/utilities.css?v=56",
        "/style.css?v=56",
    ]

    positions = [html.index(path) for path in expected]
    assert positions == sorted(positions)


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
    for filename in ("base.css", "components.css", "utilities.css"):
        css = (STATIC_DIR / "css" / filename).read_text(encoding="utf-8")
        assert "#" not in css, filename


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
