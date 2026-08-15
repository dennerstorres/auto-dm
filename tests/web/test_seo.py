"""Contratos de SEO da landing — meta tags, dados estruturados, robots e sitemap."""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "auto_dm" / "web" / "static"
SITE = "https://autodm.dennerstorres.dev"

INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def test_head_declares_canonical_and_indexable_robots() -> None:
    assert f'<link rel="canonical" href="{SITE}/" />' in INDEX
    assert 'name="robots" content="index, follow' in INDEX


def test_title_and_description_carry_the_search_terms() -> None:
    title = re.search(r"<title>(.*?)</title>", INDEX).group(1)
    description = re.search(r'name="description"\s+content="(.*?)"', INDEX, re.S).group(1)
    for term in ("D&amp;D 5e", "IA"):
        assert term in title, f"faltando no <title>: {term}"
    for term in ("RPG", "D&amp;D 5e", "inteligência artificial", "português"):
        assert term in description, f"faltando na description: {term}"


def test_open_graph_and_twitter_cards_are_complete() -> None:
    for prop in ("og:type", "og:url", "og:title", "og:description", "og:image", "og:locale"):
        assert f'property="{prop}"' in INDEX, f"faltando meta {prop}"
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        assert f'name="{name}"' in INDEX, f"faltando meta {name}"

    for match in re.findall(r'(?:property|name)="(?:og|twitter):image" content="([^"]+)"', INDEX):
        asset = STATIC / match.removeprefix(f"{SITE}/")
        assert asset.is_file(), f"imagem social inexistente: {match}"


def test_structured_data_describes_the_app_and_the_faq() -> None:
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', INDEX, re.S).group(1)
    graph = json.loads(raw)["@graph"]
    types = {node["@type"] for node in graph}
    assert {"WebSite", "SoftwareApplication", "FAQPage"} <= types

    app = next(node for node in graph if node["@type"] == "SoftwareApplication")
    assert app["applicationCategory"] == "GameApplication"
    assert app["inLanguage"] == "pt-BR"
    assert app["featureList"]


def test_every_faq_question_is_visible_on_the_page() -> None:
    """Rich result de FAQ exige a resposta no HTML, não só no JSON-LD."""
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', INDEX, re.S).group(1)
    faq = next(node for node in json.loads(raw)["@graph"] if node["@type"] == "FAQPage")

    visible = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", INDEX)))
    for entry in faq["mainEntity"]:
        question = re.sub(r"\s+", " ", entry["name"])
        assert question in visible, f"pergunta do JSON-LD não aparece na página: {question}"


def test_h1_names_the_product_and_what_it_does() -> None:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", INDEX, re.S).group(1)
    assert "AUTO DM" in h1
    assert "D&amp;D 5e" in h1 and "IA" in h1


def test_robots_allows_crawling_and_points_at_the_sitemap() -> None:
    robots = (STATIC / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert "Disallow: /api/" in robots
    assert f"Sitemap: {SITE}/sitemap.xml" in robots


def test_sitemap_lists_the_canonical_url() -> None:
    sitemap = (STATIC / "sitemap.xml").read_text(encoding="utf-8")
    assert "http://www.sitemaps.org/schemas/sitemap/0.9" in sitemap
    assert f"<loc>{SITE}/</loc>" in sitemap


async def test_static_mount_serves_robots_and_sitemap(client) -> None:
    for path, content_type in (("/robots.txt", "text/plain"), ("/sitemap.xml", "xml")):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"]
