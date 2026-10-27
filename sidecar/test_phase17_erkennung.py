"""
Phase 17: Datumsangaben, Organisationen, Organisationszeichen (Auftrag vom 27.10.2026, Abschnitt 7).

Ohne KI-Modelle lauffähig (auch im Cloud-Build): Treffer des KI-Modells GLiNER werden, wo nötig,
nachgestellt (ner._model_hits). Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase17_erkennung
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "b" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))

import fitz  # noqa: E402

from core import marks, ner, rules, vorschlagsregeln  # noqa: E402
from core import pdf_processor as pdf  # noqa: E402


def words_of(lines: list[str]) -> list[dict]:
    """Zeilen wie in einem PDF: jede Zeile eine Textzeile mit Wort-Koordinaten."""
    doc = fitz.open()
    page = doc.new_page()
    y = 80
    for line in lines:
        page.insert_text((50, y), line, fontsize=10, fontname="helv")
        y += 16
    data = doc.tobytes()
    doc.close()
    with fitz.open(stream=data, filetype="pdf") as d:
        return pdf.extract_page(d[0], 0, detect_images=False).words


def detect(lines, gliner_hits=None):
    """Erkennung ohne KI-Modell; gliner_hits = [(Kategorie, Text, Sicherheit)] stellt GLiNER nach."""
    words = words_of(lines if isinstance(lines, list) else [lines])
    if gliner_hits is None:
        return ner.detect(words, use_llm=False, use_gliner=False)
    text, _ = ner._build_text_and_index(words)

    def fake(_text, _index):
        out = []
        for label, value, score in gliner_hits:
            start = text.index(value)
            out.append({"label": label, "text": value, "start": start, "end": start + len(value), "score": score})
        return out
    with patch("core.ner._model_hits", fake):
        return ner.detect(words, use_llm=False, use_gliner=True)


def found(suggestions, label):
    return [s for s in suggestions if s["label"] == label]


class OrganisationszeichenTest(unittest.TestCase):
    POSITIV = [
        ("Org.-Z. 1", "1"), ("Org.-Z. 1.1", "1.1"), ("Org.-Z. 131", "131"), ("Org.-Z. 131.01", "131.01"),
        ("Org.Z. 1211.01a", "1211.01a"), ("Org.-Zeichen: 311.345c", "311.345c"),
        ("Organisationskennzeichen: 12b", "12b"), ("Org.Z.: 131.01", "131.01"),
        ("Org.-Zeichen 1211", "1211"), ("Organisationskennzeichen: 311.345", "311.345"),
        ("Org.-Z. 012.007", "012.007"),             # führende Nullen
        ("org.-z. 131", "131"), ("ORG.-Z. 131", "131"), ("Org.-zeichen 13", "13"),  # Groß-/Kleinschreibung
        ("Org. -Z. 131", "131"), ("Org.Z.:131.01", "131.01"), ("Org.-Z.  :  31", "31"),  # Leerzeichen
        ("Org.-Z. 1a", "1a"), ("Org.-Z. 12b", "12b"), ("Org.-Z. 131c", "131c"), ("Org.-Z. 1211.01b", "1211.01b"),
    ]

    def test_positive_cases_label_and_identifier_together_preselected(self):
        for line, ident in self.POSITIV:
            with self.subTest(line):
                hits = found(detect(line), "Organisationszeichen")
                self.assertEqual(len(hits), 1, hits)
                h = hits[0]
                self.assertTrue(h["text"].endswith(ident), h["text"])
                self.assertTrue(h["text"].lower().startswith(("org", "organisationskennzeichen")), h["text"])
                self.assertTrue(h["default_selected"])
                self.assertGreaterEqual(h["confidence"], 0.85)  # in der Sammelbestätigung

    def test_identifier_is_complete_at_sentence_end_and_before_next_word(self):
        h = found(detect("Bitte nennen Sie das Org.-Z. 131.01a."), "Organisationszeichen")[0]
        self.assertEqual(h["text"], "Org.-Z. 131.01a.")  # Wort samt Satzpunkt, Kennung vollständig
        h = found(detect("Org.-Z. 131 Betreff: Antrag"), "Organisationszeichen")[0]
        self.assertEqual(h["text"], "Org.-Z. 131", "Nachbarwörter dürfen nicht mit erfasst werden")

    def test_line_break_between_label_and_identifier_gives_one_hit_with_two_boxes(self):
        sug = detect(["Absender: Amt Muster Org.-Z.", "131.01a"])
        hits = found(sug, "Organisationszeichen")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["text"], "Org.-Z. 131.01a")
        self.assertEqual(len(hits[0]["word_ids"]), 2, "nur Bezeichnung und Kennung, nichts dazwischen")

    def test_invalid_or_missing_identifiers_are_not_recognised(self):
        for line in ["Org.-Z. 131.01d", "Org.-Z. 131.01.5", "Org.-Z. 12.05.2025", "Org.-Z. 131A", "Org.-Z. abc",
                     "Org.-Z.", "Org.-Z.:", "Org.-Z. -", "Org.-Z. 131x"]:
            with self.subTest(line):
                self.assertEqual(found(detect(line), "Organisationszeichen"), [])
        self.assertEqual(found(detect(["Org.-Z.:", "Betreff 131"]), "Organisationszeichen"), [],
                         "Kennung nur unmittelbar nach der Bezeichnung")

    def test_numbers_and_dates_without_label_are_not_recognised(self):
        for line in ["131.01", "311.345c", "Ziffer 131.01 der Gebührenordnung", "Artikel 311.345c",
                     "am 12.05.2025", "Raum 1211.01a", "Zeichen 131"]:
            with self.subTest(line):
                self.assertEqual(found(detect(line), "Organisationszeichen"), [])

    def test_label_must_not_be_part_of_a_longer_word(self):
        self.assertEqual(found(detect("XOrg.-Z. 131"), "Organisationszeichen"), [])

    def test_overlapping_ai_hit_does_not_change_category(self):
        sug = detect("Org.-Z. 131.01", gliner_hits=[("Aktenzeichen", "131.01", 0.7)])
        self.assertEqual([s["label"] for s in sug], ["Organisationszeichen"])
        self.assertEqual(sug[0]["text"], "Org.-Z. 131.01")
        self.assertIn("Aktenzeichen", [a["label"] for a in sug[0]["also_detected_as"]])

    def test_pdf_redaction_covers_exactly_label_and_identifier(self):
        """Koordinaten im PDF: Bezeichnung und Kennung verschwinden, Nachbartext bleibt."""
        from test_phase1_redaction import build_document
        data = fitz.open()
        page = data.new_page()
        page.insert_text((50, 80), "Absender: Amt Muster Org.-Z.", fontsize=11, fontname="helv")
        page.insert_text((50, 96), "131.01a Betreff: Antrag", fontsize=11, fontname="helv")
        pdf_bytes = data.tobytes(); data.close()
        document = build_document(pdf_bytes, detect=True)
        self.addCleanup(lambda: pdf.forget(document["_doc_id"]))
        hits = [s for s in document["pages"][0].suggestions if s["label"] == "Organisationszeichen"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(hits[0]["rects"]), 2, "eine Box je Zeile")
        hits[0]["status"] = marks.CONFIRMED
        out, targets = pdf.finalize_redaction(document)
        self.addCleanup(lambda: Path(out).unlink(missing_ok=True))
        self.assertTrue(pdf.verify_redaction(out, document, targets).passed)
        with fitz.open(out) as doc:
            text = doc[0].get_text()
        self.assertNotIn("Org.-Z.", text)
        self.assertNotIn("131.01a", text)
        for kept in ("Absender:", "Amt", "Muster", "Betreff:", "Antrag"):
            self.assertIn(kept, text)


class DatumTest(unittest.TestCase):
    def test_birth_and_death_dates_are_proposed(self):
        for line, label in [("Geburtsdatum: 12.05.1980", "Geburtsdatum"), ("geboren am 12.05.1980", "Geburtsdatum"),
                            ("Herr Seidel wurde am 12.05.1980 in Kiel geboren.", "Geburtsdatum"),
                            ("geb. 12.05.1980", "Geburtsdatum"),
                            ("Herr Becker ist am 02.02.2024 verstorben.", "Sterbedatum"),
                            ("verstorben am 02.02.2024", "Sterbedatum"), ("Sterbedatum: 17.08.2023", "Sterbedatum"),
                            ("Todestag: 17. August 2023", "Sterbedatum")]:
            with self.subTest(line):
                hits = found(detect(line), label)
                self.assertEqual(len(hits), 1, detect(line))
                self.assertTrue(hits[0]["default_selected"])

    def test_dagger_before_date(self):
        # Direkt am Text: die Testschrift des PDF-Generators kann „†“ nicht darstellen
        hits = [h for h in rules.context_hits("Erika Musterfrau † 17.08.2023") if h.label == "Sterbedatum"]
        self.assertEqual(len(hits), 1)

    def test_general_dates_are_not_proposed(self):
        for line in ["Dokument erstellt am 12.05.2025", "Besprechung am 15.06.2025", "Frist bis zum 31.12.2025",
                     "Kiel, 03.02.2026", "Eingang: 07.01.2026", "Bearbeitet am 1. März 2026"]:
            with self.subTest(line):
                self.assertEqual(detect(line), [], "Formatregel allein darf keinen Vorschlag auslösen")
                self.assertEqual(detect(line, gliner_hits=[("Datum", line.split()[-1], 0.6)]), [])

    def test_ai_birthdate_without_birth_hint_is_not_proposed(self):
        self.assertEqual(detect("Besprechung am 15.06.2025", gliner_hits=[("Geburtsdatum", "15.06.2025", 0.55)]), [])

    def test_ai_birthdate_with_birth_hint_is_kept(self):
        sug = detect("Die Antragstellerin, geb. am 15.06.1990, wohnt in Kiel.",
                     gliner_hits=[("Geburtsdatum", "15.06.1990", 0.55)])
        self.assertEqual([s["label"] for s in sug if "15.06.1990" in s["text"]], ["Geburtsdatum"])

    def test_date_in_sentence_with_person_stays_possible_but_not_preselected(self):
        sug = detect("Frau Albrecht, 04.11.1971, beantragt Elternzeit.")
        dates = found(sug, "Datum")
        self.assertEqual(len(dates), 1)
        self.assertFalse(dates[0]["default_selected"])
        self.assertIn("Personenbezug möglich", dates[0]["reason"])
        self.assertTrue(found(sug, "Person"), "der Name selbst bleibt vorgeschlagen")

    def test_person_in_other_sentence_does_not_keep_general_date(self):
        self.assertEqual(found(detect("Sehr geehrte Frau Albrecht. Die Frist endet am 31.12.2025."), "Datum"), [])


class OrganisationTest(unittest.TestCase):
    def test_organisations_without_personal_reference_are_not_proposed(self):
        for name in ["Siemens AG", "Deutsche Bahn AG", "Stadtverwaltung Kiel", "Bundesagentur für Arbeit",
                     "Muster GmbH", "Müller GmbH", "Dr. Oetker KG"]:
            with self.subTest(name):
                self.assertEqual(detect(name, gliner_hits=[("Organisation", name, 0.9)]), [])

    def test_sole_trader_and_practice_with_full_name_are_proposed(self):
        sug = detect("Malermeister Jonas Becker e.K.")
        self.assertEqual([s["text"] for s in found(sug, "Person")], ["Jonas Becker"])
        sug = detect("Praxis Dr. Erika Mustermann")
        self.assertEqual([s["text"] for s in found(sug, "Person")], ["Dr. Erika Mustermann"])
        # Nur als Organisation erkannt, aber mit Kennzeichen eines Einzelunternehmens
        sug = detect("Inh. Erika Musterfrau", gliner_hits=[("Organisation", "Inh. Erika Musterfrau", 0.7)])
        self.assertTrue(any(s["default_selected"] for s in sug))

    def test_person_and_organisation_on_same_words_keeps_the_person(self):
        sug = detect("Becker Haustechnik", gliner_hits=[("Organisation", "Becker Haustechnik", 0.9),
                                                        ("Person", "Becker", 0.6)])
        self.assertEqual([(s["label"], s["text"]) for s in sug], [("Person", "Becker Haustechnik")])
        self.assertTrue(sug[0]["default_selected"])

    def test_person_rules_unchanged(self):
        sug = detect(["Name: Erika Mustermann", "Sehr geehrter Herr Becker,"])
        self.assertEqual(sorted(s["text"] for s in found(sug, "Person")), ["Becker,", "Erika Mustermann"])

    def test_llm_verdict_private_person_keeps_organisation(self):
        s = {"label": "Organisation", "text": "Blumen Petra", "confidence": 0.8, "default_selected": True,
             "llm_reviewed": True, "also_detected_as": [], "source": "gliner"}
        self.assertEqual(vorschlagsregeln.nach_der_ki([s]), [s])
        s2 = dict(s, default_selected=False, confidence=0.15)
        self.assertEqual(vorschlagsregeln.nach_der_ki([s2]), [])


class KonfigurationTest(unittest.TestCase):
    def test_central_table_matches_the_order(self):
        r = vorschlagsregeln.regel
        self.assertEqual(r("Person"), vorschlagsregeln.VORSCHLAGEN)
        self.assertEqual(r("Geburtsdatum"), vorschlagsregeln.VORSCHLAGEN)
        self.assertEqual(r("Sterbedatum"), vorschlagsregeln.VORSCHLAGEN)
        self.assertEqual(r("Organisationszeichen"), vorschlagsregeln.VORSCHLAGEN)
        self.assertEqual(r("Datum"), vorschlagsregeln.NUR_MIT_PERSONENBEZUG)
        self.assertEqual(r("Organisation"), vorschlagsregeln.NUR_MIT_PERSONENBEZUG)
        for other in ("Adresse", "Telefonnummer", "E-Mail", "IBAN"):
            self.assertEqual(r(other), vorschlagsregeln.VORSCHLAGEN)

    def test_changing_the_table_changes_behaviour(self):
        with patch.dict(vorschlagsregeln.VORSCHLAG, {"Organisationszeichen": vorschlagsregeln.NICHT_VORSCHLAGEN}):
            self.assertEqual(found(detect("Org.-Z. 131"), "Organisationszeichen"), [])

    def test_other_detections_unaffected(self):
        sug = detect(["E-Mail: erika.musterfrau@example.org", "Telefon: 0431 1234567",
                      "Anschrift: Musterweg 12, 24103 Kiel"])
        self.assertEqual(sorted(s["label"] for s in sug), ["Adresse", "E-Mail", "Telefonnummer"])
        self.assertEqual(rules.DEFAULT_OFF_LABELS & {"Organisationszeichen", "Sterbedatum"}, set())


if __name__ == "__main__":
    unittest.main()
