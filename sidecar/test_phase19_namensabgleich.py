"""
Phase 19: Namen im ganzen Dokument abgleichen, „gez. Name“, Tabellenzellen.

Nachgestellt nach einem synthetischen Vermerk mit Tabellen: Kommissionsmitglieder in einer Tabellenspalte
(„Dr. Möller, Jensen, Hansen“) und „gez. Hansen“ wurden nicht erkannt, ein Tabellenkopf
(„Anschrift | Kontakt | Status“) wurde zur Adresse und eine umbrochene Personalnummer zur Telefonnummer.
Ohne KI-Modelle lauffähig. Nur erfundene Angaben.

    cd sidecar
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase19_namensabgleich
"""

import unittest

import fitz

from core import marks, namensabgleich, ner
from core import pdf_processor as pdf
from test_phase17_erkennung import detect, found

SEITE_1 = [
    "Zum Gespräch eingeladen wurden Frau Dr. Jana Beispiel und Herr Jonas Muster.",
    "Die Kommission bestand aus Frau Hansen, Herrn Dr. Möller und Frau Thomsen.",
]
SEITE_2 = [
    "Uhrzeit   Bewerber/in   Anwesende Kommissionsmitglieder",
    "09:00     Jana Beispiel   Dr. Möller, Jensen, Hansen,",
    "                          Thomsen, Wiese",
    "Personalakte Muster; Anlagen: Bewertungsbögen (Beispiel, Muster)",
    "Die Kosten trägt das Amt. Ein Koch war nicht anwesend.",
]


def analysiere(*seiten):
    doc = fitz.open()
    for lines in seiten:
        page = doc.new_page()
        y = 80
        for line in lines:
            page.insert_text((50, y), line, fontsize=10, fontname="helv")
            y += 16
    data = doc.tobytes()
    doc.close()
    pages = []
    with fitz.open(stream=data, filetype="pdf") as d:
        for i, p in enumerate(d):
            pg = pdf.extract_page(p, i, detect_images=False)
            pg.suggestions.extend(ner.detect(pg.words, use_llm=False, use_gliner=False))
            marks.init_auto(pg)
            pages.append(pg)
    extra = namensabgleich.ergaenzen([p.words for p in pages], [p.suggestions for p in pages])
    return pages, extra


def woerter(suggestions):
    return {namensabgleich._clean(w) for s in suggestions for w in s["text"].split()}


class NamensabgleichTest(unittest.TestCase):
    def setUp(self):
        self.pages, self.extra = analysiere(SEITE_1, SEITE_2)

    def test_known_names_are_proposed_elsewhere(self):
        neu = woerter(self.extra[1])
        for name in ("Beispiel", "Möller", "Hansen", "Thomsen", "Muster", "Jana"):
            self.assertIn(name, neu, self.extra[1])

    def test_unknown_names_in_a_list_of_names_are_proposed(self):
        neu = {s["text"].strip(",. "): s for s in self.extra[1]}
        for name in ("Jensen", "Wiese"):
            self.assertIn(name, neu, self.extra[1])
            self.assertLess(neu[name]["confidence"], 0.85)
            self.assertTrue(neu[name]["default_selected"])

    def test_new_proposals_are_to_be_checked_and_preselected(self):
        for s in self.extra[1]:
            self.assertEqual(s["label"], "Person")
            self.assertTrue(s["default_selected"])
            self.assertLess(s["confidence"], 0.85, "nicht in die Sammelbestätigung")

    def test_ordinary_words_and_titles_are_not_proposed(self):
        neu = woerter(self.extra[1])
        for wort in ("Dr", "Koch", "Amt", "Kosten", "Personalakte", "Anlagen", "Uhrzeit"):
            self.assertNotIn(wort, neu)

    def test_words_already_proposed_are_not_duplicated(self):
        vorhanden = {wid for s in self.pages[0].suggestions for wid in s["word_ids"]}
        self.assertFalse(vorhanden & {wid for s in self.extra[0] for wid in s["word_ids"]})

    def test_one_proposal_per_person_in_a_list(self):
        # Phase 20: jede Person einzeln, damit sie sich einzeln auslassen lässt
        for sug in self.extra[1]:
            self.assertNotIn(",", sug["text"].rstrip(",; "), sug["text"])  # kein „Möller, Jensen“ in einem Vorschlag

    def test_rejected_names_are_not_spread(self):
        pages, _ = analysiere(SEITE_1, SEITE_2)
        for s in pages[0].suggestions:
            s["status"] = marks.REJECTED
        extra = namensabgleich.ergaenzen([p.words for p in pages], [p.suggestions for p in pages])
        self.assertEqual(extra, [[], []])

    def test_list_without_known_names_stays_unchanged(self):
        _, extra = analysiere(["Teilnehmende Städte: Kiel, Lübeck, Flensburg, Husum"])
        self.assertEqual(extra, [[]])


class RegelnTest(unittest.TestCase):
    def test_signed_on_behalf_names(self):
        for zeile, name in (("gez. Hansen", "Hansen"), ("gez. Dr. Möller", "Dr. Möller"),
                            ("i. A. Wiebke Hansen", "Wiebke Hansen"), ("i.V. Thomsen", "Thomsen")):
            self.assertEqual([s["text"] for s in found(detect(zeile), "Person")], [name], zeile)

    def test_table_header_is_no_address(self):
        s = detect("Nr. Name, Vorname Geb.-Datum Anschrift Kontakt Status")
        self.assertEqual(found(s, "Adresse"), [])

    def test_labelled_address_and_place_still_found(self):
        self.assertEqual([x["text"] for x in found(detect("Anschrift: Musterweg 12, 12345 Musterstadt"), "Adresse")],
                         ["Musterweg 12, 12345 Musterstadt"])
        self.assertEqual([x["text"] for x in found(detect("wohnhaft in Musterdorf"), "Adresse")], ["Musterdorf"])

    def test_personnel_number_in_wrapped_cell(self):
        s = detect(["intern, Pers.-Nr.", "004-12345, derzeit E 11"])
        self.assertEqual([x["text"] for x in found(s, "Personalnummer")], ["004-12345,"])
        self.assertEqual(found(s, "Telefonnummer"), [])

    def test_section_numbers_are_no_house_numbers(self):
        for text in ("Im Referat 3.1 wird geprüft.", "Am Standort 2.14 tagt die Kommission."):
            self.assertEqual(found(detect(text), "Adresse"), [], text)
        self.assertEqual([x["text"] for x in found(detect("Am Redder 4"), "Adresse")], ["Am Redder 4"])

    def test_phone_at_line_start_after_unrelated_line_is_kept(self):
        s = detect(["Bitte rufen Sie wegen des Vertrags an:", "0431 123456"])
        self.assertEqual([x["text"] for x in found(s, "Telefonnummer")], ["0431 123456"])


if __name__ == "__main__":
    unittest.main()
