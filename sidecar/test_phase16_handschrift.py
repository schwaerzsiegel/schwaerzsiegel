"""
Phase 16: Unterschriften, Stempel und Handschrift auf Scan-Seiten (core/handschrift.py).

Nur synthetische Dokumente: erfundene Namen, gezeichnete Fantasie-Unterschrift.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase16_handschrift
"""

import sys
import unittest
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "eval"))

import lange_dokumente as L  # noqa: E402
from core import marks  # noqa: E402
from core import pdf_processor as pdf  # noqa: E402
from test_phase5_ocr import ocr_ready  # noqa: E402

LINES = ["Erika Musterfrau", "Musterweg 12", "24103 Kiel", "", "Antrag auf Akteneinsicht", "",
         "Sehr geehrte Damen und Herren,", "", "bitte senden Sie mir die Unterlagen zu.", "",
         "Mit freundlichen Grüßen"]


def _scan(draw=None, rotate: int = 0, dpi: int = 200) -> tuple[bytes, dict]:
    """Brief als reines Bild-PDF; draw(page, y_nach_text) zeichnet Handschrift und liefert Soll-Bereiche."""
    src = fitz.open()
    page = src.new_page()
    y = 70
    for line in LINES:
        page.insert_text((60, y), line, fontsize=11, fontname="helv")
        y += 16
    areas = draw(page, y) if draw else {}
    pix = page.get_pixmap(dpi=dpi)
    out = fitz.open()
    scan = out.new_page(width=page.rect.width, height=page.rect.height)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    if rotate:
        scan.set_rotation(rotate)
    data = out.tobytes()
    out.close(); src.close()
    return data, areas


def _analyse(data: bytes):
    with fitz.open(stream=data, filetype="pdf") as d:
        page = pdf.extract_page(d[0], 0)
    marks.init_auto(page)
    return [s for s in page.suggestions if s["source"] == "handschrift_heuristik"]


def _hits(found, area) -> list:
    r = fitz.Rect(area)
    return [s for s in found if fitz.Rect(s["bbox"]).intersects(r)]


@unittest.skipUnless(ocr_ready(), "Tesseract mit deutschem Sprachpaket nicht verfügbar")
class HandschriftTest(unittest.TestCase):
    def test_signature_below_greeting_is_found_as_signature(self):
        data, areas = _scan(lambda p, y: {"sig": L._signature_strokes(p, 64, y + 26)})
        found = _analyse(data)
        hits = _hits(found, areas["sig"])
        self.assertEqual(len(hits), 1, found)
        s = hits[0]
        self.assertEqual(s["label"], "Unterschrift")
        self.assertTrue(s["default_selected"])
        self.assertLess(s["confidence"], 0.85, "darf nicht in die Sammelbestätigung „sichere Vorschläge“")
        self.assertEqual(s["kind"], "area")
        self.assertEqual(s["origin"], "rule")
        # Der Vorschlag deckt die Unterschrift weitgehend ab
        inter = fitz.Rect(s["bbox"]) & fitz.Rect(areas["sig"])
        self.assertGreater(inter.get_area(), 0.6 * fitz.Rect(areas["sig"]).get_area())

    def test_stamp_and_note_are_found_as_handwriting_or_stamp(self):
        def draw(p, y):
            return {"stempel": L._stamp(p, (470, 120)), "notiz": L._note(p, 380, y + 120)}
        data, areas = _scan(draw)
        found = _analyse(data)
        for name in ("stempel", "notiz"):
            hits = _hits(found, areas[name])
            self.assertTrue(hits, f"{name} nicht gefunden: {found}")
            self.assertEqual(hits[0]["label"], "Handschrift oder Stempel")

    def test_clean_scan_gives_no_handwriting(self):
        self.assertEqual(_analyse(_scan()[0]), [])

    def test_printed_underline_and_table_lines_are_ignored(self):
        def draw(p, y):
            p.draw_line((60, y + 30), (400, y + 30), width=0.8)      # Unterstrich
            p.draw_rect(fitz.Rect(60, y + 60, 500, y + 120), width=0.8)  # leere Tabelle
            return {}
        self.assertEqual(_analyse(_scan(draw)[0]), [])

    def test_scan_noise_specks_are_ignored(self):
        import random
        rnd = random.Random(16)

        def draw(p, y):
            for _ in range(300):  # Staub und Fussel eines Scanners
                x0, y0 = rnd.uniform(20, 570), rnd.uniform(20, 820)
                p.draw_circle((x0, y0), rnd.uniform(0.3, 1.2), color=(0.2, 0.2, 0.2), fill=(0.2, 0.2, 0.2))
            return {}
        self.assertEqual(_analyse(_scan(draw)[0]), [])

    def test_low_resolution_scan_still_finds_signature(self):
        data, areas = _scan(lambda p, y: {"sig": L._signature_strokes(p, 64, y + 26)}, dpi=150)
        self.assertTrue(_hits(_analyse(data), areas["sig"]))

    def test_rotated_scan_gives_box_in_page_coordinates(self):
        # Seite um 90° gedreht: Vorschläge liegen wie die Wörter in ungedrehten Seitenkoordinaten,
        # also dort, wo die Unterschrift im Seitenbild liegt.
        data, areas = _scan(lambda p, y: {"sig": L._signature_strokes(p, 64, y + 26)}, rotate=90)
        found = _analyse(data)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(_hits(found, areas["sig"]), (found, areas))
        self.assertEqual(found[0]["label"], "Unterschrift")

    def test_confirmed_signature_is_removed_from_scan_image(self):
        """Ende zu Ende: erkannte Unterschrift bestätigen, schwärzen, Prüfung bestehen, Striche weg."""
        from test_phase1_redaction import build_document, dark_pixels
        data, areas = _scan(lambda p, y: {"sig": L._signature_strokes(p, 64, y + 26)})
        document = build_document(data, detect=True)
        self.addCleanup(lambda: pdf.forget(document["_doc_id"]))
        sigs = [s for s in document["pages"][0].suggestions if s["label"] == "Unterschrift"]
        self.assertEqual(len(sigs), 1)
        sigs[0]["status"] = marks.CONFIRMED
        out, targets = pdf.finalize_redaction(document)
        self.addCleanup(lambda: Path(out).unlink(missing_ok=True))
        result = pdf.verify_redaction(out, document, targets)
        self.assertTrue(result.passed, result)
        zoom = 2
        with fitz.open(stream=data, filetype="pdf") as before:
            self.assertGreater(dark_pixels(before[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom)),
                                           fitz.Rect(areas["sig"]), zoom), 200, "Testbild ohne Unterschrift?")
        with fitz.open(out) as after:
            pix = after[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        # Der vorgeschlagene Bereich ist jetzt eine geschlossene schwarze Fläche (Striche nicht mehr erkennbar) …
        r = fitz.Rect(sigs[0]["bbox"])
        inner = fitz.Rect(r.x0 + 2, r.y0 + 2, r.x1 - 2, r.y1 - 2)
        total = int(inner.width * zoom - 2) * int(inner.height * zoom - 2)
        self.assertGreater(dark_pixels(pix, inner, zoom), 0.95 * total, "Bereich nicht vollständig geschwärzt")
        # … und die Grußformel darüber bleibt lesbar
        self.assertGreater(dark_pixels(pix, fitz.Rect(60, 230, 200, 245), zoom), 50)

    def test_native_text_pdf_is_not_analysed_for_handwriting(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((60, 80), "Mit freundlichen Grüßen", fontsize=11)
        L._signature_strokes(page, 64, 120)  # Vektor-Unterschrift: dafür gibt es die Warnung beim Schwärzen
        data = doc.tobytes(); doc.close()
        self.assertEqual(_analyse(data), [])


if __name__ == "__main__":
    unittest.main()
