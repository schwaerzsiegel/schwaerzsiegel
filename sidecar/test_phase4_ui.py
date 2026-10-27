"""
Tests Phase 4: Kunden-Branding und "Erneut analysieren" (Sidecar-Seite).
Nur synthetische Daten, kein KI-Modell nötig.

    python -m unittest -v test_phase4_ui
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "e" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import fitz  # noqa: E402

from core import branding  # noqa: E402

EXAMPLE = HERE / "branding-beispiel"


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="branding-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = patch.object(branding, "BRANDING_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        (self.dir / "branding.json").write_text(json.dumps(data), encoding="utf-8")

    def test_missing_folder_gives_default_design(self):
        info = branding.load()
        self.assertFalse(info["active"])
        self.assertEqual((info["productName"], info["accent"], info["logo"]), ("Schwärzsiegel", branding.DEFAULT_ACCENT, None))

    def test_example_branding_is_valid(self):
        for f in EXAMPLE.iterdir():
            shutil.copy(f, self.dir / f.name)
        info = branding.load()
        self.assertTrue(info["active"])
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["customerName"], "Stadtwerke Musterhausen")
        self.assertEqual(info["accent"], "#0F6B6B")
        content, media_type = branding.logo("dark")
        self.assertEqual(media_type, "image/svg+xml")
        self.assertIn(b"<svg", content)

    def test_invalid_values_fall_back_with_reason(self):
        (self.dir / "big.png").write_bytes(b"0" * (branding.MAX_LOGO_BYTES + 1))
        self.write({"productName": "  \x07 ", "accent": "#F0F0F0", "logo": "../secret.png", "logoDark": "big.png",
                    "support": {"url": "javascript:alert(1)", "email": "a@example.org"}, "showPoweredBy": "nein"})
        info = branding.load()
        self.assertEqual(info["productName"], "Schwärzsiegel")
        self.assertEqual(info["accent"], branding.DEFAULT_ACCENT)  # zu hell für weiße Schrift
        self.assertIsNone(info["logo"])
        self.assertIsNone(info["logoDark"])
        self.assertTrue(info["showPoweredBy"])
        self.assertEqual(info["support"], {"email": "a@example.org"})
        joined = " ".join(info["warnings"])
        for part in ("Kontrast", "Dateiname", "2 MB", "support.url", "productName"):
            self.assertIn(part, joined)

    def test_broken_json_never_blocks_start(self):
        (self.dir / "branding.json").write_text("{kaputt", encoding="utf-8")
        info = branding.load()
        self.assertFalse(info["active"])
        self.assertTrue(info["warnings"])

    def test_contrast(self):
        self.assertAlmostEqual(branding.contrast_with_white("#000000"), 21.0, places=1)
        self.assertGreaterEqual(branding.contrast_with_white(branding.DEFAULT_ACCENT), 4.5)


class ApiPhase4Tests(unittest.TestCase):
    TOKEN = {"X-Redaction-Session": os.environ["REDACTION_SESSION_TOKEN"]}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main
        cls.main = main
        cls.patcher = patch("main.models.start_preload")
        cls.patcher.start()
        cls.cm = TestClient(main.app)
        cls.client = cls.cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)
        cls.patcher.stop()

    def wait(self, doc_id):
        for _ in range(200):
            p = self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).json()
            if p["state"] not in ("queued", "processing"):
                return p
            time.sleep(0.05)
        self.fail("Zeitüberschreitung")

    def test_branding_endpoints(self):
        with patch.object(branding, "BRANDING_DIR", EXAMPLE):
            info = self.client.get("/branding", headers=self.TOKEN).json()
            logo = self.client.get("/branding/logo?variant=light", headers=self.TOKEN)
        self.assertEqual(info["productName"], "Datenschutz-Schwärzung")
        self.assertEqual(logo.status_code, 200)
        self.assertEqual(logo.headers["content-type"], "image/svg+xml")
        self.assertIn("default-src 'none'", logo.headers["content-security-policy"])
        self.assertEqual(self.client.get("/branding").status_code, 401)  # auch Branding nur mit Sitzungstoken
        with patch.object(branding, "BRANDING_DIR", Path(tempfile.mkdtemp())):
            self.assertEqual(self.client.get("/branding/logo", headers=self.TOKEN).status_code, 404)

    def test_status_reports_ocr(self):
        self.assertIn("ocr", self.client.get("/status", headers=self.TOKEN).json())

    def test_reanalyze_keeps_manual_and_decided_marks(self):
        doc = fitz.open()
        page = doc.new_page()
        for i, line in enumerate(["Personalnummer: 104827", "IBAN: DE89 3704 0044 0532 0130 00",
                                  "E-Mail: erika.mustermann@example.org", "Nur von Hand: Geheimprojekt"]):
            page.insert_text((72, 72 + 16 * i), line, fontsize=11)
        res = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                               files={"file": ("x.pdf", doc.tobytes(), "application/pdf")})
        doc_id = res.json()["document_id"]
        self.assertEqual(self.wait(doc_id)["state"], "done")
        words = {w["text"]: w for w in self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()["words"]}
        manual = self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                                  json={"page": 0, "kind": "text", "word_ids": [words["Geheimprojekt"]["id"]]}).json()

        # Erste erneute Analyse (nur Regeln): Vorschläge kommen hinzu
        self.client.post(f"/documents/{doc_id}/reanalyze", headers=self.TOKEN, json={"mode": "rules"})
        self.assertEqual(self.wait(doc_id)["step"], "Fertig")
        marks = self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()
        self.assertEqual(marks["analysis_mode"], "rules")
        by_label = {m["label"]: m for m in marks["marks"]}
        self.assertIn("Personalnummer", by_label)
        self.assertIn(manual["id"], {m["id"] for m in marks["marks"]})

        # Entscheidungen setzen, dann nochmals analysieren
        self.client.patch(f"/documents/{doc_id}/marks/{by_label['IBAN']['id']}", headers=self.TOKEN, json={"status": "confirmed"})
        self.client.patch(f"/documents/{doc_id}/marks/{by_label['E-Mail']['id']}", headers=self.TOKEN, json={"status": "rejected"})
        self.client.post(f"/documents/{doc_id}/reanalyze", headers=self.TOKEN, json={"mode": "rules"})
        self.wait(doc_id)
        after = self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()["marks"]
        ids = {m["id"]: m for m in after}
        self.assertEqual(ids[by_label["IBAN"]["id"]]["status"], "confirmed")
        self.assertEqual(ids[by_label["E-Mail"]["id"]]["status"], "rejected")
        self.assertIn(manual["id"], ids)
        # keine Dubletten zu entschiedenen Stellen, Personalnummer erneut als Vorschlag
        self.assertEqual(sum(1 for m in after if m["label"] == "IBAN"), 1)
        self.assertEqual(sum(1 for m in after if m["label"] == "E-Mail"), 1)
        self.assertEqual(sum(1 for m in after if m["label"] == "Personalnummer"), 1)

    def test_cancelled_reanalysis_changes_nothing(self):
        doc = fitz.open()
        for i in range(15):
            doc.new_page().insert_text((72, 72), f"Personalnummer: 10482{i}", fontsize=11)
        doc_id = self.client.post("/documents", headers=self.TOKEN, data={"mode": "rules"},
                                  files={"file": ("x.pdf", doc.tobytes(), "application/pdf")}).json()["document_id"]
        self.wait(doc_id)
        before = sorted(m["id"] for m in self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()["marks"])
        original = self.main.ner.detect

        def slow(*args, **kwargs):
            time.sleep(0.1)
            return original(*args, **kwargs)

        with patch.object(self.main.ner, "detect", side_effect=slow):
            self.client.post(f"/documents/{doc_id}/reanalyze", headers=self.TOKEN, json={"mode": "rules"})
            time.sleep(0.25)
            self.client.post(f"/documents/{doc_id}/cancel", headers=self.TOKEN)
            progress = self.wait(doc_id)
        self.assertIn("abgebrochen", progress["step"])
        after = sorted(m["id"] for m in self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).json()["marks"])
        self.assertEqual(before, after)

    def test_reanalyze_rejects_unknown_mode(self):
        doc = fitz.open()
        doc.new_page()
        doc_id = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                                  files={"file": ("x.pdf", doc.tobytes(), "application/pdf")}).json()["document_id"]
        self.wait(doc_id)
        self.assertEqual(self.client.post(f"/documents/{doc_id}/reanalyze", headers=self.TOKEN, json={"mode": "manual"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
