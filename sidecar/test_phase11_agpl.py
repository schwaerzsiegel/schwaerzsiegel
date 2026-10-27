"""
Tests Phase 11: Open Source (AGPL) - die Lizenzprüfung ist entfernt.
Ohne jede Lizenzdatei lassen sich Dokumente öffnen und schwärzen, und es
gibt keine Lizenz-Schnittstelle mehr. Nur synthetische Daten.

    python -m unittest -v test_phase11_agpl
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "c" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_phase1_redaction import LETTER, pdf_bytes, write_lines  # noqa: E402


class NoLicenseTests(unittest.TestCase):
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

    def test_lizenzmodul_entfernt(self):
        self.assertFalse((HERE / "core" / "licensing.py").exists())
        self.assertFalse((HERE / "core" / "license_keys.json").exists())
        self.assertNotIn("licensing", sys.modules)

    def test_status_ohne_lizenzangabe(self):
        status = self.client.get("/status", headers=self.TOKEN).json()
        self.assertNotIn("license", status)

    def test_keine_lizenz_schnittstelle(self):
        self.assertEqual(self.client.get("/license", headers=self.TOKEN).status_code, 404)
        res = self.client.post("/license", headers=self.TOKEN, json={"content": "{}"})
        self.assertIn(res.status_code, (404, 405))

    def test_oeffnen_und_schwaerzen_ohne_lizenzdatei(self):
        from core.paths import DATA_DIR
        self.assertFalse((DATA_DIR / "lizenz").exists())
        data = pdf_bytes(lambda d: write_lines(d.new_page(), LETTER))
        res = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                               files={"file": ("brief.pdf", data, "application/pdf")})
        self.assertEqual(res.status_code, 200, res.text)
        doc_id = res.json()["document_id"]
        for _ in range(400):
            progress = self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).json()
            if progress.get("state") in ("done", "error", "cancelled"):
                break
            time.sleep(0.05)
        self.assertEqual(progress["state"], "done")

        page = self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()
        word = next(w for w in page["words"] if w["text"] == "Mustermann")
        self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                         json={"page": 0, "kind": "text", "word_ids": [word["id"]]})
        self.client.post(f"/documents/{doc_id}/review/confirm", headers=self.TOKEN)
        res = self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": "redact"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse((DATA_DIR / "lizenz").exists())


if __name__ == "__main__":
    unittest.main()
