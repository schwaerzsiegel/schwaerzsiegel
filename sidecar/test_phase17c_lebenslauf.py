"""
Phase 17c: Lebenslauf ohne Beschriftungen (Symbole statt „Telefon:“ usw.).

Nachgestellt nach einem Fall aus der Praxis: Straße ohne übliche Endung, Handynummer ohne Trennzeichen,
Geburtsdatum mit Schrägstrichen und Geburtsort in einer eigenen Zeile wurden nicht erkannt.
Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase17c_lebenslauf
"""

import unittest

from core import rules
from test_phase17_erkennung import detect, found

LEBENSLAUF = [
    "Persönliche Infos",
    "Achter de Möhl 7",
    "24999 Musterdorf",
    "Deutschland",
    "01761234567",
    "14/07/1990 in Musterdorf",
    "Fähigkeiten",
    "Fundierte Kenntnisse in MS Office",
]


def texts(suggestions, label):
    return [s["text"] for s in found(suggestions, label)]


class LebenslaufTest(unittest.TestCase):
    def setUp(self):
        self.s = detect(LEBENSLAUF)

    def test_street_without_usual_ending_before_postcode(self):
        self.assertIn("Achter de Möhl 7", texts(self.s, "Adresse"))

    def test_mobile_number_without_separators(self):
        self.assertEqual(texts(self.s, "Telefonnummer"), ["01761234567"])

    def test_birth_date_and_place_in_own_line(self):
        self.assertEqual(texts(self.s, "Geburtsdatum"), ["14/07/1990"])
        self.assertEqual(texts(self.s, "Geburtsort"), ["Musterdorf"])
        for s in found(self.s, "Geburtsdatum") + found(self.s, "Geburtsort"):
            self.assertTrue(s["default_selected"])
            self.assertLess(s["confidence"], 0.85, "ohne Beschriftung nur „zu prüfen“")

    def test_nothing_else_is_proposed(self):
        labels = sorted({s["label"] for s in self.s})
        self.assertEqual(labels, ["Adresse", "Geburtsdatum", "Geburtsort", "Telefonnummer"], self.s)


class SchreibweisenTest(unittest.TestCase):
    def test_slash_and_iso_dates_with_birth_label(self):
        for value in ("14/07/1990", "14-07-1990", "1990-07-14"):
            self.assertEqual(texts(detect(f"Geburtsdatum: {value}"), "Geburtsdatum"), [value])

    def test_recent_date_with_place_is_not_a_birth_date(self):
        s = detect(["Musterweg 1", "15/06/2025 in Kiel"])
        self.assertEqual(found(s, "Geburtsdatum"), [])
        self.assertEqual(found(s, "Geburtsort"), [])

    def test_date_with_place_inside_a_sentence_is_not_a_birth_date(self):
        s = detect("Die Besprechung findet am 14/07/1990 in Kiel statt.")
        self.assertEqual(found(s, "Geburtsdatum"), [])

    def test_slash_fractions_are_no_dates(self):
        for text in ("Seite 1/2", "Anlage 3/4/5", "Az. 12/03/45"):
            self.assertEqual(found(detect(text), "Datum"), [], text)

    def test_mobile_numbers(self):
        for value in ("01761234567", "015112345678", "+4917612345678"):
            self.assertEqual(texts(detect(value), "Telefonnummer"), [value], value)

    def test_long_numbers_with_other_meaning_are_no_phone(self):
        for text in ("Kundennummer 01761234567", "Rechnungsnummer 01701234567", "04311234567"):
            self.assertEqual(found(detect(text), "Telefonnummer"), [], text)

    def test_north_german_street_names(self):
        for value in ("Lütt Moor 2", "Holtenauer Koppel 12a", "Kieler Straße 5", "Große Twiete 3",
                      "Am Redder 4", "Karl-Ferdinand-Str. 3"):
            self.assertIn(value, texts(detect(value), "Adresse"), value)

    def test_street_rule_needs_house_number_and_postcode(self):
        self.assertEqual(found(detect(["Achter de Möhl", "24999 Musterdorf"]), "Adresse")[0]["text"],
                         "24999 Musterdorf")
        self.assertEqual(found(detect("Kapitel Drei 7"), "Adresse"), [])

    def test_company_address_stays_company_address(self):
        s = detect("zwischen der Muster Logistik GmbH, Hafenstieg 9, 24999 Musterdorf, und")
        self.assertEqual(found(s, "Adresse"), [])
        self.assertTrue(found(s, "Firmenanschrift"))
        self.assertFalse(any(x["default_selected"] for x in found(s, "Firmenanschrift")))

    def test_year_helper(self):
        self.assertEqual(rules._year("14/07/1990"), 1990)
        self.assertEqual(rules._year("3. Mai 1979"), 1979)
        self.assertEqual(rules._year("12.05.80"), 1980)


if __name__ == "__main__":
    unittest.main()
