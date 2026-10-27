"""
Phase 7: Programmsymbol und Installer-Grafiken von Schwärzsiegel aus
Logo-Entwurf A erzeugen (rein lokal, PyMuPDF rendert das SVG, Pillow
schreibt ICO/BMP).

Aufruf (im Projektordner):
    .venv\\Scripts\\python.exe tools\\branding\\erzeugen.py

Ergebnis:
    tools/branding/logo.svg           Bildmarke (Quelle, auch für Druck/Web)
    src-tauri/icons/*.png, icon.ico   Programmsymbol (Kachel mit Bildmarke)
    src-tauri/installer/*.bmp, *.ico  Grafiken des Installationsassistenten
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "src-tauri" / "icons"
INSTALLER = ROOT / "src-tauri" / "installer"

BAR = "#11151b"
SEAL = "#9b2a24"  # Siegellack-Rot
TILE = "#ffffff"
TILE_LINE = "#d9dce1"


def mark(x: float, y: float, scale: float, simple: bool = False) -> str:
    """Bildmarke A (Grundmaß 96 x 64). `simple`: ohne Zierring, kräftigerer
    Haken – für 16 bis 32 Pixel, wo der gestrichelte Ring nur verschwimmt."""
    ring = (
        ""
        if simple
        else '<circle cx="70" cy="32" r="16.5" fill="none" stroke="#fff" stroke-width="1.6" stroke-dasharray="2.4 2.4"/>'
    )
    check_width = 5 if simple else 3.4
    return (
        f'<g transform="translate({x} {y}) scale({scale})">'
        f'<rect x="2" y="22" width="62" height="20" rx="2" fill="{BAR}"/>'
        f'<circle cx="70" cy="32" r="22" fill="{SEAL}"/>{ring}'
        f'<path d="M61.5 32.5l6 6 11-12" fill="none" stroke="#fff" stroke-width="{check_width}" '
        'stroke-linecap="round" stroke-linejoin="round"/></g>'
    )


def svg(width: int, height: int, body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{body}</svg>'


def render(source: str, size: tuple[int, int], alpha: bool = True) -> Image.Image:
    doc = fitz.open("svg", source.encode("utf-8"))
    page = doc[0]
    zoom = size[0] / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=alpha)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return image.convert("RGBA" if alpha else "RGB").resize(size, Image.LANCZOS)


def tile(px: int) -> Image.Image:
    """Quadratische helle Kachel mit Bildmarke – auf heller und dunkler
    Taskleiste gleich gut sichtbar (der schwarze Balken allein wäre auf
    dunklem Grund kaum zu sehen)."""
    simple = px <= 32
    n = 64.0  # Zeichenraster
    radius = 12 if px > 32 else 10
    inset = 1.5 if px > 32 else 0.5
    # Sichtbarer Teil der Bildmarke: x 2..92, y 10..54 (Grundmaß 96 x 64)
    width = 58.0 if simple else 50.0
    scale = width / 90
    body = (
        f'<rect x="{inset}" y="{inset}" width="{n - 2 * inset}" height="{n - 2 * inset}" rx="{radius}" '
        f'fill="{TILE}" stroke="{TILE_LINE}" stroke-width="1"/>'
        + mark((n - width) / 2 - 2 * scale, (n - 64 * scale) / 2, scale, simple)
    )
    return render(svg(64, 64, body), (px, px))


def main() -> None:
    (ROOT / "tools" / "branding" / "logo.svg").write_text(svg(96, 64, mark(0, 0, 1)) + "\n", encoding="utf-8")

    ICONS.mkdir(parents=True, exist_ok=True)
    for name, px in {"32x32.png": 32, "128x128.png": 128, "128x128@2x.png": 256, "icon.png": 512}.items():
        tile(px).save(ICONS / name)

    sizes = [16, 24, 32, 48, 64, 256]
    frames = [tile(px) for px in sizes]
    frames[-1].save(ICONS / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])

    INSTALLER.mkdir(parents=True, exist_ok=True)
    frames[-1].save(INSTALLER / "installer.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])

    # Kopfgrafik (150 x 57) und Seitenleiste (164 x 314) des NSIS-Assistenten
    header = svg(150, 57, '<rect width="150" height="57" fill="#ffffff"/>' + mark(84, 9.5, 0.6))
    render(header, (150, 57), alpha=False).save(INSTALLER / "header.bmp")

    sidebar = svg(
        164,
        314,
        '<rect width="164" height="314" fill="#f6f4f1"/>'
        f'<rect x="0" y="0" width="6" height="314" fill="{SEAL}"/>'
        + mark(26, 92, 1.15)
        + f'<text x="82" y="200" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" '
        f'font-size="19" font-weight="700" fill="{BAR}">Schwärzsiegel</text>'
        + '<text x="82" y="224" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" '
        'font-size="10.5" fill="#4a5363">Sicher geschwärzt.</text>'
        + '<text x="82" y="238" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" '
        'font-size="10.5" fill="#4a5363">Nachweisbar geprüft.</text>',
    )
    render(sidebar, (164, 314), alpha=False).save(INSTALLER / "sidebar.bmp")
    print("Symbole und Installer-Grafiken erzeugt.")


if __name__ == "__main__":
    main()
