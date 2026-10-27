"""
Tests Phase 10: Aufräumen von Arbeitsspeicher und temporären Dateien,
keine Dokumentinhalte in Protokoll und Audit-Datenbank, ganzseitige
Scanbilder nicht vorausgewählt. Nur synthetische Daten.

    python -m unittest -v test_phase10_cleanup
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "c" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from test_phase1_redaction import LETTER, pdf_bytes, write_lines  # noqa: E402

SECRETS = ["Mustermann", "104827", "erika.mustermann@example.org", "DE89 3704"]


def run_python(code: str, temp: Path, **kwargs) -> subprocess.Popen:
    """Python-Schnipsel im sidecar-Ordner mit eigenem TEMP-Ordner starten."""
    env = {**os.environ, "TEMP": str(temp), "TMP": str(temp)}
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code)], cwd=HERE, env=env, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


class WorkdirTests(unittest.TestCase):
    """core/workdir.py in eigenen Prozessen - wie die echte App."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="workdir-test-"))
        self.base = self.temp / "Schwaerzsiegel"

    def sessions(self):
        return sorted(p.name for p in self.base.iterdir()) if self.base.exists() else []

    def test_temp_files_go_to_session_dir_and_are_removed(self):
        p = run_python("""
            import tempfile
            from core import workdir
            d = workdir.start()
            f = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            f.write(b"%PDF-test"); f.close()
            print(d, f.name, sep="|")
            workdir.cleanup_session()
            import os; print(os.path.exists(d))
        """, self.temp)
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        first, gone = out.strip().splitlines()
        session, file = first.split("|")
        self.assertTrue(Path(file).is_relative_to(Path(session)), first)
        self.assertTrue(Path(session).is_relative_to(self.base), session)
        self.assertEqual(gone, "False")
        self.assertEqual(self.sessions(), [])

    def test_leftovers_of_crashed_session_removed_on_next_start(self):
        crash = run_python("""
            import os, tempfile
            from core import workdir
            d = workdir.start()
            open(os.path.join(d, "redacted-x.pdf"), "wb").write(b"%PDF-test")
            print(d, flush=True)
            os._exit(1)   # wie ein Absturz oder hartes Beenden: kein Aufräumen
        """, self.temp)
        out, _ = crash.communicate(timeout=60)
        left = Path(out.strip())
        self.assertTrue((left / "redacted-x.pdf").exists())

        nxt = run_python("""
            from core import workdir
            workdir.start()
            print(workdir.cleanup_stale())
            workdir.cleanup_session()
        """, self.temp)
        out, err = nxt.communicate(timeout=60)
        self.assertEqual(nxt.returncode, 0, err)
        self.assertIn(left.name, out)
        self.assertFalse(left.exists())
        self.assertEqual(self.sessions(), [])

    def test_running_session_is_not_touched(self):
        running = run_python("""
            import os, sys
            from core import workdir
            d = workdir.start()
            open(os.path.join(d, "ergebnis.pdf"), "wb").write(b"%PDF-test")
            print(d, flush=True)
            sys.stdin.read()          # läuft, bis die Leitung geschlossen wird
            workdir.cleanup_session()
        """, self.temp)
        active = Path(running.stdout.readline().strip())
        try:
            other = run_python("""
                from core import workdir
                workdir.start()
                print(workdir.cleanup_stale())
                workdir.cleanup_session()
            """, self.temp)
            out, err = other.communicate(timeout=60)
            self.assertEqual(other.returncode, 0, err)
            self.assertEqual(out.strip(), "[]")
            self.assertTrue((active / "ergebnis.pdf").exists())
        finally:
            running.stdin.close()
            running.wait(timeout=60)
            running.stdout.close()
            running.stderr.close()
        self.assertFalse(active.exists())

    def test_sidecar_removes_session_dir_when_app_closes(self):
        """Echter Sidecar (main.py --exit-with-parent): Schließt die App die
        Eingabeleitung, beendet er sich und löscht seinen Arbeitsordner."""
        data_dir = Path(tempfile.mkdtemp(prefix="data-", dir=self.temp))
        env = {**os.environ, "TEMP": str(self.temp), "TMP": str(self.temp),
               "REDACTION_APP_DATA_DIR": str(data_dir), "REDACTION_SESSION_TOKEN": "d" * 64,
               "PYTHONIOENCODING": "utf-8"}
        p = subprocess.Popen([sys.executable, "main.py", "--exit-with-parent"], cwd=HERE, env=env, text=True,
                             encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 180
            while time.time() < deadline:
                line = p.stdout.readline()
                if not line or line.startswith("SIDECAR_PORT="):
                    break
            self.assertTrue(line.startswith("SIDECAR_PORT="), "Sidecar hat keinen Port gemeldet")
            self.assertEqual(len(self.sessions()), 1)
            p.stdin.close()
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
        self.assertEqual(self.sessions(), [])


class ApiCleanupTests(unittest.TestCase):
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

    def wait(self, doc_id):
        for _ in range(400):
            progress = self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).json()
            if progress.get("state") in ("done", "error", "cancelled"):
                return progress
            time.sleep(0.05)
        return progress

    def upload(self, data=None):
        data = data or pdf_bytes(lambda d: write_lines(d.new_page(), LETTER))
        res = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                               files={"file": ("brief.pdf", data, "application/pdf")})
        self.assertEqual(res.status_code, 200, res.text)
        doc_id = res.json()["document_id"]
        self.assertEqual(self.wait(doc_id)["state"], "done")
        return doc_id

    def finalize(self, doc_id, mode):
        page = self.client.get(f"/documents/{doc_id}/pages/0", headers=self.TOKEN).json()
        self.client.get(f"/documents/{doc_id}/pages/0/image", headers=self.TOKEN)  # Vorschau im Zwischenspeicher
        words = {w["text"]: w for w in page["words"]}
        for text in ("Mustermann", "104827"):
            self.client.post(f"/documents/{doc_id}/marks", headers=self.TOKEN,
                             json={"page": 0, "kind": "text", "word_ids": [words[text]["id"]]})
        self.client.post(f"/documents/{doc_id}/review/confirm", headers=self.TOKEN)
        res = self.client.post(f"/documents/{doc_id}/finalize", headers=self.TOKEN, json={"mode": mode})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def assert_gone(self, doc_id, files):
        pp = self.main.pdf_processor
        self.assertNotIn(doc_id, self.main.DOCUMENTS)
        self.assertNotIn(doc_id, pp._RAW_BYTES)
        self.assertFalse([k for k in pp._PREVIEW_CACHE if k[0] == doc_id])
        for f in files:
            self.assertFalse(Path(f).exists(), f)
        self.assertEqual(self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).status_code, 404)

    def test_result_files_are_in_session_dir(self):
        doc_id = self.upload()
        result = self.finalize(doc_id, "pseudonymize")
        session = self.main.workdir.SESSION_DIR
        for f in (result["output_path"], result["mapping_table_path"]):
            self.assertTrue(Path(f).is_relative_to(session), f)

    def test_close_removes_memory_and_files(self):
        doc_id = self.upload()
        result = self.finalize(doc_id, "pseudonymize")
        files = [result["output_path"], result["mapping_table_path"]]
        self.assertTrue(all(Path(f).exists() for f in files))
        self.assertEqual(self.client.delete(f"/documents/{doc_id}", headers=self.TOKEN).status_code, 200)
        self.assert_gone(doc_id, files)
        self.assertEqual(self.client.delete(f"/documents/{doc_id}", headers=self.TOKEN).status_code, 404)

    def test_close_requires_session_token(self):
        doc_id = self.upload()
        self.assertEqual(self.client.delete(f"/documents/{doc_id}").status_code, 401)
        self.assertIn(doc_id, self.main.DOCUMENTS)

    def test_new_document_replaces_previous(self):
        first = self.upload()
        result = self.finalize(first, "redact")
        second = self.upload()
        self.assert_gone(first, [result["output_path"]])
        self.assertIn(second, self.main.DOCUMENTS)

    def test_close_during_analysis_leaves_nothing(self):
        original = self.main.pdf_processor.extract_page

        def slow(*args, **kwargs):
            time.sleep(0.3)
            return original(*args, **kwargs)

        data = pdf_bytes(lambda d: [write_lines(d.new_page(), LETTER) for _ in range(5)])
        with patch.object(self.main.pdf_processor, "extract_page", side_effect=slow):
            doc_id = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                                      files={"file": ("x.pdf", data, "application/pdf")}).json()["document_id"]
            time.sleep(0.4)
            self.assertEqual(self.client.delete(f"/documents/{doc_id}", headers=self.TOKEN).status_code, 200)
            time.sleep(2.0)  # Analyse-Thread läuft zu Ende
        self.assert_gone(doc_id, [])

    def test_no_document_content_in_log_or_audit(self):
        doc_id = self.upload()
        self.finalize(doc_id, "pseudonymize")
        self.client.delete(f"/documents/{doc_id}", headers=self.TOKEN)
        data_dir = Path(os.environ["REDACTION_APP_DATA_DIR"])
        log_text = (data_dir / "sidecar.log").read_text(encoding="utf-8")
        with sqlite3.connect(data_dir / "audit.db") as conn:
            audit_text = "\n".join(" ".join(map(str, row)) for row in conn.execute("SELECT * FROM audit_log"))
        self.assertIn("document_closed", audit_text)
        for secret in SECRETS:
            self.assertNotIn(secret, log_text)
            self.assertNotIn(secret, audit_text)


class FullPageImageTests(unittest.TestCase):
    """Phase 10 (V7) / Phase 16b: Ein Scan (Bild über die ganze Seite) wird nicht
    mehr als Vorschlag angezeigt (Rahmen über der ganzen Seite störte); kleine
    Bilder werden weiter vorgeschlagen und sind vorausgewählt."""

    @staticmethod
    def png(w, h):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (200, 200, 200)).save(buf, format="PNG")
        return buf.getvalue()

    def test_full_page_image_not_suggested(self):
        from core import pdf_processor
        doc = fitz.open()
        scan = doc.new_page()
        scan.insert_image(scan.rect, stream=self.png(300, 420))
        photo = doc.new_page()
        photo.insert_image(fitz.Rect(72, 72, 172, 172), stream=self.png(100, 100))
        rotated = doc.new_page()
        rotated.set_rotation(90)
        rotated.insert_image(rotated.rect * rotated.derotation_matrix, stream=self.png(300, 420))
        self.assertEqual(pdf_processor.detect_embedded_images(doc[0]), [])
        self.assertEqual(pdf_processor.detect_embedded_images(doc[2]), [], "gedrehte Scan-Seite")
        found = pdf_processor.detect_embedded_images(doc[1])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["default_selected"])
        self.assertEqual(found[0]["confidence"], 0.5)

    def test_image_covering_most_of_the_page_but_not_all_is_still_suggested(self):
        from core import pdf_processor
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(fitz.Rect(40, 40, 555, 600), stream=self.png(300, 300))  # gut 60 % der Seite
        self.assertEqual(len(pdf_processor.detect_embedded_images(page)), 1)


if __name__ == "__main__":
    unittest.main()
