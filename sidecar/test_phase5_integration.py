"""
Tests Phase 5: Befunde aus dem Integrationstest der installierten App.
Nur synthetische Daten, kein KI-Modell nötig (GLiNER-Treffer simuliert).

    python -m unittest -v test_phase5_integration
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "a" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
sys.path.insert(0, str(Path(__file__).parent))

import fitz  # noqa: E402

from core import ner  # noqa: E402

TEXT = "Vorgang 4711-2026 Der Antrag wurde geprüft und die Unterlagen sind vollständig."


def words(text):
    return [{"id": f"p0_w{i}", "text": t, "bbox": [72 + 40 * i, 72, 72 + 40 * i + 35, 84], "line": "0-0"}
            for i, t in enumerate(text.split())]


def fake_hits(text, index):
    """Wie im gemessenen Fall: GLiNER hält gewöhnliche Wörter für Aktenzeichen."""
    hits = []
    for needle, label in (("Antrag", "Aktenzeichen"), ("Unterlagen", "Aktenzeichen"),
                          ("4711-2026", "Aktenzeichen"), ("geprüft", "KFZ-Kennzeichen")):
        start = text.index(needle)
        hits.append({"start": start, "end": start + len(needle), "text": needle, "label": label, "score": 0.6})
    return hits


class GlinerPlausibilityTests(unittest.TestCase):
    def test_case_numbers_without_digits_are_dropped(self):
        with patch("core.ner._model_hits", side_effect=fake_hits):
            found = ner.detect(words(TEXT), use_llm=False, use_gliner=True)
        texts = {(m["label"], m["text"]) for m in found}
        self.assertIn(("Aktenzeichen", "4711-2026"), texts)
        self.assertFalse({t for t in texts if t[1] in ("Antrag", "Unterlagen", "geprüft")}, texts)


def form_words(rows):
    """Formularzeilen wie im gemeldeten PDF: Beschriftung (x=77) und Wert
    (x=216) auf gleicher Höhe, aber als getrennte Zeilen der Textebene."""
    words = []
    for r, (label, value) in enumerate(rows):
        y = 100 + 27 * r
        for col, (text, x) in enumerate(((label, 77), (value, 216))):
            for t in text.split():
                words.append({"id": f"p0_w{len(words)}", "text": t, "bbox": [x, y, x + 6 * len(t), y + 11],
                              "line": f"{r}-{col}"})
                x += 6 * len(t) + 4
    return words


class FormLayoutDetectionTests(unittest.TestCase):
    """Phase 5 (Befund mit einem Formular-PDF des Nutzers): IBAN, Steuer-ID
    und Sozialversicherungsnummer wurden nicht vorgeschlagen."""

    def found(self, rows):
        return {m["label"]: m for m in ner.detect(form_words(rows), use_llm=False, use_gliner=False)}

    def test_labels_and_values_in_separate_text_lines(self):
        found = self.found([("IBAN (Testwert)", "DE12 3456 7890 1234 5678 90"),   # Prüfziffer ungültig
                            ("BIC (Testwert)", "TESTDEFFXXX"),
                            ("Steuer-ID (Platzhalter)", "12 345 678 901"),         # Prüfziffer ungültig
                            ("Sozialversicherungsnr.", "TEST-SV-123456 (Platzhalter)"),
                            ("Personalnummer", "PN-48213"),
                            ("Geburtsdatum", "12.03.1985")])
        self.assertEqual(found["IBAN"]["text"], "DE12 3456 7890 1234 5678 90")
        self.assertIn("Prüfziffer ungültig", found["IBAN"]["reason"])
        self.assertEqual(found["Steuer-ID"]["text"], "12 345 678 901")
        self.assertEqual(found["Sozialversicherungsnummer"]["text"], "TEST-SV-123456")
        self.assertIn("Format unbekannt", found["Sozialversicherungsnummer"]["reason"])
        self.assertIn("Personalnummer", found)
        self.assertIn("Geburtsdatum", found)
        # nicht als "sicher" eingestuft -> keine Sammelbestätigung ohne Prüfung
        for label in ("IBAN", "Steuer-ID", "Sozialversicherungsnummer"):
            self.assertLess(found[label]["confidence"], 0.85, label)

    def test_valid_iban_with_label_stays_sure(self):
        found = self.found([("IBAN", "DE89 3704 0044 0532 0130 00 BIC")])
        self.assertEqual(found["IBAN"]["text"], "DE89 3704 0044 0532 0130 00")
        self.assertGreaterEqual(found["IBAN"]["confidence"], 0.85)

    def test_text_in_two_columns_is_not_joined(self):
        # Fließtext in zwei Spalten: das nächste Wort steht tiefer -> neue Zeile
        words = form_words([("IBAN", "")]) + [
            {"id": "p0_w9", "text": "DE89", "bbox": [77, 140, 101, 151], "line": "9-0"}]
        text, _ = ner._build_text_and_index(words)
        self.assertEqual(text, "IBAN\nDE89")

    def test_address_ends_after_postcode_and_city(self):
        words = [{"id": f"p0_w{i}", "text": t, "bbox": [72 + 40 * i, 100, 108 + 40 * i, 111], "line": "0-0"}
                 for i, t in enumerate("Anschrift: Musterweg 12, 12345 Musterstadt. Bitte Rückmeldung bis Freitag.".split())]
        found = {m["label"]: m for m in ner.detect(words, use_llm=False, use_gliner=False)}
        self.assertEqual(found["Adresse"]["text"], "Musterweg 12, 12345 Musterstadt.")


JS = "<< /S /JavaScript /JS (app.alert\\(1\\)) >>"


def _new_obj(doc, source):
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


def _names_inline(doc):
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<< /JavaScript << /Names [(a) {_new_obj(doc, JS)} 0 R] >> >>")


def _names_tree_object(doc):
    tree = _new_obj(doc, f"<< /Names [(a) {_new_obj(doc, JS)} 0 R] >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<< /JavaScript {tree} 0 R >>")


def _open_action_inline(doc):
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", JS)


def _open_action_object(doc):
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{_new_obj(doc, JS)} 0 R")


def _page_action(doc):
    doc.xref_set_key(doc[0].xref, "AA", f"<< /O {JS} >>")


def _launch_link(doc):
    doc[0].insert_link({"kind": fitz.LINK_LAUNCH, "from": fitz.Rect(300, 500, 400, 520), "file": "calc.exe"})


def _form_script(doc):
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(300, 300, 400, 320)
    widget.field_name = "feld"
    widget.script_calc = "event.value = 1;"
    doc[0].add_widget(widget)


def _attachment(doc):
    doc.embfile_add("anlage.txt", b"Bearbeitungsverlauf Erika Mustermann")


def _c2pa_associated_file(doc):
    """Wie ein Dokument mit "Content Credentials": Manifest als Anhang UND als
    zugeordnete Datei (/AF) im Katalog - nur die Anhangsliste wurde geleert."""
    doc.embfile_add("manifest.c2pa", b"Erika Mustermann - Bearbeitungsverlauf")
    cat = doc.pdf_catalog()
    tree = doc.xref_get_key(cat, "Names/EmbeddedFiles/Names")[1]
    spec = int(re.search(r"(\d+) 0 R", tree).group(1))
    doc.xref_set_key(spec, "AFRelationship", "/C2PA_Manifest")
    doc.xref_set_key(cat, "AF", f"[{spec} 0 R]")


class ActiveContentCleanupTests(unittest.TestCase):
    """Phase 5 (Befund im Test mit eigenem Dokument): JavaScript/Launch an
    verschiedenen Stellen. Bisher wurde ein Objekt mit eingebetteter Aktion
    ganz geleert (Katalog/Seite/Link zerstört) und Dokument-JavaScript im
    Namensbaum nicht entfernt -> Export gesperrt."""

    def test_active_content_is_removed_without_damaging_the_document(self):
        from core import pdf_processor as pdf
        from test_phase1_redaction import LETTER, add_area_mark, build_document, raw_contains, write_lines
        cases = (_names_inline, _names_tree_object, _open_action_inline, _open_action_object, _page_action,
                 _launch_link, _form_script, _attachment, _c2pa_associated_file)
        for case in cases:
            with self.subTest(case.__name__):
                doc = fitz.open()
                write_lines(doc.new_page(), LETTER)
                case(doc)
                data = doc.tobytes()
                doc.close()
                document = build_document(data)
                mark = add_area_mark(document, 0, (60, 60, 200, 80))
                mark["status"] = "confirmed"
                out, targets = pdf.finalize_redaction(document)
                try:
                    result = pdf.verify_redaction(out, document, targets)
                    self.assertTrue(result.passed, result.residual_text_found)
                    with fitz.open(out) as res:
                        self.assertEqual(res.page_count, 1)
                        self.assertIn("Ende des Dokuments", res[0].get_text())
                    # "Bearbeitungsverlauf" steht nur in der eingebetteten Datei
                    for needle in ("app.alert", "calc.exe", "/JavaScript", "/Launch", "Bearbeitungsverlauf"):
                        self.assertFalse(raw_contains(out, needle), needle)
                finally:
                    Path(out).unlink(missing_ok=True)
                    pdf.forget(document["_doc_id"])


if __name__ == "__main__":
    unittest.main()
