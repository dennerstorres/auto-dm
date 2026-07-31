"""Contratos do PWA — manifest, ícones e service worker (Fase 53)."""
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "auto_dm" / "web" / "static"
ICONS = STATIC / "assets" / "icons"


def png_size(path: Path) -> tuple[int, int]:
    """Lê largura/altura do cabeçalho IHDR sem depender de Pillow."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} não é PNG"
    return struct.unpack(">II", header[16:24])


def test_manifest_declares_installable_app() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] and manifest["short_name"] == "Auto DM"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["lang"] == "pt-BR"
    assert manifest["background_color"] == "#0a0c0f"
    assert manifest["theme_color"] == "#0a0c0f"

    sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in manifest["icons"]}
    assert ("192x192", "any") in sizes
    assert ("512x512", "any") in sizes
    assert ("192x192", "maskable") in sizes
    assert ("512x512", "maskable") in sizes


def test_manifest_icons_exist_with_declared_dimensions() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        asset = STATIC / icon["src"].lstrip("/")
        assert asset.is_file(), f"ícone ausente: {icon['src']}"
        if icon["type"] != "image/png":
            continue
        expected = int(icon["sizes"].split("x")[0])
        assert png_size(asset) == (expected, expected)
        assert asset.stat().st_size < 80 * 1024


def test_apple_and_favicon_assets_are_wired_in_html() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/manifest.webmanifest" />' in html
    assert 'name="theme-color" content="#0a0c0f"' in html
    assert 'rel="apple-touch-icon" href="/assets/icons/apple-touch-icon.png"' in html
    assert 'rel="icon" href="/assets/icons/d20.svg"' in html
    assert 'name="apple-mobile-web-app-title" content="Auto DM"' in html
    assert png_size(ICONS / "apple-touch-icon.png") == (180, 180)
    assert (ICONS / "d20.svg").read_text(encoding="utf-8").startswith("<svg")


def test_service_worker_is_registered_and_skips_the_api() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    pwa = (STATIC / "pwa.js").read_text(encoding="utf-8")
    worker = (STATIC / "sw.js").read_text(encoding="utf-8")

    assert '<script src="/pwa.js?v=70" type="module"></script>' in html
    assert 'navigator.serviceWorker.register("/sw.js", { scope: "/" })' in pwa
    assert 'url.pathname.startsWith("/api/")' in worker
    assert "self.skipWaiting()" in worker and "self.clients.claim()" in worker


def test_precache_matches_the_versioned_assets_of_index_html() -> None:
    """O shell cacheado tem de bater com o que o HTML realmente carrega."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    worker = (STATIC / "sw.js").read_text(encoding="utf-8")

    referenced = set(re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html))
    referenced |= set(re.findall(r'<script src="([^"]+)" type="module">', html))
    referenced |= set(re.findall(r'<link rel="modulepreload" href="([^"]+)"', html))

    precached = set(re.findall(r'"(/[^"]+)"', worker.split("PRECACHE_URLS")[1]))
    missing = referenced - precached
    assert not missing, f"faltam no precache do sw.js: {sorted(missing)}"

    for url in sorted(precached):
        asset = STATIC / url.split("?")[0].lstrip("/")
        if url == "/":
            continue
        assert asset.is_file(), f"precache aponta pra arquivo inexistente: {url}"


def test_install_button_hooks_exist_on_landing_and_shell() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    pwa = (STATIC / "pwa.js").read_text(encoding="utf-8")
    assert html.count("data-install-app") == 2
    assert 'querySelectorAll("[data-install-app]")' in pwa
    assert "beforeinstallprompt" in pwa
