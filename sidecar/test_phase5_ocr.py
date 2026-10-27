"""
Tests Phase 5: Texterkennung (OCR) für gescannte PDFs.
Die "Scans" werden synthetisch erzeugt: ein Brief mit erfundenen Daten
wird gerastert und nur als Bild in ein neues PDF gelegt (keine Textebene).
Benötigt Tesseract mit deutschem Sprachpaket - sonst werden die Tests
übersprungen (das Verhalten ohne Tesseract prüft test_phase1_redaction).

    python -m unittest -v test_phase5_ocr
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "f" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
sys.path.insert(0, str(Path(__file__).parent))

import fitz  # noqa: E402

from core import ocr  # noqa: E402
from test_phase1_redaction import (  # noqa: E402
    LETTER, RedactionTestCase, add_text_mark, build_document, dark_pixels, display_box, find_words,
    raw_contains, write_lines,
)


def ocr_ready() -> bool:
    import main
    return main._ocr_available()


def scanned_pdf(lines=LETTER, rotate: int = 0, dpi: int = 200) -> bytes:
    """Brief rendern und nur als Bild einbetten = Scan ohne Textebene."""
    src = fitz.open()
    write_lines(src.new_page(), lines, fontsize=12, lead=18)
    pix = src[0].get_pixmap(dpi=dpi)
    src.close()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    if rotate:
        page.set_rotation(rotate)
    data = doc.tobytes()
    doc.close()
    return data


@unittest.skipUnless(ocr_ready(), "Tesseract mit deutschem Sprachpaket nicht verfügbar")
class OcrTests(RedactionTestCase):
    def test_scan_words_are_recognised_with_positions(self):
        document = build_document(scanned_pdf())
        page = document["pages"][0]
        self.addCleanup(lambda: __import__("core.pdf_processor").pdf_processor.forget(document["_doc_id"]))
        self.assertTrue(page.words)
        self.assertEqual({w["source"] for w in page.words}, {"ocr"})
        words = find_words(page, "Erika Mustermann")
        # Der Text liegt im Original bei y ~ 72+18 (Grundlinie), Höhe ~12 pt
        y0, y1 = words[0]["bbox"][1], words[0]["bbox"][3]
        self.assertTrue(75 < y0 < 92 and 84 < y1 < 100, words[0]["bbox"])
        self.assertTrue(page.words[0]["line"])

    def test_rules_detect_personal_data_on_scan(self):
        document = build_document(scanned_pdf(), detect=True)
        self.addCleanup(lambda: __import__("core.pdf_processor").pdf_processor.forget(document["_doc_id"]))
        labels = {m["label"] for m in document["pages"][0].suggestions}
        for expected in ("IBAN", "E-Mail", "Telefonnummer"):
            self.assertIn(expected, labels, labels)

    def test_scan_redaction_removes_pixels_and_passes_verification(self):
        document = build_document(scanned_pdf())
        box = display_box(document, 0, "Erika Mustermann")
        mark = add_text_mark(document, 0, "Erika Mustermann")
        mark["status"] = "confirmed"
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        with fitz.open(out) as doc:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
            # Schwärzung = schwarze Fläche; im Bild darunter kein Original mehr
            self.assertGreater(dark_pixels(pix, box, 2), 0.8 * (box.width * 2 - 2) * (box.height * 2 - 2))
            self.assertEqual(doc[0].get_text().strip(), "")
        # Eine erneute Texterkennung des Ergebnisses findet den Namen nicht mehr
        with fitz.open(out) as doc:
            again = " ".join(w["text"] for w in ocr.extract_words(doc[0], 0))
        self.assertNotIn("Mustermann", again)
        self.assertIn("Personalakte", again)
        self.assertFalse(raw_contains(out, "Mustermann"))

    def test_rotated_scan(self):
        document = build_document(scanned_pdf(rotate=90))
        self.addCleanup(lambda: __import__("core.pdf_processor").pdf_processor.forget(document["_doc_id"]))
        page = document["pages"][0]
        words = find_words(page, "Erika Mustermann")
        # Wörter liegen wie nativer Text in ungedrehten Koordinaten
        self.assertLess(words[0]["bbox"][1], 100, words[0]["bbox"])
        mark = add_text_mark(document, 0, "Erika Mustermann")
        mark["status"] = "confirmed"
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        with fitz.open(out) as doc:
            again = " ".join(w["text"] for w in ocr.extract_words(doc[0], 0))
        self.assertNotIn("Mustermann", again)

    def test_status_reports_ocr(self):
        from fastapi.testclient import TestClient
        import main
        with patch("main.models.start_preload"), TestClient(main.app) as client:
            status = client.get("/status", headers={"X-Redaction-Session": os.environ["REDACTION_SESSION_TOKEN"]}).json()
        self.assertTrue(status["ocr"])


class OcrLanguageTests(unittest.TestCase):
    def test_missing_language_is_reported_as_unavailable(self):
        with patch.dict(os.environ, {"TESSDATA_PREFIX": tempfile.mkdtemp(prefix="leer-")}):
            self.assertFalse(ocr.language_available())


if __name__ == "__main__":
    unittest.main()
