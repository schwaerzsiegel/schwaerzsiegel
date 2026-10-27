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
INK_MAX = 200              # Grauwert darunter gilt als Tinte – auch dünne, helle Kugelschreiberstriche
INK_BELOW_PAPER = 50       # … und mindestens so viel dunkler als das Papier (graue Scans)
CELL = 6                   # Rastergröße in Bildpunkten
CELL_MIN_INK = 4           # so viele Tintenpunkte machen eine Zelle „beschrieben“
JOIN = 3                   # Zellen Abstand, über den Striche zu einem Bereich verbunden werden
MIN_W, MIN_H = 24.0, 9.0   # kleinster Bereich in PDF-Punkten
MIN_CELLS = 8              # so viele beschriebene Zellen braucht ein Bereich (Staub bleibt darunter)
MAX_PAGE_SHARE = 0.25      # größer = eher Bild oder Rand als Unterschrift
WORD_PAD = 1.5             # Rand um erkannte Wörter, der nicht als Tinte zählt
LINE_RUN = 60              # waagerechte gerade Strecke (Bildpunkte, ca. 40 pt) = gedruckte Linie
VLINE_RUN = 30             # senkrechte gerade Strecke (ca. 20 pt) = Rand eines Kastens
LINE_GAP = 3               # so große Lücken in einer gescannten Linie werden überbrückt
PAD = 2.0                  # Rand um einen Fund in PDF-Punkten

CONF_SIGNATURE = 0.7
CONF_OTHER = 0.6

_GRUSS = re.compile(r"(?i)grü(ß|ss)e|gruss|gruß|hochachtungsvoll|^i\.?\s?a\.?$|^i\.?\s?v\.?$|^gez\.?$|"
                    r"unterschrift|^ppa\.?$")

_PUNCT = set("()[]{},.;:!?\"'„“”‚‘’«»-–")


def _is_real_word(w: dict) -> bool:
    """Wörter, die die Texterkennung sicher gelesen hat. Kritzeleien liest sie oft als Zeichensalat
    mit niedriger Sicherheit – die zählen hier als Tinte, nicht als Text."""
    # Satzzeichen wie Klammern und Komma zählen nicht mit („(TV-L),“ ist ein echtes Wort)
    text = "".join(ch for ch in w.get("text", "") if ch not in _PUNCT and not ch.isspace())
    if not text:
        return False
    alnum = sum(ch.isalnum() for ch in text)
    if alnum / len(text) < 0.5:
        return False
    conf = w.get("conf")
    return conf is None or conf >= 50


def _ink(img: np.ndarray) -> np.ndarray:
    """Tinte: deutlich dunkler als das Papier. Die Grenze richtet sich nach dem Papier (Median der Seite),
    damit helle Unterschriften auf weißem Papier zählen und grau gescanntes Papier nicht."""
    return img < min(INK_MAX, int(np.median(img)) - INK_BELOW_PAPER)


def _long_runs(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Bildpunkte, die zu waagerechten Strecken von mindestens min_len gehören (Zeile für Zeile).
    Gescannte Linien sind leicht schief und haben kleine Lücken: Die Nachbarzeilen zählen mit, Lücken
    bis LINE_GAP werden überbrückt."""
    band = mask.copy()
    band[1:] |= mask[:-1]
    band[:-1] |= mask[1:]
    out = np.zeros_like(mask)
    for y in np.nonzero(band.any(axis=1))[0]:
        row = np.concatenate(([0], band[y].astype(np.int8), [0]))
        d = np.diff(row)
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        a0 = b0 = None
        for a, b in list(zip(starts, ends)) + [(None, None)]:
            if a is not None and b0 is not None and a - b0 <= LINE_GAP:
                b0 = b
                continue
            if b0 is not None and b0 - a0 >= min_len:
                out[y, a0:b0] = True
            a0, b0 = a, b
    return out & mask


def _straight_lines(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Gedruckte Linien (Unterstriche, Tabellen, Rahmen und deren Ränder): waagerechte und senkrechte.
    Handschrift hat kaum lange gerade Strecken; ein Strich, der nur teilweise gerade ist, bleibt nach dem
    Entfernen mit seinen übrigen Teilen erhalten."""
    return _long_runs(ink, LINE_RUN), _long_runs(ink.T, VLINE_RUN).T


def _on_line(r: fitz.Rect, hlines: np.ndarray) -> bool:
    """Steht der Bereich auf einer gedruckten Linie (Unterschriftslinie)? r in angezeigten PDF-Punkten."""
    x0, x1 = int(r.x0 * ZOOM), int(r.x1 * ZOOM)
    y0, y1 = max(0, int((r.y1 - 3) * ZOOM)), int((r.y1 + 6) * ZOOM)
    band = hlines[y0:y1, x0:x1]
    return band.size > 0 and band.sum(axis=1).max() >= 0.5 * (x1 - x0)


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
    ink = _ink(img)

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

    hlines, vlines = _straight_lines(ink)
    ink &= ~(hlines | vlines)
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
        found.append((r, _on_line(r, hlines)))

    suggestions = []
    for r, on_line in found:
        r = r + (-PAD, -PAD, PAD, PAD)                 # Striche am Rand der Zellen sicher abdecken
        # Vergleich in Seitenkoordinaten wie die Wörter (bei gedrehten Seiten liegt „unter“ sonst seitlich).
        # Unterschriften stehen unter der Grußformel und beginnen etwa auf ihrer Höhe (nicht weit rechts).
        bbox = (r * pdf_page.derotation_matrix).normalize()
        gruss = any(_GRUSS.search(w["text"]) and 0 <= bbox.y0 - w["bbox"][3] <= 140
                    and bbox.x0 < w["bbox"][2] + 150 and bbox.x1 > w["bbox"][0] - 40 for _, w in text_lines)
        signature = gruss or on_line
        suggestions.append({
            "id": str(uuid4()),
            "label": "Unterschrift" if signature else "Handschrift oder Stempel",
            "text": "",
            "reason": ("Handschrift unter einer Grußformel – bitte prüfen" if gruss else
                       "Handschrift auf einer Unterschriftslinie – bitte prüfen" if on_line else
                       "Striche ohne erkannten Text (z. B. Handschrift, Stempel, Logo oder Linie) – bitte prüfen"),
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            "word_ids": [],
            "confidence": CONF_SIGNATURE if signature else CONF_OTHER,
            "source": "handschrift_heuristik",
            "default_selected": True,
            "also_detected_as": [],
        })
    return suggestions
