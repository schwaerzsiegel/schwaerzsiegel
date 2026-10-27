"""
Abnahmetests Phase 1: sichere Schwärzung und manuelle Markierungen.
Ausschließlich synthetische Daten (erfundene Namen, Test-IBAN der
Bundesbank-Beispielrechnung, example.org-Adressen).

Ausführen (im Ordner sidecar, mit der Projekt-.venv):
    python -m unittest -v test_phase1_redaction
"""

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("REDACTION_SESSION_TOKEN", "b" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
sys.path.insert(0, str(Path(__file__).parent))

import fitz  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from core import marks, ner, redaction  # noqa: E402
from core import pdf_processor as pdf  # noqa: E402

LETTER = [
    "Personalakte",
    "Name: Erika Mustermann",
    "Personalnummer: 104827",
    "Telefon: 030 12345678",
    "E-Mail: erika.mustermann@example.org",
    "IBAN: DE89 3704 0044 0532 0130 00",
    "Geburtsdatum: 12.03.1985",
    "Ende des Dokuments",
]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def pdf_bytes(build) -> bytes:
    doc = fitz.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def write_lines(page, lines, *, x=72, y=72, lead=13.2, fontsize=11):
    for line in lines:
        page.insert_text((x, y), line, fontsize=fontsize, fontname="helv")
        y += lead


def build_document(data: bytes, detect: bool = False) -> dict:
    """Wie main._process_document, aber synchron und ohne KI-Modelle."""
    data, notices = redaction.prepare_document(data)
    pages = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for i, pdf_page in enumerate(doc):
            page = pdf.extract_page(pdf_page, i, detect_images=detect)
            if detect:
                page.suggestions.extend(ner.detect(page.words, use_llm=False, use_gliner=False))
            marks.init_auto(page)
            pages.append(page)
    doc_id = "test-" + uuid4().hex
    pdf.register(doc_id, data)
    return {"_doc_id": doc_id, "pages": pages, "notices": notices}


def find_words(page, phrase: str) -> list[dict]:
    tokens = phrase.split()
    words = page.words
    for i in range(len(words) - len(tokens) + 1):
        if [w["text"] for w in words[i:i + len(tokens)]] == tokens:
            return words[i:i + len(tokens)]
    raise AssertionError(f"'{phrase}' nicht auf Seite {page.index + 1} gefunden: {[w['text'] for w in words]}")


def add_text_mark(document, page_index: int, phrase: str) -> dict:
    page = document["pages"][page_index]
    words = find_words(page, phrase)
    mark = marks.new_manual(page, kind="text", rects=marks.line_rects(words),
                            text=" ".join(w["text"] for w in words), word_ids=[w["id"] for w in words])
    page.suggestions.append(mark)
    return mark


def add_area_mark(document, page_index: int, display_rect) -> dict:
    page = document["pages"][page_index]
    rect = marks.from_display(page, list(display_rect))
    assert rect is not None, display_rect
    mark = marks.new_manual(page, kind="area", rects=[rect])
    page.suggestions.append(mark)
    return mark


def display_box(document, page_index: int, phrase: str) -> fitz.Rect:
    page = document["pages"][page_index]
    words = find_words(page, phrase)
    boxes = [fitz.Rect(marks.to_display(page, w["bbox"])) for w in words]
    result = boxes[0]
    for b in boxes[1:]:
        result |= b
    return result


def dark_pixels(png_or_pix, rect: fitz.Rect, zoom: float) -> int:
    pix = png_or_pix
    count = 0
    for y in range(int(rect.y0 * zoom) + 1, int(rect.y1 * zoom) - 1):
        for x in range(int(rect.x0 * zoom) + 1, int(rect.x1 * zoom) - 1):
            if 0 <= x < pix.width and 0 <= y < pix.height and pix.pixel(x, y)[0] < 100:
                count += 1
    return count


def raw_contains(path, needle: str) -> bool:
    """Sucht needle in allen Objekten und entpackten Streams der Datei."""
    with fitz.open(path) as doc:
        for xref in range(1, doc.xref_length()):
            try:
                if needle in doc.xref_object(xref):
                    return True
                stream = doc.xref_stream(xref)
                if stream and needle.encode("latin-1", "ignore") in stream:
                    return True
            except Exception:
                continue
    return False


class RedactionTestCase(unittest.TestCase):
    def finalize(self, document):
        out, targets = pdf.finalize_redaction(document)
        self.addCleanup(lambda: Path(out).unlink(missing_ok=True))
        self.addCleanup(lambda: pdf.forget(document["_doc_id"]))
        result = pdf.verify_redaction(out, document, targets)
        return out, result

    def page_text(self, out, index=0) -> str:
        with fitz.open(out) as doc:
            return doc[index].get_text()


# ---------------------------------------------------------------------------
# 1.1 Der gemeldete Fehler und seine Ursache
# ---------------------------------------------------------------------------

class OriginalBugRegressionTests(RedactionTestCase):
    def test_tight_line_spacing_passes_without_damaging_neighbour_lines(self):
        """Vorher: Box über die volle Zeilenhöhe -> Nachbarzeilen angeschnitten
        ("Personalakte" -> "Person") oder Prüfung gesperrt."""
        for lead in (12.65, 13.2, 14.0, 16.0):
            with self.subTest(zeilenabstand=lead):
                document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER, lead=lead)))
                for phrase in ("Erika Mustermann", "104827", "030 12345678", "12.03.1985"):
                    add_text_mark(document, 0, phrase)
                out, result = self.finalize(document)
                self.assertTrue(result.passed, result.residual_text_found)
                text = self.page_text(out)
                for secret in ("Erika", "Mustermann", "104827", "12345678", "12.03.1985"):
                    self.assertNotIn(secret, text)
                for kept in ("Personalakte", "Personalnummer:", "Telefon:", "Geburtsdatum:", "Ende des Dokuments"):
                    self.assertIn(kept, text)
                self.assertFalse(any("mit entfernt" in w for w in result.warnings), result.warnings)

    def test_area_with_margin_keeps_neighbour_lines(self):
        """Ein von Hand gezogenes Rechteck hat meist etwas Rand. Vorher hat
        MuPDF damit die halbe Nachbarzeile gelöscht."""
        for pad in (1, 2, 3):
            with self.subTest(rand=pad):
                document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
                box = display_box(document, 0, "Personalnummer: 104827")
                add_area_mark(document, 0, (box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad))
                out, result = self.finalize(document)
                self.assertTrue(result.passed, result.residual_text_found)
                text = self.page_text(out)
                self.assertNotIn("104827", text)
                self.assertIn("Name: Erika Mustermann", text)
                self.assertIn("Telefon: 030 12345678", text)

    def test_area_cutting_through_a_word_does_not_cause_false_lock(self):
        """Ein Rechteck, das ein Wort nur teilweise abdeckt, darf keine
        Sperre auslösen (vorher: angeschnittenes Wort = "Resttext")."""
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), ["GEHEIMNIS offen"])))
        box = display_box(document, 0, "GEHEIMNIS")
        add_area_mark(document, 0, (box.x0 - 2, box.y0 - 2, box.x0 + box.width * 0.3, box.y1 + 2))
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertIn("offen", self.page_text(out))


# ---------------------------------------------------------------------------
# 1.2 / 1.4 Manuelle Markierungen
# ---------------------------------------------------------------------------

class ManualMarkingTests(RedactionTestCase):
    def test_single_page_area_mark(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        box = display_box(document, 0, "erika.mustermann@example.org")
        add_area_mark(document, 0, (box.x0 - 1, box.y0 - 1, box.x1 + 1, box.y1 + 1))
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertNotIn("example.org", self.page_text(out))
        self.assertIn("E-Mail:", self.page_text(out))

    def test_multi_page_marks(self):
        def build(doc):
            for n in range(3):
                write_lines(doc.new_page(), [f"Seite {n + 1}", f"Kundennummer K-{n}4711", "Ende"])
        document = build_document(pdf_bytes(build))
        add_text_mark(document, 0, "K-04711")
        add_text_mark(document, 2, "K-24711")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertNotIn("K-04711", self.page_text(out, 0))
        self.assertIn("K-14711", self.page_text(out, 1))  # Seite 2 unberührt
        self.assertNotIn("K-24711", self.page_text(out, 2))

    def test_multiline_text_selection_uses_one_box_per_line(self):
        """Mehrzeilige Auswahl: rechts daneben stehender Text bleibt erhalten."""
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["Anschrift: Musterweg 12a", "12345 Beispielstadt"])
            write_lines(page, ["RECHTS-OBEN bleibt", "RECHTS-UNTEN bleibt"], x=330)
        document = build_document(pdf_bytes(build))
        mark = add_text_mark(document, 0, "Musterweg 12a 12345 Beispielstadt")
        self.assertEqual(len(mark["rects"]), 2)
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        text = self.page_text(out)
        for secret in ("Musterweg", "12345", "Beispielstadt"):
            self.assertNotIn(secret, text)
        for kept in ("Anschrift:", "RECHTS-OBEN", "RECHTS-UNTEN"):
            self.assertIn(kept, text)

    def test_overlapping_marks(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        add_text_mark(document, 0, "Erika Mustermann")
        box = display_box(document, 0, "Mustermann")
        add_area_mark(document, 0, (box.x0 - 5, box.y0 - 1, box.x1 + 5, box.y1 + 1))
        add_area_mark(document, 0, (box.x0, box.y0, box.x1, box.y1))
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertNotIn("Mustermann", self.page_text(out))

    def test_automatic_and_manual_marks_combined(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)), detect=True)
        page = document["pages"][0]
        auto = {m["label"]: m for m in page.suggestions if m["origin"] == "rule"}
        self.assertIn("IBAN", auto)
        self.assertIn("E-Mail", auto)
        self.assertTrue(all(m["status"] == marks.SUGGESTED for m in auto.values()))
        auto["IBAN"]["status"] = marks.CONFIRMED
        auto["E-Mail"]["status"] = marks.REJECTED  # verworfen -> bleibt sichtbar
        add_text_mark(document, 0, "Erika Mustermann")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        text = self.page_text(out)
        self.assertNotIn("DE89", text)
        self.assertNotIn("Mustermann", text.replace("erika.mustermann", ""))
        self.assertIn("erika.mustermann@example.org", text)

    def test_suggested_marks_are_not_redacted_without_confirmation(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)), detect=True)
        self.assertEqual(marks.confirmed_by_page(document), {})

    def test_edited_text_mark_becomes_area(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        mark = add_text_mark(document, 0, "104827")
        page = document["pages"][0]
        box = display_box(document, 0, "104827")
        marks.set_rects(mark, [marks.from_display(page, [box.x0 - 3, box.y0 - 1, box.x1 + 3, box.y1 + 1])])
        self.assertEqual(mark["kind"], "area")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertNotIn("104827", self.page_text(out))

    def test_review_signature_changes_with_coordinates(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        mark = add_text_mark(document, 0, "104827")
        before = marks.signature(document)
        marks.set_rects(mark, [[r[0], r[1], r[2] + 5, r[3]] for r in mark["rects"]])
        self.assertNotEqual(before, marks.signature(document))


# ---------------------------------------------------------------------------
# Koordinaten: Rotation, Zoom, verschobene Seitenbox
# ---------------------------------------------------------------------------

class CoordinateTests(RedactionTestCase):
    def test_rotated_pages(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                def build(doc):
                    page = doc.new_page()
                    write_lines(page, ["GEHEIM 4711", "OFFEN bleibt"])
                    page.set_rotation(rotation)
                document = build_document(pdf_bytes(build))
                page = document["pages"][0]
                self.assertEqual(page.geometry["rotation"], rotation)
                box = display_box(document, 0, "GEHEIM 4711")
                # Die Anzeige-Koordinaten treffen den Text im gerenderten Bild
                with fitz.open(stream=pdf._RAW_BYTES[document["_doc_id"]], filetype="pdf") as d:
                    pix = d[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                self.assertGreater(dark_pixels(pix, box, 2), 20)
                # Rundreise Anzeige -> PDF -> Anzeige
                word = find_words(page, "GEHEIM")[0]
                shown = marks.to_display(page, word["bbox"])
                back = marks.from_display(page, shown)
                for a, b in zip(back, word["bbox"]):
                    self.assertAlmostEqual(a, b, delta=0.05)
                add_area_mark(document, 0, (box.x0 - 2, box.y0 - 2, box.x1 + 2, box.y1 + 2))
                out, result = self.finalize(document)
                self.assertTrue(result.passed, result.residual_text_found)
                self.assertNotIn("GEHEIM", self.page_text(out))
                self.assertIn("OFFEN", self.page_text(out))

    def test_text_mark_on_rotated_page_with_upright_text(self):
        """Seite /Rotate 90 mit Text, der in der Anzeige waagerecht steht
        (im PDF also senkrecht verläuft) - Band quer zur Schreibrichtung."""
        def build(doc):
            page = doc.new_page()
            page.set_rotation(90)
            page.insert_text((100, 700), "Erika Mustermann", fontsize=11, rotate=90)
            page.insert_text((113.2, 700), "Nachbarzeile bleibt", fontsize=11, rotate=90)
        document = build_document(pdf_bytes(build))
        add_text_mark(document, 0, "Erika Mustermann")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        text = self.page_text(out)
        self.assertNotIn("Mustermann", text)
        self.assertIn("Nachbarzeile bleibt", text)

    def test_zoom_levels_render_at_same_coordinates(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        box = display_box(document, 0, "104827")
        for zoom in (0.5, 1.0, 1.5, 2.0, 3.0):
            with self.subTest(zoom=zoom):
                png = pdf.render_page_image(document["_doc_id"], 0, zoom)
                pix = fitz.Pixmap(png)
                self.assertGreater(dark_pixels(pix, box, zoom), 0)
        pdf.forget(document["_doc_id"])

    def test_cropbox_offset(self):
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["oben", "GEHEIM 4711", "OFFEN bleibt"], y=300)
            page.set_cropbox(fitz.Rect(40, 250, 400, 500))
        document = build_document(pdf_bytes(build))
        box = display_box(document, 0, "GEHEIM 4711")
        with fitz.open(stream=pdf._RAW_BYTES[document["_doc_id"]], filetype="pdf") as d:
            pix = d[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        self.assertGreater(dark_pixels(pix, box, 2), 20)
        add_area_mark(document, 0, (box.x0 - 1, box.y0 - 1, box.x1 + 1, box.y1 + 1))
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertNotIn("GEHEIM", self.page_text(out))

    def test_invalid_display_rects_are_rejected(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), ["x"])))
        page = document["pages"][0]
        self.assertIsNone(marks.from_display(page, [10, 10, 10.5, 10.5]))
        self.assertIsNone(marks.from_display(page, [float("nan"), 0, 10, 10]))
        self.assertIsNone(marks.from_display(page, [-50, -50, -10, -10]))
        self.assertIsNone(marks.from_display(page, [1, 2, 3]))
        pdf.forget(document["_doc_id"])


# ---------------------------------------------------------------------------
# Bilder, Scans, Vektorgrafik
# ---------------------------------------------------------------------------

def noise_png(width=200, height=120) -> bytes:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    for i in range(0, width, 7):
        draw.line([(i, 0), (width - i, height)], fill=(i % 255, 60, 200 - i % 150), width=3)
    draw.text((10, 50), "Max Beispiel 1980", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class ImageAndScanTests(RedactionTestCase):
    def test_scanned_page_area_mark_removes_image_pixels(self):
        def build(doc):
            doc.new_page().insert_image(fitz.Rect(50, 50, 450, 290), stream=noise_png())
        document = build_document(pdf_bytes(build))
        add_area_mark(document, 0, (100, 100, 300, 200))
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertTrue(any("keine Textebene" in w for w in result.warnings), result.warnings)

    def test_scanned_page_without_ocr_engine_stays_usable(self):
        def build(doc):
            doc.new_page().insert_image(fitz.Rect(50, 50, 450, 290), stream=noise_png())
        with patch("core.ocr.extract_words", side_effect=RuntimeError("Tesseract-OCR-Engine nicht gefunden. Details")):
            document = build_document(pdf_bytes(build))
        page = document["pages"][0]
        self.assertEqual(page.words, [])
        self.assertTrue(page.notices and "OCR" in page.notices[0], page.notices)
        pdf.forget(document["_doc_id"])

    def test_image_detection_marks_are_areas(self):
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["Foto:"])
            page.insert_image(fitz.Rect(72, 100, 172, 160), stream=noise_png(100, 60))
        document = build_document(pdf_bytes(build), detect=True)
        image_marks = [m for m in document["pages"][0].suggestions if m["source"] == "image_heuristik"]
        self.assertEqual(len(image_marks), 1)
        self.assertEqual(image_marks[0]["kind"], "area")
        image_marks[0]["status"] = marks.CONFIRMED
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)

    def test_black_box_over_image_is_detected(self):
        """Nur ein schwarzer Kasten über einem Bild (keine echte Schwärzung)
        muss als Fehler erkannt werden."""
        path = Path(tempfile.mkdtemp()) / "fake.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(fitz.Rect(50, 50, 450, 290), stream=noise_png())
        page.draw_rect(fitz.Rect(100, 100, 300, 200), color=None, fill=(0, 0, 0))
        doc.save(path)
        doc.close()
        result = redaction.verify(path, [{"page": 0, "rect": [100, 100, 300, 200]}], [])
        self.assertFalse(result.passed)
        self.assertTrue(any("Bilddaten" in p for p in result.residual_text_found), result.residual_text_found)

    def test_black_box_over_text_is_detected(self):
        path = Path(tempfile.mkdtemp()) / "fake.pdf"
        doc = fitz.open()
        page = doc.new_page()
        write_lines(page, ["Name: Erika Mustermann"])
        word = [w for w in page.get_text("words") if w[4] == "Mustermann"][0]
        page.draw_rect(fitz.Rect(word[:4]), color=None, fill=(0, 0, 0))
        doc.save(path)
        doc.close()
        result = redaction.verify(path, [{"page": 0, "rect": list(word[:4])}], ["Mustermann"])
        self.assertFalse(result.passed)
        self.assertTrue(any("Resttext" in p for p in result.residual_text_found))

    @staticmethod
    def draw_signature(page):
        shape = page.new_shape()
        shape.draw_bezier((100, 120), (130, 80), (160, 160), (190, 110))
        shape.draw_bezier((190, 110), (210, 90), (230, 140), (260, 115))
        shape.finish(color=(0, 0, 0.5), width=1.5)
        shape.commit()

    def test_vector_signature_is_removed_completely(self):
        """Gezeichnete Unterschrift (Vektorpfad): auch wenn sie nur teilweise
        im Bereich liegt, darf ihr Pfad nicht unter dem schwarzen Kasten
        weiterbestehen - sie wird vollständig entfernt."""
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["Unterschrift:"])
            self.draw_signature(page)
        # teilweise / vollständig (unterhalb der Beschriftung, die bis y=74,4 reicht)
        for area in ((95, 85, 190, 160), (90, 76, 270, 170)):
            with self.subTest(bereich=area):
                document = build_document(pdf_bytes(build))
                add_area_mark(document, 0, area)
                out, result = self.finalize(document)
                self.assertTrue(result.passed, result.residual_text_found)
                with fitz.open(out) as d:
                    curves = [i for dr in d[0].get_drawings() for i in dr["items"] if i[0] == "c"]
                self.assertEqual(curves, [])
                self.assertIn("Unterschrift:", self.page_text(out))

    def test_black_box_over_vector_signature_is_detected(self):
        path = Path(tempfile.mkdtemp()) / "fake.pdf"
        doc = fitz.open()
        page = doc.new_page()
        self.draw_signature(page)
        page.draw_rect(fitz.Rect(95, 85, 190, 160), color=None, fill=(0, 0, 0))
        doc.save(path)
        doc.close()
        result = redaction.verify(path, [{"page": 0, "rect": [95, 85, 190, 160]}], [])
        self.assertFalse(result.passed)
        self.assertTrue(any("Vektorgrafik" in p for p in result.residual_text_found), result.residual_text_found)


# ---------------------------------------------------------------------------
# Metadaten, Annotationen, Formulare, Links, Anhänge, Ebenen
# ---------------------------------------------------------------------------

class DocumentStructureTests(RedactionTestCase):
    def build_rich(self, doc):
        page = doc.new_page()
        write_lines(page, ["Kontakt: erika.mustermann@example.org", "Akte 2026-17", "Name: Erika Mustermann"])
        email = [w for w in page.get_text("words") if "example.org" in w[4]][0]
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(email[:4]),
                          "uri": "mailto:erika.mustermann@example.org"})
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 400, 200, 415),
                          "uri": "https://example.org/akte?name=Erika%20Mustermann"})
        page.add_freetext_annot(fitz.Rect(72, 200, 300, 230), "Notiz zu Max Beispiel", fontsize=11)
        sticky = page.add_text_annot((400, 72), "Versteckter Kommentar: Max Beispiel")
        sticky.set_info(title="Erika Mustermann")
        widget = fitz.Widget()
        widget.field_name = "kunde"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "Formularwert Beispiel"
        widget.rect = fitz.Rect(72, 300, 300, 320)
        page.add_widget(widget)
        doc.embfile_add("geheim.txt", b"Erika Mustermann, 12.03.1985")
        doc.set_metadata({"author": "Erika Mustermann", "title": "Akte Mustermann"})
        doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'>Erika Mustermann</x:xmpmeta>")
        js = doc.get_new_xref()
        doc.update_object(js, "<< /S /JavaScript /JS (app.alert('Erika')) >>")
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")

    def test_annotations_widgets_links_attachments_metadata_are_cleaned(self):
        document = build_document(pdf_bytes(self.build_rich))
        notices = " ".join(document["notices"])
        self.assertIn("Formularfeld", notices)
        self.assertIn("Dateianhang", notices)
        page = document["pages"][0]
        texts = " ".join(w["text"] for w in page.words)
        # Sichtbarer Kommentar- und Formularinhalt ist jetzt markierbar
        self.assertIn("Beispiel", texts)
        self.assertIn("Formularwert", texts)
        add_text_mark(document, 0, "erika.mustermann@example.org")
        add_text_mark(document, 0, "Erika Mustermann")  # steht URL-kodiert auch in einem Link
        add_text_mark(document, 0, "Max Beispiel")
        add_text_mark(document, 0, "Formularwert Beispiel")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        with fitz.open(out) as d:
            self.assertIsNone(d[0].first_annot)
            self.assertIsNone(d[0].first_widget)
            self.assertEqual(d.embfile_count(), 0)
            self.assertFalse(any((d.metadata or {}).get(k) for k in ("author", "title")))
            self.assertEqual(d.xref_xml_metadata(), 0)
            uris = [link.get("uri", "") for link in d[0].get_links()]
            self.assertFalse(any("mustermann" in u.lower() for u in uris), uris)
            text = d[0].get_text()
        self.assertNotIn("example.org", text)
        self.assertNotIn("Formularwert", text)
        self.assertIn("Akte 2026-17", text)
        for hidden in ("Versteckter Kommentar", "app.alert", "geheim.txt"):
            self.assertFalse(raw_contains(out, hidden), hidden)
        self.assertTrue(all(c["ok"] for c in result.checks), result.checks)

    def test_unapplied_redaction_annotation_from_other_tool_is_applied(self):
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["Vorher geschwärzt: Max Beispiel", "sichtbar"])
            word = [w for w in page.get_text("words") if w[4] == "Beispiel"][0]
            page.add_redact_annot(fitz.Rect(word[:4]), fill=(0, 0, 0))
        document = build_document(pdf_bytes(build))
        self.assertTrue(any("Schwärzungsmarkierung" in n for n in document["notices"]))
        self.assertNotIn("Beispiel", " ".join(w["text"] for w in document["pages"][0].words))
        pdf.forget(document["_doc_id"])

    def test_hidden_layer_becomes_visible_and_redactable(self):
        def build(doc):
            page = doc.new_page()
            hidden = doc.add_ocg("Intern", on=False)
            page.insert_text((72, 100), "Sichtbarer Text", fontsize=11)
            page.insert_text((72, 140), "Ebene: Max Beispiel", fontsize=11, oc=hidden)
        document = build_document(pdf_bytes(build))
        self.assertTrue(any("Ebenen" in n for n in document["notices"]))
        add_text_mark(document, 0, "Max Beispiel")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        with redaction._open_all_layers(out) as d:
            self.assertNotIn("Beispiel", d[0].get_text())

    def test_bookmark_with_marked_text_is_removed(self):
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["Akte Erika Mustermann"])
            doc.set_toc([[1, "Akte Erika Mustermann", 1]])
        document = build_document(pdf_bytes(build))
        add_text_mark(document, 0, "Erika Mustermann")
        out, result = self.finalize(document)
        self.assertTrue(result.passed, result.residual_text_found)
        with fitz.open(out) as d:
            self.assertEqual(d.get_toc(), [])

    def test_other_occurrences_are_reported(self):
        def build(doc):
            write_lines(doc.new_page(), ["Erika Mustermann", "Grüße an Erika Mustermann"])
        document = build_document(pdf_bytes(build))
        page = document["pages"][0]
        words = page.words[:2]
        page.suggestions.append(marks.new_manual(page, kind="text", rects=marks.line_rects(words),
                                                 text="Erika Mustermann", word_ids=[w["id"] for w in words]))
        out, result = self.finalize(document)
        self.assertTrue(result.passed)
        self.assertTrue(any("noch 1× ungeschwärzt" in w for w in result.warnings), result.warnings)

    def test_pseudonymization_still_works(self):
        document = build_document(pdf_bytes(lambda d: write_lines(d.new_page(), LETTER)))
        add_text_mark(document, 0, "Erika Mustermann")
        out, table = pdf.finalize_pseudonymization(document)
        self.addCleanup(lambda: Path(out).unlink(missing_ok=True))
        result = pdf.verify_pseudonymization(out, table)
        self.assertTrue(result.passed, result.residual_text_found)
        self.assertIn("[Markierter Text-1]", self.page_text(out))


# ---------------------------------------------------------------------------
# API: Markierungen bearbeiten, Freigabe, Export-Sperre
# ---------------------------------------------------------------------------

class ApiTests(unittest.TestCase):
    TOKEN = {"X-Redaction-Session": os.environ["REDACTION_SESSION_TOKEN"]}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main
        cls.main = main
        cls.patcher = patch("main.models.start_preload")
        cls.patcher.start()
        cls.client_cm = TestClient(main.app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.patcher.stop()

    def upload(self, data: bytes, mode="manual") -> str:
        res = self.client.post("/documents", headers=self.TOKEN, data={"mode": mode},
                               files={"file": ("test.pdf", data, "application/pdf")})
        self.assertEqual(res.status_code, 200, res.text)
        doc_id = res.json()["document_id"]
        for _ in range(200):
            progress = self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).json()
            if progress["state"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(progress["state"], "done", progress)
        return doc_id

    def letter(self):
        return pdf_bytes(lambda d: write_lines(d.new_page(), LETTER))

    def test_manual_workflow_and_export_lock(self):
        doc_id = self.upload(self.letter())
        page = self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()
        self.assertEqual(page["marks"], [])  # Modus "nur manuell": keine Vorschläge
        words = {w["text"]: w for w in page["words"]}

        # Textauswahl über zwei Wörter
        res = self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN, json={
            "page": 0, "kind": "text", "word_ids": [words["Erika"]["id"], words["Mustermann"]["id"]]})
        self.assertEqual(res.status_code, 200, res.text)
        text_mark = res.json()
        self.assertEqual((text_mark["origin"], text_mark["status"], text_mark["kind"]), ("manual", "confirmed", "text"))

        # Rechteck um die Personalnummer
        b = words["104827"]["bbox"]
        res = self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN, json={
            "page": 0, "kind": "area", "rects": [[b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1]], "label": "Personalnummer"})
        area = res.json()
        self.assertEqual(area["label"], "Personalnummer")

        # Verschieben/Größe ändern, Kategorie ändern, Löschen + Wiederherstellen
        res = self.client.patch(f"/documents/{doc_id}/marks/{area['id']}", headers=self.TOKEN,
                                json={"rects": [[b[0] - 3, b[1] - 2, b[2] + 6, b[3] + 2]]})
        self.assertEqual(res.status_code, 200)
        self.assertAlmostEqual(res.json()["rects"][0][2], b[2] + 6, delta=0.05)
        self.assertEqual(self.client.delete(f"/documents/{doc_id}/marks/{area['id']}", headers=self.TOKEN).status_code, 200)
        self.assertEqual(self.client.post(f"/documents/{doc_id}/marks/{area['id']}/restore", headers=self.TOKEN).status_code, 200)

        # Ungültige Eingaben
        bad = self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                               json={"page": 0, "kind": "area", "rects": [[5, 5, 5.2, 5.2]]})
        self.assertEqual(bad.status_code, 400)
        bad = self.client.patch(f"/documents/{doc_id}/marks/{area['id']}", headers=self.TOKEN, json={"status": "redacted"})
        self.assertEqual(bad.status_code, 400)

        # Ohne Freigabe kein Schwärzen
        self.assertEqual(self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": "redact"}).status_code, 409)
        self.client.post(f"/documents/{doc_id}/review/confirm", headers=self.TOKEN)
        res = self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": "redact"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["verification"]["passed"])

        listing = self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()
        self.assertTrue(listing["result_current"])
        self.assertEqual({m["status"] for m in listing["marks"]}, {"redacted"})
        download = self.client.get(f"/documents/{doc_id}/result/download", headers=self.TOKEN)
        self.assertEqual(download.status_code, 200)
        with fitz.open(stream=download.content, filetype="pdf") as d:
            text = d[0].get_text()
        self.assertNotIn("Mustermann", text)
        self.assertNotIn("104827", text)
        self.assertEqual(self.client.post(f"/documents/{doc_id}/result/verify", headers=self.TOKEN).status_code, 200)

        # Jede Änderung danach macht das Ergebnis ungültig -> kein Export
        self.client.patch(f"/documents/{doc_id}/marks/{text_mark['id']}", headers=self.TOKEN, json={"status": "rejected"})
        self.assertEqual(self.client.get(f"/documents/{doc_id}/result/download", headers=self.TOKEN).status_code, 404)
        self.assertEqual(self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": "redact"}).status_code, 409)

    def test_failed_verification_locks_export(self):
        doc_id = self.upload(self.letter())
        page = self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()
        word = [w for w in page["words"] if w["text"] == "104827"][0]
        self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                         json={"page": 0, "kind": "area", "rects": [word["bbox"]]})
        self.client.post(f"/documents/{doc_id}/review/confirm", headers=self.TOKEN)

        def no_op_apply(doc, marks_by_page, **kwargs):
            # Simuliert eine fehlerhafte Schwärzung: nur schwarzer Kasten
            targets = []
            for page_index, items in marks_by_page.items():
                for m in items:
                    for r in m["rects"]:
                        doc[page_index].draw_rect(fitz.Rect(r), color=None, fill=(0, 0, 0))
                        targets.append({"page": page_index, "rect": r, "mark_id": m["id"], "kind": "area"})
            return targets

        with patch("core.redaction.apply_marks", side_effect=no_op_apply):
            res = self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": "redact"})
        self.assertEqual(res.status_code, 422)
        detail = res.json()["detail"]
        self.assertIn("nicht verifiziert", detail["message"])
        self.assertFalse(detail["verification"]["passed"])
        self.assertTrue(any("Resttext" in p for p in detail["verification"]["problems"]))
        self.assertFalse(Path(tempfile.gettempdir(), f"redacted-{doc_id}.pdf").exists())
        self.assertEqual(self.client.get(f"/documents/{doc_id}/result/download", headers=self.TOKEN).status_code, 404)
        self.assertEqual(self.client.post(f"/documents/{doc_id}/result/save", headers=self.TOKEN).status_code, 404)

    def test_rules_mode_creates_rule_suggestions_without_ai(self):
        doc_id = self.upload(self.letter(), mode="rules")
        listing = self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()
        self.assertTrue(listing["marks"])
        self.assertEqual({m["origin"] for m in listing["marks"]}, {"rule"})
        self.assertEqual({m["status"] for m in listing["marks"]}, {"suggested"})

    def test_rotated_page_marks_in_display_coordinates(self):
        def build(doc):
            page = doc.new_page()
            write_lines(page, ["GEHEIM 4711"])
            page.set_rotation(90)
        doc_id = self.upload(pdf_bytes(build))
        page = self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()
        self.assertEqual((page["width"], page["height"], page["rotation"]), (842, 595, 90))
        word = [w for w in page["words"] if w["text"] == "GEHEIM"][0]
        self.assertGreater(word["bbox"][0], 600)  # rechts in der gedrehten Anzeige
        mark = self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                                json={"page": 0, "kind": "area", "rects": [word["bbox"]]}).json()
        for a, b in zip(mark["rects"][0], word["bbox"]):
            self.assertAlmostEqual(a, b, delta=0.05)


if __name__ == "__main__":
    unittest.main()
