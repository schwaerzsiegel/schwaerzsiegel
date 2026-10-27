"""
Tests Phase 13: Corporate Branding erstellen (tools/branding/paket_erstellen.py).
Prüft Farbe, Logos, Ausgabe und dass die App das Ergebnis ohne Warnung lädt.
Nur synthetische Daten.

    python -m unittest -v test_phase13_branding
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("paket_erstellen", ROOT / "tools" / "branding" / "paket_erstellen.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"><rect width="120" height="40" fill="#1D5A8A"/></svg>'


class BrandingPaketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="branding-test-"))
        self.env_before = os.environ.get("REDACTION_BRANDING_DIR")

    def tearDown(self):
        self.assertEqual(os.environ.get("REDACTION_BRANDING_DIR"), self.env_before, "Umgebung nicht zurückgesetzt")

    def datei(self, name, inhalt):
        p = self.tmp / name
        p.write_text(inhalt, encoding="utf-8") if isinstance(inhalt, str) else p.write_bytes(inhalt)
        return p

    def run_tool(self, *extra):
        return tool.main(["--organisation", "Stadt Musterhausen", "--ziel", str(self.tmp / "aus"), *extra])

    def test_vollstaendiges_branding(self):
        logo = self.datei("logo.svg", SVG)
        dunkel = self.datei("hell.svg", SVG.replace("#1D5A8A", "#FFFFFF"))
        rc = self.run_tool("--farbe", "1d5a8a", "--logo", str(logo), "--logo-dunkel", str(dunkel),
                           "--support-name", "IT-Service", "--support-email", "it@example.org", "--zip")
        self.assertEqual(rc, 0)
        ordner = self.tmp / "aus" / "branding"
        config = json.loads((ordner / "branding.json").read_text(encoding="utf-8"))
        self.assertEqual(config["customerName"], "Stadt Musterhausen")
        self.assertEqual(config["accent"], "#1D5A8A")
        self.assertTrue(config["showPoweredBy"])
        self.assertNotIn("productName", config, "Produktname muss Schwärzsiegel bleiben")
        self.assertEqual((config["logo"], config["logoDark"]), ("logo.svg", "logo-dark.svg"))
        self.assertEqual(config["support"], {"name": "IT-Service", "email": "it@example.org"})
        self.assertIn("neben die setup.exe", (ordner / "LIESMICH.txt").read_text(encoding="utf-8").lower())
        with zipfile.ZipFile(self.tmp / "aus" / "Schwaerzsiegel-Branding-Stadt-Musterhausen.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["branding/LIESMICH.txt", "branding/branding.json",
                                                    "branding/logo-dark.svg", "branding/logo.svg"])

    def test_app_laedt_ergebnis_ohne_warnung(self):
        self.assertEqual(self.run_tool("--farbe", "#2F6B3B"), 0)
        os.environ["REDACTION_BRANDING_DIR"] = str(self.tmp / "aus" / "branding")
        try:
            from core import branding
            info = importlib.reload(branding).load()
        finally:
            if self.env_before is None:
                os.environ.pop("REDACTION_BRANDING_DIR", None)
            else:
                os.environ["REDACTION_BRANDING_DIR"] = self.env_before
            importlib.reload(branding)
        self.assertTrue(info["active"])
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["productName"], "Schwärzsiegel")
        self.assertEqual(info["accent"], "#2F6B3B")

    def test_zu_helle_farbe(self):
        with self.assertRaises(tool.Fehler) as ctx:
            tool.farbe_pruefen("#F5C400", darf_abdunkeln=False)
        self.assertIn("Vorschlag", str(ctx.exception))
        farbe, hinweise = tool.farbe_pruefen("#F5C400", darf_abdunkeln=True)
        self.assertGreaterEqual(tool.kontrast_zu_weiss(farbe), 4.5)
        self.assertTrue(hinweise)
        self.assertEqual(self.run_tool("--farbe", "#F5C400"), 1)

    def test_ungueltige_farbe(self):
        for bad in ("blau", "#12345", "#GGGGGG"):
            with self.assertRaises(tool.Fehler):
                tool.farbe_pruefen(bad, darf_abdunkeln=True)

    def test_unsichere_svg_abgelehnt(self):
        faelle = {
            "script.svg": SVG.replace("</svg>", "<script>alert(1)</script></svg>"),
            "onload.svg": SVG.replace("<rect", '<rect onload="x()"'),
            "extern.svg": SVG.replace("</svg>", '<image href="https://example.org/a.png"/></svg>'),
            "kaputt.svg": "<svg",
        }
        for name, inhalt in faelle.items():
            with self.subTest(name), self.assertRaises(tool.Fehler):
                tool.logo_pruefen(self.datei(name, inhalt))

    def test_png_logo(self):
        from PIL import Image
        ok = self.tmp / "ok.png"
        Image.new("RGBA", (300, 100), (29, 90, 138, 255)).save(ok)
        self.assertEqual(tool.logo_pruefen(ok), [])
        klein = self.tmp / "klein.png"
        Image.new("RGB", (60, 20)).save(klein)
        self.assertTrue(tool.logo_pruefen(klein), "Hinweis bei zu kleinem Logo erwartet")
        riesig = self.tmp / "riesig.png"
        Image.new("1", (5000, 10)).save(riesig)
        with self.assertRaises(tool.Fehler):
            tool.logo_pruefen(riesig)
        falsch = self.datei("falsch.png", b"kein bild")
        with self.assertRaises(tool.Fehler):
            tool.logo_pruefen(falsch)

    def test_installer_behaelt_branding_bei_update(self):
        hooks = (ROOT / "src-tauri" / "installer-hooks.nsh").read_text(encoding="utf-8")
        install = hooks.split("!macro NSIS_HOOK_POSTINSTALL", 1)[1].split("!macroend", 1)[0]
        # Branding wird nur ersetzt, wenn neben der setup.exe ein neues liegt
        guard = '${If} ${FileExists} "$EXEDIR\\branding\\branding.json"'
        self.assertIn(guard, install)
        before_guard = install.split(guard, 1)[0]
        self.assertNotIn("sidecar\\branding", before_guard, "Branding darf ohne neues Branding nicht gelöscht werden")


if __name__ == "__main__":
    unittest.main()
