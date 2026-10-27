"""Sind alle Pakete da, die die KI-Modi zur Laufzeit brauchen?

Im Cloud-Build gibt es keine KI-Modelle, die Tests mit echten Modellen werden dort übersprungen.
Fehlt ein Paket, das nur beim Laden eines Modells gebraucht wird, fällt das sonst erst in der
fertigen App auf (erster Cloud-Build: protobuf fehlte, Schnell-KI und Gründliche KI waren gesperrt).
"""
import importlib
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HIER = Path(__file__).resolve().parent


class LaufzeitPaketeTests(unittest.TestCase):
    def test_pakete_fuer_die_ki_lassen_sich_laden(self):
        for modul in ("gliner", "llama_cpp", "transformers", "torch", "sentencepiece", "google.protobuf"):
            with self.subTest(modul=modul):
                importlib.import_module(modul)

    def test_transformers_findet_protobuf(self):
        # Ohne protobuf kann transformers den Tokenizer von GLiNER nicht umwandeln.
        from transformers.utils import is_protobuf_available, is_sentencepiece_available
        self.assertTrue(is_protobuf_available())
        self.assertTrue(is_sentencepiece_available())

    def test_jede_direkte_abhaengigkeit_steht_in_der_festen_paketliste(self):
        def namen(datei):
            zeilen = (z.split("#")[0].strip() for z in (HIER / datei).read_text(encoding="utf-8").splitlines())
            return {canonicalize_name(Requirement(z).name) for z in zeilen if z}

        fehlend = namen("requirements.txt") - namen("requirements-lock.txt")
        self.assertEqual(fehlend, set(), "requirements-lock.txt neu erzeugen: tools/abhaengigkeiten_sperren.py")


if __name__ == "__main__":
    unittest.main()
