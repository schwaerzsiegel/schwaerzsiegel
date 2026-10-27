"""
Phase 16: Unterschriften, Stempel und handschriftliche Notizen auf Scan-Seiten finden.

Die Texterkennung liest nur gedruckten Text; eine Unterschrift ist für sie kein Text. Deshalb wird hier
nach „Tinte ohne Text“ gesucht: dunkle Bildpunkte, die nicht zu einem erkannten Wort gehören, werden zu
Bereichen zusammengefasst. Liegt ein Bereich unter einer Grußformel („Mit freundlichen Grüßen“, „i. A.“,
„Unterschrift“ …), heißt er „Unterschrift“, sonst „Handschrift oder Stempel“.

Bewusst einfach (nur PyMuPDF und NumPy, kein weiteres Modell): Es werden auch Linien, Logos oder
Kritzeleien gefunden. Deshalb sind die Vorschläge vorausgewählt, aber „zu prüfen“ (Sicherheit unter der
Grenze der Sammelbestätigung) – der Mensch entscheidet.
"""

from __future__ import annotations

import re
from collections import deque
from uuid import uuid4

import fitz
import numpy as np

ZOOM = 1.5                 # ca. 108 dpi – reicht für Striche, bleibt schnell
INK = 150                  # Grauwert darunter gilt als Tinte (auch blaue/rote Tinte)
CELL = 6                   # Rastergröße in Bildpunkten
CELL_MIN_INK = 4           # so viele Tintenpunkte machen eine Zelle „beschrieben“
JOIN = 3                   # Zellen Abstand, über den Striche zu einem Bereich verbunden werden
MIN_W, MIN_H = 24.0, 9.0   # kleinster Bereich in PDF-Punkten
MIN_CELLS = 8              # so viele beschriebene Zellen braucht ein Bereich (Staub bleibt darunter)
MAX_PAGE_SHARE = 0.25      # größer = eher Bild oder Rand als Unterschrift
WORD_PAD = 1.5             # Rand um erkannte Wörter, der nicht als Tinte zählt
LINE_RUN = 60              # gerade Strecke (Bildpunkte, ca. 40 pt) = gedruckte Linie, keine Handschrift

CONF_SIGNATURE = 0.7
CONF_OTHER = 0.6

_GRUSS = re.compile(r"(?i)grü(ß|ss)e|gruss|gruß|hochachtungsvoll|^i\.?\s?a\.?$|^i\.?\s?v\.?$|^gez\.?$|"
                    r"unterschrift|^ppa\.?$")


def _is_real_word(w: dict) -> bool:
    """Wörter, die die Texterkennung sicher gelesen hat. Kritzeleien liest sie oft als Zeichensalat
    mit niedriger Sicherheit – die zählen hier als Tinte, nicht als Text."""
    text = w.get("text", "").strip()
    if not text:
        return False
    alnum = sum(ch.isalnum() for ch in text)
    if alnum / len(text) < 0.5:
        return False
    conf = w.get("conf")
    return conf is None or conf >= 50


def _long_runs(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Bildpunkte, die zu waagerechten Strecken von mindestens min_len gehören (Zeile für Zeile)."""
    out = np.zeros_like(mask)
    for y in np.nonzero(mask.any(axis=1))[0]:
        row = np.concatenate(([0], mask[y].astype(np.int8), [0]))
        d = np.diff(row)
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        for a, b in zip(starts, ends):
            if b - a >= min_len:
                out[y, a:b] = True
    return out


def _remove_straight_lines(ink: np.ndarray) -> np.ndarray:
    """Gedruckte Linien (Unterstriche, Tabellen, Rahmen) entfernen; Handschrift hat kaum lange gerade
    Strecken. Ein Strich, der nur teilweise gerade ist, bleibt mit seinen übrigen Teilen erhalten."""
    lines = _long_runs(ink, LINE_RUN) | _long_runs(ink.T, LINE_RUN).T
    return ink & ~lines


def _components(grid: np.ndarray) -> list[list[tuple[int, int]]]:
    """Zusammenhängende beschriebene Zellen (8er-Nachbarschaft, Abstand bis JOIN)."""
    h, w = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    cells = list(zip(*np.nonzero(grid)))
    out = []
    for start in cells:
        if seen[start]:
            continue
        seen[start] = True
        queue, comp = deque([start]), []
        while queue:
            y, x = queue.popleft()
            comp.append((y, x))
            y0, y1 = max(0, y - JOIN), min(h, y + JOIN + 1)
            x0, x1 = max(0, x - JOIN), min(w, x + JOIN + 1)
            near = np.argwhere(grid[y0:y1, x0:x1] & ~seen[y0:y1, x0:x1])
            for dy, dx in near:
                p = (y0 + dy, x0 + dx)
                seen[p] = True
                queue.append(p)
        out.append(comp)
    return out


def detect(pdf_page: "fitz.Page", words: list[dict]) -> list[dict]:
    """Vorschläge für Handschrift auf einer Scan-Seite (Wörter aus der Texterkennung)."""
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    ink = img < INK

    # Erkannte Wörter ausblenden (Koordinaten: Wörter ungedreht, Bild gedreht wie angezeigt)
    rot = pdf_page.rotation_matrix
    text_lines = []
    for w in words:
        if not _is_real_word(w):
            continue
        shown = (fitz.Rect(w["bbox"]) * rot).normalize()
        text_lines.append((shown, w))
        x0 = max(0, int((shown.x0 - WORD_PAD) * ZOOM)); y0 = max(0, int((shown.y0 - WORD_PAD) * ZOOM))
        x1 = min(pix.width, int((shown.x1 + WORD_PAD) * ZOOM) + 1)
        y1 = min(pix.height, int((shown.y1 + WORD_PAD) * ZOOM) + 1)
        ink[y0:y1, x0:x1] = False

    ink = _remove_straight_lines(ink)
    gh, gw = pix.height // CELL, pix.width // CELL
    if not gh or not gw:
        return []
    counts = ink[:gh * CELL, :gw * CELL].reshape(gh, CELL, gw, CELL).sum(axis=(1, 3))
    grid = counts >= CELL_MIN_INK

    page_w, page_h = pix.width / ZOOM, pix.height / ZOOM
    found = []
    for comp in _components(grid):
        ys = [c[0] for c in comp]; xs = [c[1] for c in comp]
        r = fitz.Rect(min(xs) * CELL / ZOOM, min(ys) * CELL / ZOOM,
                      (max(xs) + 1) * CELL / ZOOM, (max(ys) + 1) * CELL / ZOOM)
        if r.width < MIN_W or r.height < MIN_H or len(comp) < MIN_CELLS:
            continue                                   # Fleck, Staub, Satzzeichen
        if r.get_area() > MAX_PAGE_SHARE * page_w * page_h or r.width > 0.9 * page_w:
            continue                                   # Rand, Schatten, großes Bild
        if r.height < 14 and r.width > 6 * r.height and len(comp) < r.width * ZOOM / CELL * 1.6:
            continue                                   # dünne Linie (Unterstrich, Tabellenrand)
        found.append(r)

    suggestions = []
    for r in found:
        # Vergleich in Seitenkoordinaten wie die Wörter (bei gedrehten Seiten liegt „unter“ sonst seitlich).
        # Unterschriften stehen unter der Grußformel und beginnen etwa auf ihrer Höhe (nicht weit rechts).
        bbox = (r * pdf_page.derotation_matrix).normalize()
        gruss = any(_GRUSS.search(w["text"]) and 0 <= bbox.y0 - w["bbox"][3] <= 140
                    and bbox.x0 < w["bbox"][2] + 150 and bbox.x1 > w["bbox"][0] - 40 for _, w in text_lines)
        label = "Unterschrift" if gruss else "Handschrift oder Stempel"
        suggestions.append({
            "id": str(uuid4()),
            "label": label,
            "text": "",
            "reason": ("Handschrift unter einer Grußformel – bitte prüfen" if gruss else
                       "Striche ohne erkannten Text (z. B. Handschrift, Stempel, Logo oder Linie) – bitte prüfen"),
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            "word_ids": [],
            "confidence": CONF_SIGNATURE if gruss else CONF_OTHER,
            "source": "handschrift_heuristik",
            "default_selected": True,
            "also_detected_as": [],
        })
    return suggestions
