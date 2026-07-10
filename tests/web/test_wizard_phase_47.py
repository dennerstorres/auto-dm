"""Static interaction and responsive contracts for the Phase 47 wizard."""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def wizard_markup() -> str:
    html = read_static("index.html")
    start = html.index('<section id="wizard-screen"')
    end = html.index("<!-- Game screen -->", start)
    return html[start:end]


def test_wizard_stylesheet_is_isolated_and_uses_current_cache_version() -> None:
    html = read_static("index.html")
    css = read_static("css/wizard.css")

    assert '/css/wizard.css?v=63' in html
    assert html.index('/css/landing.css?v=63') < html.index('/css/wizard.css?v=63')
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None
    assert "body[data-screen=\"wizard-screen\"]" in css
    assert "var(--brand-gold)" in css
    assert "var(--status-danger)" in css


def test_wizard_has_legible_accessible_progress_for_all_steps() -> None:
    html = wizard_markup()
    app_js = read_static("app.js")

    assert 'aria-label="Etapas da criação"' in html
    assert '<ol id="wizard-progress" class="wizard-progress"></ol>' in html
    assert html.count('class="wizard-step"') == 11
    assert html.count('class="wizard-step active"') == 1
    assert app_js.count("{ label:") >= 12
    for contract in (
        'button.setAttribute("aria-current", "step")',
        'button.disabled = i > wizardState.furthestStep',
        '" is-current"',
        '" is-complete"',
    ):
        assert contract in app_js


def test_wizard_uses_keyboard_native_selectors_with_all_required_states() -> None:
    app_js = read_static("app.js")
    css = read_static("css/wizard.css")

    for contract in (
        'button.type = "button"',
        'button.setAttribute("aria-pressed", String(selected))',
        'badge.textContent = "Recomendado"',
        'button.disabled = disabled',
        'className = "choice"',
    ):
        assert contract in app_js
    for selector in (
        ".choice.selected",
        ".choice:disabled",
        ".choice.is-unavailable",
        ".choice-badge",
        ".choice:focus-visible",
        ".wizard-message.error",
    ):
        assert selector in css


def test_wizard_has_persistent_desktop_and_collapsible_mobile_sheet() -> None:
    html = wizard_markup()
    app_js = read_static("app.js")
    css = read_static("css/wizard.css")

    assert 'id="wizard-summary-sidebar"' in html
    assert '<details id="wizard-mobile-summary"' in html
    assert 'id="wizard-summary-mobile"' in html
    assert 'id="wizard-mobile-summary-line"' in html
    assert "function renderWizardPersistentSummary()" in app_js
    assert "renderWizardMiniSheet" in app_js
    assert ".wizard-sheet-preview" in css
    assert "grid-template-columns: 220px minmax(0, 1fr) 240px" in css
    assert "border-left: 1px solid var(--ink-700)" in css
    assert ".wizard-mobile-summary" in css


def test_wizard_loading_error_and_retry_states_do_not_collapse_layout() -> None:
    html = wizard_markup()
    app_js = read_static("app.js")
    css = read_static("css/wizard.css")

    for element_id in (
        "wizard-loading",
        "wizard-load-error",
        "wizard-load-error-text",
        "wizard-retry",
        "wizard-workspace",
    ):
        assert f'id="{element_id}"' in html
    assert 'setWizardLoadState("loading")' in app_js
    assert 'setWizardLoadState("ready")' in app_js
    assert 'setWizardLoadState("error", e.message)' in app_js
    assert 'document.getElementById("wizard-retry").onclick = openWizard' in app_js
    assert "min-height: 220px" in css
    assert ".wizard-companion-skeleton" in css
    assert 'root.setAttribute("aria-busy", "true")' in app_js


def test_wizard_navigation_is_sticky_safe_and_predictable_at_320px() -> None:
    html = wizard_markup()
    css = read_static("css/wizard.css")

    assert 'id="wz-prev" class="button button-secondary"' in html
    assert 'id="wz-next" class="button button-primary"' in html
    assert 'id="wz-finish" class="button button-primary"' in html
    assert ".wizard-nav" in css
    assert "flex: 0 0 auto" in css
    assert ".wizard-step-frame" in css
    assert "overflow-y: auto" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "@media (max-width: 359px)" in css
    assert "grid-template-columns: 1fr 1fr" in css
    assert "overflow-x: auto" in css


def test_final_review_is_sectioned_and_directly_editable() -> None:
    app_js = read_static("app.js")

    for title in ("Aventura", "Identidade", "Habilidades", "Grupo"):
        assert f'title: "{title}"' in app_js
    assert 'edit.textContent = "Editar"' in app_js
    assert "edit.onclick = () => wizardGoToStep(sectionData.step)" in app_js
    assert 'className = "wizard-review-section"' in app_js


def test_wizard_finish_preserves_existing_payload_contract() -> None:
    app_js = read_static("app.js")
    start = app_js.index("async function wizardFinish()")
    finish = app_js[start:]

    for field in (
        "campaign_name: wizardState.campaign_name",
        "narration_length: wizardState.narration_length",
        "initial_scenario: wizardState.initial_scenario || null",
        "name: wizardState.name",
        "race: wizardState.race",
        "subrace: wizardState.subrace",
        "class: wizardState.char_class",
        "subclass: wizardState.subclass",
        "background: wizardState.background",
        "alignment: wizardState.alignment",
        "level: wizardState.level",
        "stats_method: wizardState.stats_method",
        "skills: wizardState.skills",
        "companions: wizardState.companions",
    ):
        assert field in finish
    assert 'payload.player_character.spell_selection = wizardState.spell_selection' in finish
    assert 'api("/api/sessions/with-character"' in finish
