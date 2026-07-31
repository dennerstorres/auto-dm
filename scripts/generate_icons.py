"""Gera os ícones PWA do Auto DM (d20 da marca) — Fase 53.

Desenha um d20 visto de topo (10 faces visíveis) nas cores da marca e
exporta os PNGs do manifest, o apple-touch-icon, os favicons e um SVG
com a mesma geometria.

Uso (requer Pillow, dependência só de desenvolvimento):

    pip install pillow
    python scripts/generate_icons.py

Os arquivos gerados são versionados no repositório; rode este script
apenas quando a marca mudar.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "auto_dm" / "web" / "static" / "assets" / "icons"

# Paleta (espelha css/tokens.css).
INK_950 = (10, 12, 15)
INK_800 = (30, 32, 36)
CRIMSON = (152, 41, 46)
CRIMSON_HOVER = (179, 55, 60)
CRIMSON_DEEP = (111, 29, 34)
GOLD = (209, 163, 74)
GOLD_SOFT = (232, 199, 111)

SS = 4  # supersampling

FONT_CANDIDATES = [
    "C:/Windows/Fonts/georgiab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
]


def hexagon() -> list[tuple[float, float]]:
    """Hexágono de topo pontudo, raio 1, sentido horário a partir do topo."""
    return [
        (math.sin(math.radians(a)), -math.cos(math.radians(a)))
        for a in (0, 60, 120, 180, 240, 300)
    ]


def d20_faces() -> list[list[tuple[float, float]]]:
    """As 10 faces visíveis de um d20 visto de topo."""
    p = hexagon()
    inner = [tuple(0.5 * c for c in p[i]) for i in (0, 2, 4)]  # A(topo), B(dir), C(esq)
    a, b, c = inner
    return [
        [a, b, c],
        [a, p[5], p[0]],
        [a, p[0], p[1]],
        [a, p[1], b],
        [b, p[1], p[2]],
        [b, p[2], p[3]],
        [b, p[3], c],
        [c, p[3], p[4]],
        [c, p[4], p[5]],
        [c, p[5], a],
    ]


def lerp(start: tuple[int, int, int], end: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(s + (e - s) * t) for s, e in zip(start, end))  # type: ignore[return-value]


def face_color(face: list[tuple[float, float]], *, center: bool) -> tuple[int, int, int]:
    """Faces do alto ficam mais claras; as de baixo, mais profundas."""
    if center:
        return CRIMSON_HOVER
    cy = sum(point[1] for point in face) / len(face)
    t = (cy + 1.0) / 2.0
    return lerp(CRIMSON_HOVER, CRIMSON_DEEP, t)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def draw_background(draw: ImageDraw.ImageDraw, size: int, *, radius: float) -> None:
    if radius <= 0:
        draw.rectangle((0, 0, size, size), fill=INK_950)
        return
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=INK_950)


def render_icon(size: int, *, die_scale: float, corner_radius: float, numeral: bool = True) -> Image.Image:
    """Renderiza o ícone com supersampling e devolve a imagem final."""
    big = size * SS
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_background(draw, big, radius=corner_radius * big)

    center = big / 2
    scale = big * die_scale / 2
    stroke = max(1.0, big * 0.006)

    def to_px(point: tuple[float, float]) -> tuple[float, float]:
        return (center + point[0] * scale, center + point[1] * scale)

    faces = d20_faces()
    for index, face in enumerate(faces):
        points = [to_px(point) for point in face]
        draw.polygon(points, fill=(*face_color(face, center=index == 0), 255))

    # Arestas douradas: contorno externo mais grosso, internas discretas.
    for index, face in enumerate(faces):
        points = [to_px(point) for point in face]
        draw.line([*points, points[0]], fill=(*GOLD, 150), width=round(stroke), joint="curve")
    draw.line(
        [*[to_px(point) for point in hexagon()], to_px(hexagon()[0])],
        fill=(*GOLD, 255),
        width=round(stroke * 2),
        joint="curve",
    )

    if numeral:
        font = load_font(round(scale * 0.30))
        text_center = to_px((0.0, -0.02))
        draw.text(text_center, "20", font=font, fill=(*GOLD_SOFT, 255), anchor="mm")

    return image.resize((size, size), Image.LANCZOS)


def svg_markup(*, die_scale: float, corner_radius: float) -> str:
    """SVG com a mesma geometria (favicon vetorial)."""
    view = 512
    center = view / 2
    scale = view * die_scale / 2

    def to_px(point: tuple[float, float]) -> str:
        return f"{center + point[0] * scale:.2f},{center + point[1] * scale:.2f}"

    faces = d20_faces()
    polygons = []
    for index, face in enumerate(faces):
        color = face_color(face, center=index == 0)
        points = " ".join(to_px(point) for point in face)
        polygons.append(
            f'  <polygon points="{points}" fill="rgb{color}" stroke="rgb{GOLD}"'
            ' stroke-width="3" stroke-opacity="0.6" />'
        )
    outline = " ".join(to_px(point) for point in hexagon())
    radius = corner_radius * view
    body = "\n".join(polygons)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view} {view}" role="img" aria-label="Auto DM">
  <rect width="{view}" height="{view}" rx="{radius:.0f}" ry="{radius:.0f}" fill="rgb{INK_950}" />
{body}
  <polygon points="{outline}" fill="none" stroke="rgb{GOLD}" stroke-width="7" stroke-linejoin="round" />
  <text x="{center:.0f}" y="{center - scale * 0.02:.0f}" fill="rgb{GOLD_SOFT}" font-family="Georgia, 'Times New Roman', serif" font-size="{scale * 0.30:.0f}" font-weight="700" text-anchor="middle" dominant-baseline="central">20</text>
</svg>
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        ("icon-192.png", 192, 0.80, 0.18),
        ("icon-512.png", 512, 0.80, 0.18),
        ("icon-maskable-192.png", 192, 0.58, 0.0),
        ("icon-maskable-512.png", 512, 0.58, 0.0),
        ("apple-touch-icon.png", 180, 0.78, 0.0),
        ("favicon-32.png", 32, 0.92, 0.15),
    ]
    for name, size, die_scale, corner in targets:
        icon = render_icon(size, die_scale=die_scale, corner_radius=corner, numeral=size >= 64)
        icon.save(OUT_DIR / name, optimize=True)
        print(f"{name}: {size}px, {(OUT_DIR / name).stat().st_size / 1024:.1f} KiB")

    svg_path = OUT_DIR / "d20.svg"
    svg_path.write_text(svg_markup(die_scale=0.80, corner_radius=0.18), encoding="utf-8")
    print(f"d20.svg: {svg_path.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
