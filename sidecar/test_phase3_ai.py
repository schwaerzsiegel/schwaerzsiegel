"""
Tests Phase 3: lokaler KI-Modus, Offline-Betrieb, Abbruch, Modellstatus.
Laufen ohne geladenes Sprachmodell (Modell wird simuliert). Der Test mit dem
echten Modell läuft nur mit RUN_LLM_TESTS=1 (dauert ca. 1 Minute).

    python -m unittest -v test_phase3_ai
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REDACTION_SESSION_TOKEN", "c" * 64)
os.environ.setdefault("REDACTION_APP_DATA_DIR", tempfile.mkdtemp(prefix="redaction-test-"))
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import fitz  # noqa: E402

from core import llm, ner, rules  # noqa: E402


class OfflineGuardTests(unittest.TestCase):
    """In einem eigenen Prozess, weil sich ein Audit-Hook nicht entfernen lässt."""

    SCRIPT = r"""
import socket, sys, threading, urllib.request
sys.path.insert(0, {here!r})
from core import offline
offline.install()
results = []
for target in (("example.org", 80), ("93.184.215.14", 443), ("8.8.8.8", 53)):
    try:
        socket.create_connection(target, timeout=2)
        results.append("OFFEN")
    except PermissionError:
        results.append("gesperrt")
    except OSError as e:
        results.append("OSError")
try:
    urllib.request.urlopen("https://huggingface.co/api/models", timeout=2)
    results.append("OFFEN")
except Exception as e:
    results.append("gesperrt" if "blockiert" in str(e) else type(e).__name__)
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
port = server.getsockname()[1]
client = socket.create_connection(("127.0.0.1", port), timeout=2)
results.append("lokal-ok")
client.close(); server.close()
print(",".join(results))
print(len(offline.blocked))
"""

    def test_external_connections_are_blocked_local_allowed(self):
        out = subprocess.run([sys.executable, "-c", self.SCRIPT.format(here=str(HERE))],
                             capture_output=True, text=True, timeout=60)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "gesperrt,gesperrt,gesperrt,gesperrt,lokal-ok", out.stderr)
        self.assertGreaterEqual(int(lines[1]), 4)

    def test_sidecar_installs_guard(self):
        script = (f"import sys; sys.path.insert(0, {str(HERE)!r})\n"
                  "from unittest.mock import patch\n"
                  "import main\n"
                  "from core import offline\n"
                  "print(offline.is_installed())\n")
        env = dict(os.environ, REDACTION_SESSION_TOKEN="d" * 64)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300,
                             cwd=str(HERE), env=env)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "True", out.stderr[-2000:])


class FakeModel:
    """Simuliertes GLiNER: findet "Vogel" und merkt sich die Abschnittslängen."""

    def __init__(self):
        self.lengths = []

    def predict_entities(self, text, labels, threshold):
        self.lengths.append(len(text.split()))
        pos = text.find("Lena Vogel")
        return [{"label": "Person", "text": "Lena Vogel", "start": pos, "end": pos + 10, "score": 0.9}] if pos >= 0 else []

    def inference(self, texts, labels, threshold, batch_size=8):
        return [self.predict_entities(t, labels, threshold) for t in texts]


class GlinerChunkingTests(unittest.TestCase):
    def test_long_pages_are_checked_completely(self):
        """GLiNER ignoriert Text nach 384 Token - deshalb Abschnitte."""
        tokens = ["Wort"] * 2000 + ["Lena", "Vogel"] + ["Ende"] * 50
        words = [{"id": f"p0_w{i}", "text": t, "bbox": [0, 0, 1, 1], "line": "0-0"} for i, t in enumerate(tokens)]
        fake = FakeModel()
        with patch.object(ner, "_get_model", return_value=fake):
            result = ner.detect(words, use_llm=False)
        persons = [s for s in result if s["label"] == "Person"]
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["word_ids"], ["p0_w2000", "p0_w2001"])
        self.assertLessEqual(max(fake.lengths), ner._CHUNK_WORDS)
        self.assertGreater(len(fake.lengths), 2000 // ner._CHUNK_WORDS)


class RuleAdditionsTests(unittest.TestCase):
    def test_service_numbers_are_not_private(self):
        for value in ("0800 123 4567", "+49 800 1234567", "01805 123456", "0900 1234567"):
            self.assertTrue(rules.is_service_number(value), value)
        self.assertFalse(rules.is_service_number("0171 2345678"))
        hits = [h for h in rules.all_hits("Störungsdienst: 0800 123 4567") if "0800" in "Störungsdienst: 0800 123 4567"[h.start:h.end]]
        self.assertEqual({h.label for h in hits}, {"Service-Rufnummer"})
        self.assertIn("Service-Rufnummer", rules.DEFAULT_OFF_LABELS)

    def test_model_addresses_need_address_shape(self):
        self.assertFalse(rules.plausible_address("Raum 2.14"))
        self.assertFalse(rules.plausible_address("03153 9960"))
        self.assertTrue(rules.plausible_address("Musterweg 12a"))
        self.assertTrue(rules.plausible_address("12345 Beispielstadt"))
        self.assertTrue(rules.plausible_address("Kiel"))


class LlmLogicTests(unittest.TestCase):
    def test_parse_owner_answers(self):
        self.assertTrue(llm._parse_response('{"reason": "x", "owner": "Privatperson"}')["redact"])
        self.assertFalse(llm._parse_response('{"reason": "x", "owner": "Firma oder Behörde"}')["redact"])
        self.assertFalse(llm._parse_response('<think>\n</think>{"reason": "x", "owner": "Allgemein"}')["redact"])
        self.assertIsNone(llm._parse_response('{"reason": "abgeschnitten'))
        self.assertIsNone(llm._parse_response('{"reason": "x", "owner": "vielleicht"}'))

    def s(self, label, confidence=0.6, selected=True):
        return {"label": label, "text": "x", "confidence": confidence, "default_selected": selected, "source": "regex"}

    def test_conservative_application(self):
        person = self.s("Person")
        llm.apply_answer(person, {"redact": False, "reason": "Firma"})
        self.assertEqual((person["confidence"], person["default_selected"]), (0.6, True))  # kein Veto

        phone = self.s("Telefonnummer", 0.7)
        llm.apply_answer(phone, {"redact": False, "reason": "Zentrale"})
        self.assertEqual((phone["confidence"], phone["default_selected"]), (0.15, False))  # bleibt sichtbar

        date = self.s("Datum", 0.6, selected=False)
        llm.apply_answer(date, {"redact": True, "reason": "Geburtsdatum"})
        self.assertEqual((date["label"], date["default_selected"]), ("Geburtsdatum", True))
        self.assertLess(date["confidence"], 0.85)  # nicht in der Sammelbestätigung

        contradictory = self.s("Datum", 0.6, selected=False)
        llm.apply_answer(contradictory, {"redact": False, "reason": "Geburtstag des Kollegen Jonas Becker"})
        self.assertEqual((contradictory["confidence"], contradictory["llm_reviewed"]), (0.6, False))
        self.assertIn("widersprüchlich", contradictory["llm_reason"])

        broken = self.s("Telefonnummer", 0.7)
        llm.apply_answer(broken, None)
        self.assertEqual((broken["confidence"], broken["llm_reviewed"]), (0.7, False))

    def test_hybrid_model_gets_no_think(self):
        with patch.object(llm, "_loaded_info", {"hybrid_thinking": True}):
            messages = llm.build_messages("Text 0800 1", {"label": "Telefonnummer", "text": "0800 1"})
        self.assertTrue(all(m["content"].endswith("/no_think") for m in messages if m["role"] == "user"))
        self.assertIn("Service", messages[-1]["content"])  # Frage je Kategorie

    def test_missing_model_and_low_memory_are_explained(self):
        with tempfile.TemporaryDirectory() as empty, patch.object(llm, "MODELS_DIR", Path(empty)), \
                patch.object(llm, "_loaded_info", None), patch.object(llm, "_llm", None):
            info = llm.model_info()
            self.assertFalse(info["available"])
            self.assertIn("Kein Sprachmodell", info["problem"])
            with self.assertRaises(RuntimeError):
                llm.load_model()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "Qwen3-1.7B-Q4_K_M.gguf").write_bytes(b"0" * 1000)
            with patch.object(llm, "MODELS_DIR", Path(folder)), patch.object(llm, "_available_ram_gb", return_value=0.3), \
                    patch.object(llm, "_loaded_info", None), patch.object(llm, "_llm", None):
                info = llm.model_info()
                self.assertTrue(info["available"])
                self.assertIn("Zu wenig freier Arbeitsspeicher", info["problem"])

    def test_cancel_between_calls(self):
        calls = []
        with patch.object(llm, "_get_llm", return_value=object()), \
                patch.object(llm, "_ask", side_effect=lambda *_: calls.append(1) or '{"reason": "x", "owner": "Allgemein"}'):
            items = [self.s("Datum") | {"text": str(i)} for i in range(5)]
            with self.assertRaises(llm.Cancelled):
                llm.disambiguate("text", items, should_cancel=lambda: len(calls) >= 2)
        self.assertEqual(len(calls), 2)


class ApiPhase3Tests(unittest.TestCase):
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

    def test_status_reports_modes(self):
        status = self.client.get("/status", headers=self.TOKEN).json()
        self.assertTrue(status["offline_guard"])
        self.assertEqual(set(status["modes"]), {"manual", "rules", "fast", "ai"})
        self.assertTrue(status["modes"]["rules"]["available"])
        with patch.object(llm, "model_info", return_value={"available": False, "problem": "Kein Sprachmodell"}):
            ai = self.client.get("/status", headers=self.TOKEN).json()["modes"]["ai"]
        self.assertFalse(ai["available"])
        self.assertIn("Kein Sprachmodell", ai["problem"])

    def test_cancel_running_analysis(self):
        doc = fitz.open()
        for i in range(20):
            doc.new_page().insert_text((72, 72), f"Seite {i}")
        data = doc.tobytes()
        original = self.main.pdf_processor.extract_page

        def slow(*args, **kwargs):
            time.sleep(0.1)
            return original(*args, **kwargs)

        with patch.object(self.main.pdf_processor, "extract_page", side_effect=slow):
            doc_id = self.client.post("/documents", headers=self.TOKEN, data={"mode": "manual"},
                                      files={"file": ("x.pdf", data, "application/pdf")}).json()["document_id"]
            time.sleep(0.3)
            self.client.post(f"/documents/{doc_id}/cancel", headers=self.TOKEN)
            for _ in range(100):
                progress = self.client.get(f"/documents/{doc_id}/progress", headers=self.TOKEN).json()
                if progress["state"] not in ("queued", "processing"):
                    break
                time.sleep(0.05)
        self.assertEqual(progress["state"], "cancelled")
        self.assertLess(progress["page"], 20)
        self.assertEqual(self.client.get(f"/documents/{doc_id}/marks", headers=self.TOKEN).status_code, 409)


@unittest.skipUnless(os.environ.get("RUN_LLM_TESTS") == "1" and llm._select_model(), "echtes Sprachmodell nur mit RUN_LLM_TESTS=1")
class RealModelTests(unittest.TestCase):
    def test_model_answers_in_format_and_sensibly(self):
        cases = [("Telefonnummer", "0800 222 3344", "Kostenlose Hotline der Versicherung: 0800 222 3344", False),
                 ("Datum", "07.08.1990", "Herr Brandt (geb. am 07.08.1990) zieht nach Kiel.", True)]
        right = 0
        for label, text, context, expected in cases:
            parsed = llm._parse_response(llm._ask(llm._get_llm(), llm.build_messages(context, {"label": label, "text": text})))
            self.assertIsNotNone(parsed)
            right += parsed["redact"] == expected
        self.assertGreaterEqual(right, 1)


if __name__ == "__main__":
    unittest.main()
