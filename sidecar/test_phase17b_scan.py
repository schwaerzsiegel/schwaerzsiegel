"""
Phase 17b: gescannter Vertrag – Kästen ohne Fehlalarm, helle Unterschrift auf der Linie, Rollenwörter.

Nachgestellt nach einem Fall aus der Praxis (Arbeitsvertrag als Scan): Kästen um Textzeilen wurden als
„Handschrift oder Stempel“ vorgeschlagen, eine dünne, helle Unterschrift auf der Unterschriftslinie nicht
gefunden, und „Beschäftigter“ wurde als Person vorgeschlagen. Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase17b_scan
"""

import sys
import unittest
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "eval"))

from core import handschrift, rules  # noqa: E402
from test_phase16_handschrift import _analyse, _hits  # noqa: E402
from test_phase17_erkennung import detect, found  # noqa: E402
from test_phase5_ocr import ocr_ready  # noqa: E402

LIGHT_PEN = (0.55, 0.6, 0.85)   # heller Kugelschreiber


def _vertrag_scan(draw_signature: bool = True, tilt: float = 1.2, dpi: int = 200) -> tuple[bytes, fitz.Rect]:
    """Vertragsseite mit Kästen um Textzeilen (leicht schief wie gescannt) und Unterschriftsfeld."""
    src = fitz.open()
    p = src.new_page()

    def box(y, h, text_lines):
        # leicht schiefer Rahmen: rechts um „tilt“ Punkte tiefer
        a, b = fitz.Point(60, y), fitz.Point(540, y + tilt)
        c, d = fitz.Point(540, y + h + tilt), fitz.Point(60, y + h)
        for s, e in ((a, b), (b, c), (c, d), (d, a)):
            p.draw_line(s, e, width=0.7)
        for i, t in enumerate(text_lines):
            p.insert_text((66, y + 15 + i * 14), t, fontsize=10, fontname="helv")

    p.insert_text((60, 70), "Arbeitsvertrag", fontsize=14, fontname="hebo")
    box(90, 22, ["Herrn Jonas Beispiel         geboren am 3. Mai 1985               (Beschäftigter)"])
    p.insert_text((290, 140), "§1", fontsize=10, fontname="hebo")
    box(150, 22, ["Herr Beispiel wird ab 1. Januar 2024 auf unbestimmte Zeit eingestellt."])
    box(190, 50, ["Für das Arbeitsverhältnis gelten die Tarifverträge (TV-L),",
                  "die für den Bereich der Tarifgemeinschaft jeweils gelten.",
                  "Der Beschäftigte ist in der Entgeltgruppe 9 eingruppiert."])
    p.insert_text((380, 300), "Beschäftigter", fontsize=10, fontname="hebo")
    p.insert_text((380, 316), "Musterdorf, 20. November 2023", fontsize=10, fontname="helv")
    p.draw_line((380, 352), (540, 352), width=0.6)                     # Unterschriftslinie
    area = fitz.Rect(386, 322, 520, 356)
    if draw_signature:
        shape = p.new_shape()
        x, y = 392, 348                                                 # Striche sitzen auf der Linie
        pts = [(x, y), (x + 10, y - 20), (x + 20, y + 3), (x + 34, y - 16), (x + 48, y + 2), (x + 62, y - 22),
               (x + 76, y + 3), (x + 92, y - 10), (x + 108, y + 2), (x + 122, y - 6)]
        for s, e in zip(pts, pts[1:]):
            shape.draw_bezier(s, (s[0] + (e[0] - s[0]) / 3, s[1] - 8), (s[0] + 2 * (e[0] - s[0]) / 3, e[1] + 8), e)
        shape.finish(color=LIGHT_PEN, width=0.6, closePath=False)
        shape.commit()
    pix = p.get_pixmap(dpi=dpi)
    out = fitz.open()
    scan = out.new_page(width=p.rect.width, height=p.rect.height)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    data = out.tobytes()
    out.close(); src.close()
    return data, area


@unittest.skipUnless(ocr_ready(), "Tesseract mit deutschem Sprachpaket nicht verfügbar")
class ScanVertragTest(unittest.TestCase):
    def test_boxes_around_text_lines_give_no_handwriting(self):
        found_ = _analyse(_vertrag_scan(draw_signature=False)[0])
        self.assertEqual(found_, [], "Kastenränder dürfen nicht als Handschrift gelten")

    def test_light_signature_on_signature_line_is_found_as_signature(self):
        data, area = _vertrag_scan()
        found_ = _analyse(data)
        hits = _hits(found_, area)
        self.assertEqual(len(hits), 1, found_)
        self.assertEqual(hits[0]["label"], "Unterschrift")
        self.assertTrue(hits[0]["default_selected"])
        self.assertLess(hits[0]["confidence"], 0.85)
        # nur die Unterschrift, keine Fehlalarme an den Kästen
        self.assertEqual(len(found_), 1, found_)
        # die ganze Unterschrift liegt im Vorschlag (bis zum letzten Strich)
        sig = fitz.Rect(392, 326, 514, 351)
        self.assertTrue(fitz.Rect(hits[0]["bbox"]).contains(sig), (hits[0]["bbox"], sig))

    def test_light_paper_threshold_follows_paper(self):
        import numpy as np
        white = np.full((10, 10), 255, dtype=np.uint8)
        grey = np.full((10, 10), 215, dtype=np.uint8)
        white[0, 0] = grey[0, 0] = 190                  # heller Strich
        self.assertTrue(handschrift._ink(white)[0, 0], "heller Strich auf weißem Papier ist Tinte")
        self.assertFalse(handschrift._ink(grey)[0, 0], "auf grauem Papier ist derselbe Grauwert Papier")
        self.assertFalse(handschrift._ink(grey)[1, 1])

    def test_words_with_brackets_count_as_text(self):
        self.assertTrue(handschrift._is_real_word({"text": "(TV-L),", "conf": 90}))
        self.assertTrue(handschrift._is_real_word({"text": "(Beschäftigter)", "conf": 90}))
        self.assertFalse(handschrift._is_real_word({"text": "_/7", "conf": 90}))
        self.assertFalse(handschrift._is_real_word({"text": "Kritzel", "conf": 20}))


class RollenwoerterTest(unittest.TestCase):
    ROLLEN = ["Beschäftigter", "(Beschäftigter)", "Beschäftigten", "Herrn", "er", "Arbeitgeber",
              "Mitarbeiterin", "Antragsteller", "Patientin", "der Beschäftigte", "Kollege", "Kollegin"]

    def test_role_words_from_ai_are_not_proposed_as_person(self):
        line = ("Der Beschäftigte (Beschäftigter) und den Beschäftigten bittet Herrn er Arbeitgeber "
                "Mitarbeiterin Antragsteller Patientin der Beschäftigte")
        hits = [("Person", r, 0.9) for r in ["Beschäftigter", "(Beschäftigter)", "Beschäftigten", "Herrn", "er",
                                             "Arbeitgeber", "Mitarbeiterin", "Antragsteller", "Patientin"]]
        s = detect(line, hits)
        self.assertEqual(found(s, "Person"), [], found(s, "Person"))

    def test_names_from_ai_are_still_proposed(self):
        s = detect("Der Beschäftigte Jonas Beispiel und Erika Mustermann unterschreiben.",
                   [("Person", "Jonas Beispiel", 0.9), ("Person", "Erika Mustermann", 0.9)])
        self.assertEqual(sorted(x["text"] for x in found(s, "Person")), ["Erika Mustermann", "Jonas Beispiel"])

    def test_rule_with_salutation_is_unchanged(self):
        s = detect("Herr Beispiel wird ab 1. Januar 2024 eingestellt.")
        self.assertEqual([x["text"] for x in found(s, "Person")], ["Beispiel"])

    def test_plausible_person(self):
        for role in self.ROLLEN:
            self.assertFalse(rules.plausible_person(role), role)
        for name in ["Erika Mustermann", "Herr Müller", "Frau Dr. Seidel", "Leitner", "Kindermann", "Max"]:
            self.assertTrue(rules.plausible_person(name), name)


if __name__ == "__main__":
    unittest.main()
