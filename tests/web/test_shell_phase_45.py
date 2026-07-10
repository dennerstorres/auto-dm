"""Static contracts for the shared authenticated shell from Phase 45."""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def test_shell_styles_and_module_are_loaded_with_current_cache_version() -> None:
    html = read_static("index.html")

    assert '<body class="auth-visible">' in html
    assert '<script src="/app.js?v=63" type="module"></script>' in html
    assert '/css/shell.css?v=63' in html
    assert '/app.js?v=63' in html
    assert html.index('/style.css?v=63') < html.index('/css/shell.css?v=63')
    assert html.index('/css/shell.css?v=63') < html.index('/css/landing.css?v=63')


def test_authenticated_header_is_shared_and_contextual() -> None:
    html = read_static("index.html")

    assert html.count('class="site-header"') == 1
    assert 'id="app-navigation"' in html
    assert 'aria-label="Navegação da conta"' in html
    for element_id in (
        "shell-page-title",
        "shell-lobby-btn",
        "shell-game-btn",
        "shell-admin-btn",
        "prefs-btn",
        "who",
        "user-role",
        "logout-btn",
    ):
        assert f'id="{element_id}"' in html


def test_main_landmark_skip_link_and_screen_titles_are_present() -> None:
    html = read_static("index.html")

    assert 'class="skip-link" href="#main-content"' in html
    assert '<main id="main-content" class="app-main" tabindex="-1">' in html
    for screen_id, title_id in (
        ("lobby-screen", "lobby-title"),
        ("wizard-screen", "wizard-title"),
        ("game-screen", "game-title"),
        ("admin-panel-screen", "admin-title"),
    ):
        screen = re.search(
            rf'<section\s+[^>]*id="{screen_id}"[^>]*>', html, re.DOTALL
        )
        assert screen is not None
        assert f'aria-labelledby="{title_id}"' in screen.group(0)


def test_global_feedback_regions_cover_loading_offline_and_toasts() -> None:
    html = read_static("index.html")
    shell_js = read_static("shell.js")

    assert 'id="offline-banner"' in html
    assert 'id="global-loading"' in html
    assert 'id="toast-region"' in html
    assert 'aria-live="polite"' in html
    assert 'window.addEventListener("online", updateConnectivity)' in shell_js
    assert 'window.addEventListener("offline", updateConnectivity)' in shell_js
    assert 'main.setAttribute("aria-busy", "true")' in shell_js
    assert 'export function showToast' in shell_js


def test_all_dialogs_have_accessible_contract_and_shared_controls() -> None:
    html = read_static("index.html")
    shell_js = read_static("shell.js")

    # Authentication plus eight dialogs in authenticated areas (including
    # the user-detail drawer introduced in Phase 49).
    assert html.count('role="dialog"') == 9
    assert html.count('aria-modal="true"') == 9
    assert html.count("data-dialog-close") == 9
    assert html.count('class="modal" hidden') == 7
    assert 'event.key === "Escape"' in shell_js
    assert 'event.key !== "Tab"' in shell_js
    assert 'state.returnFocus.focus' in shell_js
    assert 'document.body.classList.toggle("dialog-open"' in shell_js


def test_app_uses_shared_dialog_and_request_feedback_helpers() -> None:
    app_js = read_static("app.js")

    for helper in (
        "beginRequest();",
        "endRequest();",
        "openDialog(",
        "closeDialog(",
        "confirmAction(",
        "updateShell({",
        "setShellUser(",
        "showToast(",
    ):
        assert helper in app_js

    assert re.search(r'getElementById\("[^\"]*-modal"\)\.style\.display', app_js) is None


def test_shell_css_uses_tokens_and_has_mobile_viewport_guards() -> None:
    css = read_static("css/shell.css")

    assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None
    assert "var(--content-editorial)" in css
    assert "max-height: calc(100dvh" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "@media (max-width: 640px)" in css
    assert ".dialog-open" in css
