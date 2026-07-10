"""Repository contracts for the Phase 50 frontend quality gates."""
from pathlib import Path
import json


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "auto_dm" / "web" / "static"


def test_playwright_covers_supported_viewports_and_quality_suites() -> None:
    config = (ROOT / "playwright.config.js").read_text(encoding="utf-8")
    assert "390, height: 844" in config
    assert "768, height: 1024" in config
    assert "1440, height: 900" in config
    assert "{arg}-{projectName}-{platform}{ext}" in config
    for suite in ("flows.spec.js", "visual.spec.js", "accessibility.spec.js"):
        assert (ROOT / "tests" / "e2e" / suite).is_file()


def test_functional_flows_and_reference_screens_are_declared() -> None:
    flows = (ROOT / "tests" / "e2e" / "flows.spec.js").read_text(encoding="utf-8")
    for scenario in ("login", "cadastro", "continuar save", "criação de personagem"):
        assert scenario in flows

    visual = (ROOT / "tests" / "e2e" / "visual.spec.js").read_text(encoding="utf-8")
    for screen in ("landing.png", "login.png", "lobby.png", "wizard.png", "game.png", "admin.png"):
        assert screen in visual


def test_axe_gate_and_asset_budget_command_are_wired() -> None:
    accessibility = (ROOT / "tests" / "e2e" / "accessibility.spec.js").read_text(
        encoding="utf-8"
    )
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / "frontend-quality.yml").read_text(
        encoding="utf-8"
    )
    assert "@axe-core/playwright" in accessibility
    assert 'item.impact === "critical"' in accessibility
    assert package["scripts"]["test:assets"]
    assert "npm run test:assets" in workflow
    assert "npm run test:e2e" in workflow


def test_hero_has_modern_formats_with_png_fallback() -> None:
    css = (STATIC / "css" / "landing.css").read_text(encoding="utf-8")
    for extension in ("avif", "webp", "png"):
        asset = STATIC / "assets" / f"hero-party-dragon.{extension}"
        assert asset.is_file() and asset.stat().st_size > 0
        assert f'hero-party-dragon.{extension}' in css
    assert "image-set(" in css
    assert (STATIC / "assets" / "hero-party-dragon.png").stat().st_size < 900 * 1024


def test_documentation_and_review_checklist_exist() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "Frontend e testes de interface" in readme
    assert "npm run test:e2e" in readme
    assert "DESIGN.md" in template
    assert "prefers-reduced-motion" in template
