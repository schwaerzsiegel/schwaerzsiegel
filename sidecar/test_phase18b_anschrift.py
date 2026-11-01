"""
Phase 18b: „Anschrift“ im Fließtext ist keine Feldbezeichnung.

Fehlalarm im Beispielbescheid der Benutzeranleitung: „Bitte teilen Sie uns Änderungen … oder Ihrer
Anschrift umgehend mit.“ wurde als Adresse „umgehend mit.“ vorgeschlagen. Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase18b_anschrift
"""

import unittest

from test_phase17_erkennung import detect, found


def adressen(text):
    return [s["text"] for s in found(detect(text), "Adresse")]


class AnschriftImFliesstextTest(unittest.TestCase):
    def test_word_anschrift_in_running_text_gives_no_address(self):
        self.assertEqual(adressen("Bitte teilen Sie uns Änderungen Ihres Einkommens oder Ihrer Anschrift "
                                  "umgehend mit."), [])
        self.assertEqual(adressen("Die Adresse wird nicht weitergegeben."), [])

    def test_labelled_addresses_are_still_found(self):
        self.assertEqual(adressen("Anschrift: Musterweg 12, 12345 Musterstadt"), ["Musterweg 12, 12345 Musterstadt"])
        self.assertEqual(adressen("wohnhaft in Musterdorf"), ["Musterdorf"])
        self.assertEqual(adressen("Adresse: 24999 Musterdorf"), ["24999 Musterdorf"])

    def test_street_before_postcode_does_not_take_words_of_the_sentence(self):
        self.assertEqual(adressen("Ihre neue Anschrift lautet Musterweg 3, 12345 Musterstadt."),
                         ["Musterweg 3,", "12345 Musterstadt."])
        self.assertIn("Op de Hööch 7,", adressen("Er wohnt jetzt Op de Hööch 7, 24999 Musterdorf"))


if __name__ == "__main__":
    unittest.main()
