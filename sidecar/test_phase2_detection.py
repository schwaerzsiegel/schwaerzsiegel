"""
Regressionstests Phase 2: Erkennungsqualität (nur synthetische Daten).
Laufen ohne KI-Modell (Regeln); die Qualitätsschwelle mit GLiNER prüft
eval/evaluate.py --mode fast (dauert ca. 1 Minute).

    python -m unittest -v test_phase2_detection
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "eval"))

from core import ner, rules, validators as v  # noqa: E402


def detect(*lines: str) -> list[dict]:
    """Wörter wie aus einem PDF: eine Zeile pro Eintrag."""
    words = []
    for li, line in enumerate(lines):
        x = 50.0
        for wi, token in enumerate(line.split()):
            words.append({"id": f"p0_w{len(words)}", "text": token, "bbox": [x, 60 + 14 * li, x + 6 * len(token), 70 + 14 * li],
                          "line": f"0-{li}"})
            x += 6 * len(token) + 4
    return ner.detect(words, use_llm=False, use_gliner=False)


def labels_for(results: list[dict], text: str) -> set[str]:
    return {r["label"] for r in results if text in r["text"]}


class ContextBeatsFormatTests(unittest.TestCase):
    def test_labelled_personnel_number_is_not_a_phone_number(self):
        """Beispiel aus dem Auftrag."""
        for line in ("Personalnummer: 104827", "Pers.-Nr. 104827", "Pers.-Nr.: 104827", "MA-Nr.: 104827",
                     "Mitarbeiter-ID: 104827", "Personal-Nr.: 0104827", "Personalnummer 0301 4711",
                     "Ihre Personalnummer für die Abrechnung: 104827"):
            with self.subTest(line=line):
                results = detect(line)
                value = line.split()[-1]
                self.assertEqual(labels_for(results, value), {"Personalnummer"}, results)
                self.assertTrue(all(r["confidence"] >= 0.9 for r in results))

    def test_unlabelled_digit_sequences_are_not_classified(self):
        for line in ("Bestellnummer: 0815 4711 23", "Vorgang 0123 4567 8", "Referenz 104827", "Code: 04711 2026",
                     "Kostenstelle 0123 4567", "Seite 1 von 2 · Version 2.0.1", "Artikel-Nr. 0123 456789"):
            with self.subTest(line=line):
                results = [r for r in detect(line) if r["label"] not in rules.DEFAULT_OFF_LABELS]
                self.assertEqual(results, [])

    def test_phone_numbers_with_and_without_label(self):
        for line, number in (("Telefon: 030 12345678", "030 12345678"), ("Tel. privat 0171 2345678", "0171 2345678"),
                             ("Meine Handynummer bleibt 0160 1234567", "0160 1234567"),
                             ("Bitte rufen Sie an: +49 30 1234567", "+49 30 1234567"),
                             ("Rückfragen bitte unter (089) 123 4567.", "(089) 123 4567")):
            with self.subTest(line=line):
                self.assertIn("Telefonnummer", labels_for(detect(line), number.split()[-1]))

    def test_unlabelled_phone_is_uncertain(self):
        results = detect("Sie erreichen uns: 030 12345678")
        phone = [r for r in results if r["label"] == "Telefonnummer"]
        self.assertTrue(phone)
        results = detect("030 12345678")
        self.assertTrue(all(r["confidence"] < 0.75 for r in results if r["label"] == "Telefonnummer"))

    def test_birth_date_by_context_plain_date_not_proposed(self):
        # Phase 17 (Auftrag vom 27.10.2026): ein allgemeines Datum wird gar nicht mehr vorgeschlagen,
        # vorher stand es abgewählt in der Liste.
        results = detect("Geburtsdatum: 12.03.1985", "Brief vom 17.10.2026")
        birth = [r for r in results if "12.03.1985" in r["text"]][0]
        self.assertEqual(birth["label"], "Geburtsdatum")
        self.assertTrue(birth["default_selected"])
        self.assertEqual([r for r in results if "17.10.2026" in r["text"]], [])

    def test_birth_place_after_date(self):
        results = detect("geboren am 3. Mai 1979 in Bad Musterberg")
        self.assertIn("Geburtsort", labels_for(results, "Musterberg"))
        self.assertIn("Geburtsdatum", labels_for(results, "1979"))

    def test_field_labels_are_never_values(self):
        results = detect("Telefon:", "Personalakte", "E-Mail:", "Name:", "IBAN:")
        self.assertEqual(results, [])

    def test_names_by_context(self):
        for line, name in (("Name: Erika Mustermann", "Erika Mustermann"), ("Sehr geehrte Frau Musterfrau,", "Musterfrau"),
                           ("Kontoinhaber: Max Beispiel", "Max Beispiel"), ("Herr Dr. Jonas Becker stellte sich vor", "Becker")):
            with self.subTest(line=line):
                hits = [r for r in detect(line) if r["label"] == "Person"]
                self.assertTrue(hits and name in hits[0]["text"], hits)
                self.assertNotIn("Name", hits[0]["text"].split())

    def test_address_parts_and_company_address(self):
        results = detect("Anschrift: Musterweg 12a, 12345 Beispielstadt", "Muster GmbH · Industriestraße 5 · 99999 Firmenstadt")
        self.assertIn("Adresse", labels_for(results, "Musterweg"))
        self.assertIn("Adresse", labels_for(results, "Beispielstadt"))
        company = [r for r in results if "Industriestraße" in r["text"]]
        self.assertTrue(company and company[0]["label"] == "Firmenanschrift" and not company[0]["default_selected"])

    def test_health_data(self):
        results = detect("Diagnose: Depressive Episode (F32.1)", "Arbeitsunfähig wegen Lumbago")
        self.assertIn("Gesundheitsdaten", labels_for(results, "Depressive"))
        self.assertIn("Gesundheitsdaten", labels_for(results, "Lumbago"))

    def test_email_wins_over_other_labels(self):
        results = detect("Kontakt: erika.mustermann@example.org")
        self.assertEqual(labels_for(results, "example.org"), {"E-Mail"})

    def test_reason_is_explained(self):
        result = detect("Personalnummer: 104827")[0]
        self.assertIn("Personalnummer", result["reason"])
        # Phase 5: beschriftete IBAN -> Kontextregel, Prüfziffer steht in der Begründung
        self.assertIn("Prüfziffer gültig", detect("IBAN DE89 3704 0044 0532 0130 00")[0]["reason"])
        self.assertIn("Prüfziffer gültig", detect("Kontakt DE89 3704 0044 0532 0130 00")[0]["reason"])


class ConflictResolutionTests(unittest.TestCase):
    def s(self, label, ids, priority, confidence, source="regex"):
        return {"id": label, "label": label, "text": label, "bbox": [0, 0, 1, 1], "word_ids": ids, "confidence": confidence,
                "source": source, "reason": "", "default_selected": True, "_priority": priority, "_span": (0, 1)}

    def test_priority_order_and_chains(self):
        merged = ner.resolve_conflicts([
            self.s("Telefonnummer", ["a", "b"], rules.PRIO_FORMAT, 0.7),
            self.s("Adresse", ["b", "c"], rules.PRIO_MODEL, 0.99, "gliner"),
            self.s("Personalnummer", ["c"], rules.PRIO_CONTEXT, 0.95),
            self.s("E-Mail", ["x"], rules.PRIO_STRUCTURE, 0.97),
        ])
        self.assertEqual(len(merged), 2)
        main = [m for m in merged if "c" in m["word_ids"]][0]
        self.assertEqual(main["label"], "Personalnummer")
        self.assertEqual(main["word_ids"], ["a", "b", "c"])
        self.assertEqual({a["label"] for a in main["also_detected_as"]}, {"Telefonnummer", "Adresse"})


class ValidatorTests(unittest.TestCase):
    def test_check_digits(self):
        self.assertTrue(v.iban("DE89 3704 0044 0532 0130 00"))
        self.assertFalse(v.iban("DE89 3704 0044 0532 0130 01"))
        self.assertFalse(v.iban("DE89 3704 0044 0532 0130"))  # zu kurz für DE
        self.assertTrue(v.steuer_id("86095742719"))
        self.assertFalse(v.steuer_id("86095742718"))
        self.assertFalse(v.steuer_id("11111111111"))
        self.assertTrue(v.sv_nummer("65 170839 J 003"))
        self.assertFalse(v.sv_nummer("65 170839 J 004"))
        self.assertTrue(v.kv_nummer("A123456780"))
        self.assertFalse(v.kv_nummer("A123456789"))
        self.assertTrue(v.ausweis("L01X00T471"))
        self.assertFalse(v.ausweis("L01X00T472"))
        self.assertFalse(v.phone("12.03.1985"))

    def test_generator_and_validators_agree(self):
        """Generator (eval/corpus.py) und Validatoren sind unabhängig
        implementiert und müssen sich gegenseitig bestätigen."""
        import random
        import corpus
        rng = random.Random(1)
        for _ in range(200):
            p = corpus.make_person(rng)
            self.assertTrue(v.iban(p.iban), p.iban)
            self.assertTrue(v.steuer_id(p.steuer_id), p.steuer_id)
            self.assertTrue(v.sv_nummer(p.sv), p.sv)
            self.assertTrue(v.kv_nummer(p.kvnr), p.kvnr)
            self.assertTrue(v.ausweis(p.ausweis), p.ausweis)


class QualityGateTests(unittest.TestCase):
    """Bestehende Kategorien dürfen sich nicht unbemerkt verschlechtern.
    Schwellen = gemessener Stand nach Phase 2 (nur Regeln) mit Puffer."""

    def run_eval(self, variants):
        import evaluate
        return evaluate.evaluate("rules", 4711, 5, variants)

    def test_rules_quality_on_both_corpora(self):
        for variants, min_f1 in ((False, 0.95), (True, 0.9)):
            with self.subTest(varianten=variants):
                result = self.run_eval(variants)
                self.assertGreaterEqual(result["micro"]["f1"], min_f1, result["micro"])
                self.assertEqual(result["confusion"], {})
                for cat in ("telefon", "personalnummer", "iban", "steuer_id", "sv_nummer", "email", "geburtsdatum"):
                    m = result["per_category"][cat]
                    if m["tp"] + m["fn"]:
                        self.assertGreaterEqual(m["f1"], 0.95, (cat, m))


if __name__ == "__main__":
    unittest.main()
