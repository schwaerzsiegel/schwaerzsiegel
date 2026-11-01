"""
Phase 21: Geburtsdaten in einer Tabellenspalte, „Rangplatz 2“ keine Anschrift, Kürzel in Großbuchstaben
keine Person, Geburtsdatum nach „geb.“ in der nächsten Zeile.

Ohne KI-Modelle lauffähig. Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase21_kleinigkeiten
"""

import unittest

import fitz

from core import ner, rules
from core import pdf_processor as pdf
from test_phase17_erkennung import detect, found


def seite(zellen):
    """Seite mit Text an festen Positionen: [(x, y, text), …]."""
    doc = fitz.open()
    page = doc.new_page()
    for x, y, text in zellen:
        page.insert_text((x, y), text, fontsize=9, fontname="helv")
    data = doc.tobytes()
    doc.close()
    with fitz.open(stream=data, filetype="pdf") as d:
        pg = pdf.extract_page(d[0], 0, detect_images=False)
    return ner.detect(pg.words, use_llm=False, use_gliner=False)


TABELLE = [
    (60, 100, "Nr."), (90, 100, "Name, Vorname"), (180, 100, "Geb.-Datum"), (260, 100, "Anschrift"),
    (60, 125, "B1"), (90, 125, "Beispiel, Jana"), (180, 125, "14.03.1987"), (260, 125, "Musterweg 3"),
    (60, 150, "B2"), (90, 150, "Muster, Jonas"), (180, 150, "02.09.1991"), (260, 150, "Am Teich 7"),
    (60, 175, "B3"), (90, 175, "Probe, Lena"), (180, 175, "30.01.1995"), (260, 175, "Eingang 01.10.2026"),
]


class TabellenspalteTest(unittest.TestCase):
    def test_dates_below_birth_date_header_are_birth_dates(self):
        s = seite(TABELLE)
        self.assertEqual(sorted(x["text"] for x in found(s, "Geburtsdatum")),
                         ["02.09.1991", "14.03.1987", "30.01.1995"])
        for x in found(s, "Geburtsdatum"):
            self.assertTrue(x["default_selected"])

    def test_dates_in_other_columns_stay_dates(self):
        s = seite(TABELLE)
        self.assertNotIn("01.10.2026", [x["text"] for x in found(s, "Geburtsdatum")])

    def test_wrapped_header(self):
        s = seite([(180, 100, "Geb.-Datu"), (180, 110, "m"), (180, 130, "14.03.1987")])
        self.assertEqual([x["text"] for x in found(s, "Geburtsdatum")], ["14.03.1987"])

    def test_running_text_is_no_table(self):
        s = seite([(60, 100, "Das Geburtsdatum wird im Formular eingetragen."),
                   (60, 130, "Die Sitzung am 01.10.2026 beginnt um neun Uhr.")])
        self.assertEqual(found(s, "Geburtsdatum"), [])

    def test_birth_date_after_geb_on_next_line(self):
        s = detect(["Gewählt wurde Frau Jana Beispiel (geb.", "14.03.1987) aus Musterstadt."])
        self.assertEqual([x["text"] for x in found(s, "Geburtsdatum")], ["14.03.1987)"])


class FehlalarmeTest(unittest.TestCase):
    def test_rank_is_no_address(self):
        for text in ("Rangplatz 2 belegt Frau Beispiel, Rangplatz 3 Herr Muster.",
                     "Der Arbeitsplatz 4 ist frei.", "Listenplatz 12 erhielt Herr Muster."):
            self.assertEqual(found(detect(text), "Adresse"), [], text)

    def test_real_squares_are_still_addresses(self):
        for text in ("Rathausplatz 1", "Marktplatz 3", "Am Teich 7", "Parkplatzweg 5"):
            self.assertEqual([x["text"] for x in found(detect(text), "Adresse")], [text])

    def test_single_upper_case_word_is_no_person(self):
        for wort in ("KOMPASS,", "ABC", "EDV"):
            self.assertFalse(rules.plausible_person(wort), wort)

    def test_names_still_plausible(self):
        for name in ("Jana Beispiel", "MUSTER, JONAS", "Dr. Muster", "LENA Probe"):
            self.assertTrue(rules.plausible_person(name), name)


if __name__ == "__main__":
    unittest.main()
