"""Static responsive and interaction contracts for Phase 49."""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).parents[2]
STATIC_DIR = REPO_ROOT / "src" / "auto_dm" / "web" / "static"


def read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def test_admin_styles_are_isolated_tokenized_and_loaded_last() -> None:
    html = read_static("index.html")
    css = read_static("css/admin.css")

    assert '/css/admin.css?v=63' in html
    assert '/app.js?v=63' in html
    assert html.index('/css/game.css?v=63') < html.index('/css/admin.css?v=63')
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", css) is None
    assert 'body[data-screen="admin-panel-screen"]' in css
    assert "var(--status-danger)" in css
    assert "var(--brand-gold)" in css


def test_admin_has_dense_usage_summary_without_nested_cards() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")

    assert 'id="admin-summary" class="admin-summary"' in html
    assert '<dl id="admin-summary"' in html
    assert '<section class="admin-usage"' in html
    assert 'admin-card' not in app_js
    for label in ("Custo (mês)", "Tokens (mês)", "Usuários ativos", "Desativados"):
        assert label in app_js


def test_user_table_supports_labeled_search_filters_and_sorting() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")

    assert 'role="search" aria-label="Filtrar usuários"' in html
    assert 'id="admin-q" class="field-control" type="search"' in html
    assert 'id="admin-status-filter"' in html
    assert 'id="admin-role-filter"' in html
    for key in ("username", "role", "status", "tokens_today", "cost_month"):
        assert f'data-admin-sort="{key}"' in html
    assert "function changeAdminSort(key)" in app_js
    assert 'setAttribute("aria-sort"' in app_js


def test_table_rows_are_safe_labeled_and_mobile_without_horizontal_scroll() -> None:
    app_js = read_static("app.js")
    css = read_static("css/admin.css")

    assert "escapeHtml(u.username)" in app_js
    for label in ("Usuário", "Papel", "Status", "Uso hoje", "Custo no mês", "Ações"):
        assert f'data-label="{label}"' in app_js
    assert "@media (max-width: 759px)" in css
    assert ".admin-table thead" in css
    assert "content: attr(data-label)" in css
    assert "overflow-wrap: anywhere" in css
    assert ".admin-table-wrap { border: 0; overflow: visible; }" in css


def test_destructive_actions_are_semantic_and_explicitly_confirmed() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")
    css = read_static("css/admin.css")

    assert "admin-action-danger" in app_js
    assert "Excluir definitivamente" in app_js
    assert '{ title: "Excluir usuário", confirmLabel: "Excluir definitivamente", danger: true }' in app_js
    assert ".admin-action-danger" in css
    assert "var(--status-danger)" in css
    assert 'id="confirm-modal"' in html


def test_user_detail_is_an_accessible_focus_managed_drawer() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")
    css = read_static("css/admin.css")

    assert 'id="admin-detail" class="modal admin-detail-overlay" hidden' in html
    assert 'class="admin-detail-drawer" role="dialog" aria-modal="true"' in html
    assert 'aria-labelledby="admin-detail-title"' in html
    assert 'data-dialog-close' in html
    assert 'openDialog("admin-detail", { initialFocus: "#admin-detail-close" })' in app_js
    assert 'closeDialog("admin-detail")' in app_js
    assert "max-height: 100dvh" in css


def test_preferences_have_keyboard_tabs_and_persistent_feedback() -> None:
    html = read_static("index.html")
    app_js = read_static("app.js")

    assert 'role="tablist" aria-label="Categorias de preferências"' in html
    for tab in ("narration", "music", "account"):
        assert f'data-prefs-tab="{tab}"' in html
        assert f'id="prefs-panel-{tab}"' in html
    assert "function selectPrefsTab(tab" in app_js
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in app_js
    assert 'setMsg("prefs-msg", "Salvando…", "")' in app_js
    assert 'setMsg("prefs-msg", "Preferências salvas.", "ok")' in app_js


def test_preference_controls_are_labeled_and_fit_narrow_viewports() -> None:
    html = read_static("index.html")
    css = read_static("css/admin.css")

    assert html.count('role="switch"') >= 3
    assert 'id="prefs-volume-output" for="prefs-music-volume"' in html
    assert 'id="prefs-account-username"' in html
    assert 'id="prefs-account-role"' in html
    assert 'id="prefs-logout" class="button button-secondary"' in html
    assert "@media (max-width: 399px)" in css
    assert "max-height: 100dvh" in css
    assert "width: 100%" in css
