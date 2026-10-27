"""
Phase 7: Modelle aus Pfaden mit Umlauten laden (Installationsordner
„Schwärzsiegel“, Benutzernamen wie „Jürgen“).

Aufruf (im Ordner sidecar):
    ..\\.venv\\Scripts\\python.exe -m unittest test_phase7_paths -v
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core import spm_paths  # noqa: F401  (installiert die Abhilfe)
from core.paths import HF_CACHE_DIR

import sentencepiece

SPM_FILES = sorted(HF_CACHE_DIR.glob("models--microsoft--mdeberta-v3-base/snapshots/*/spm.model"))


@unittest.skipUnless(SPM_FILES, "mdeberta-Tokenizer (models/hf-cache) nicht vorhanden")
class SentencepieceUnicodePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="spm-"))
        self.target = self.tmp / "Schwärzsiegel Jürgen ß" / "spm.model"
        self.target.parent.mkdir()
        shutil.copyfile(SPM_FILES[0], self.target)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_from_non_ascii_path(self):
        processor = sentencepiece.SentencePieceProcessor()
        processor.Load(str(self.target))
        self.assertEqual(processor.vocab_size(), 250101)

    def test_snake_case_and_constructor(self):
        # transformers nutzt je nach Tokenizer load(...) oder Load(...)
        processor = sentencepiece.SentencePieceProcessor()
        processor.load(str(self.target))
        self.assertGreater(processor.GetPieceSize(), 0)
        self.assertGreater(sentencepiece.SentencePieceProcessor(model_file=str(self.target)).GetPieceSize(), 0)

    def test_deberta_tokenizer_from_non_ascii_path(self):
        from transformers import DebertaV2Tokenizer

        tokenizer = DebertaV2Tokenizer(vocab_file=str(self.target))
        self.assertTrue(tokenizer.tokenize("Erika Mustermann wohnt in Köln"))

    def test_missing_file_still_fails(self):
        processor = sentencepiece.SentencePieceProcessor()
        with self.assertRaises(OSError):
            processor.Load(str(self.target.parent / "fehlt-ä.model"))


if __name__ == "__main__":
    unittest.main()
