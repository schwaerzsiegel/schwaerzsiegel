"""
Phase 13: Corporate Branding für einen Kunden erstellen.

Aus Logo, Hausfarbe und Name der Organisation entsteht ein fertiger Ordner
`branding/` (branding.json, Logos, LIESMICH.txt) und auf Wunsch eine ZIP-Datei.
Die IT des Kunden legt den Ordner neben die setup.exe; der Installer übernimmt
ihn (src-tauri/installer-hooks.nsh) und behält ihn bei späteren Updates.

Bewusst fest (Entscheidung 24.10.2026): Der Produktname bleibt „Schwärzsiegel“,
der Hinweis „bereitgestellt mit Schwärzsiegel“ bleibt sichtbar. Es gibt nur
eine Hauptfarbe; alle Farbtöne für Hell und Dunkel berechnet die App.

Aufruf (im Projektordner):
    .venv\\Scripts\\python.exe tools\\branding\\paket_erstellen.py ^
        --organisation "Stadt Musterhausen" --farbe "#1D5A8A" ^
        --logo logo.svg [--logo-dunkel logo-hell-auf-dunkel.svg] ^
        [--support-name "IT-Service" --support-email it@example.org ^
         --support-telefon "0000 000000" --support-url https://... --support-text "..."] ^
        [--ziel ausgabe\\branding-musterhausen] [--zip] [--abdunkeln]

Rückgabewert 0 = fertig und geprüft, 1 = Eingaben nicht verwendbar.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "sidecar"

MIN_CONTRAST = 4.5          # weiße Schrift auf der Hausfarbe (WCAG AA), wie core/branding.py
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_TEXT = {"organisation": 80, "name": 120, "email": 120, "telefon": 60, "url": 200, "text": 600}
PNG_MIN_HEIGHT = 64
PNG_MAX_SIDE = 4096


class Fehler(Exception):
    """Eingabe nicht verwendbar – Meldung ist für Menschen geschrieben."""


# --- Farbe -----------------------------------------------------------------

def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def kontrast_zu_weiss(hex_color: str) -> float:
    return 1.05 / (_luminance(hex_color) + 0.05)


def abdunkeln(hex_color: str) -> str:
    """Hausfarbe schrittweise abdunkeln, bis weiße Schrift 4,5:1 erreicht."""
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    for step in range(1, 101):
        f = 1 - step / 100
        candidate = "#" + "".join(f"{round(v * f):02X}" for v in rgb)
        if kontrast_zu_weiss(candidate) >= MIN_CONTRAST:
            return candidate
    return "#000000"


def farbe_pruefen(farbe: str, darf_abdunkeln: bool) -> tuple[str, list[str]]:
    hinweise: list[str] = []
    farbe = farbe.strip()
    if re.fullmatch(r"#?[0-9A-Fa-f]{6}", farbe) is None:
        raise Fehler(f"Farbe „{farbe}“: bitte im Format #RRGGBB angeben, z. B. #1D5A8A.")
    farbe = "#" + farbe.lstrip("#").upper()
    k = kontrast_zu_weiss(farbe)
    if k >= MIN_CONTRAST:
        return farbe, hinweise
    vorschlag = abdunkeln(farbe)
    if not darf_abdunkeln:
        raise Fehler(
            f"Farbe {farbe} ist zu hell für weiße Schrift (Kontrast {k:.1f}:1, nötig {MIN_CONTRAST}:1).\n"
            f"  Vorschlag: {vorschlag} (gleicher Farbton, dunkler). Mit --abdunkeln automatisch übernehmen."
        )
    hinweise.append(f"Farbe {farbe} war zu hell (Kontrast {k:.1f}:1) und wurde auf {vorschlag} abgedunkelt.")
    return vorschlag, hinweise


# --- Logos -----------------------------------------------------------------

def _svg_pruefen(path: Path) -> list[str]:
    hinweise: list[str] = []
    try:
        root = ET.fromstring(path.read_bytes())
    except ET.ParseError as e:
        raise Fehler(f"Logo {path.name}: keine gültige SVG-Datei ({e}).")
    if not root.tag.endswith("svg"):
        raise Fehler(f"Logo {path.name}: keine SVG-Grafik.")
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise Fehler(f"Logo {path.name}: enthält <{tag}> – nur reine Grafik ist erlaubt.")
        for name, value in el.attrib.items():
            local = name.rsplit("}", 1)[-1].lower()
            if local.startswith("on"):
                raise Fehler(f"Logo {path.name}: enthält Skript-Attribut {local} – nur reine Grafik ist erlaubt.")
            if local == "href" and not value.startswith(("#", "data:image/")):
                raise Fehler(f"Logo {path.name}: verweist auf externe Datei „{value[:60]}“ – bitte alles einbetten.")
    if "viewBox" not in root.attrib:
        hinweise.append(f"Logo {path.name}: ohne viewBox – Darstellung in der Kopfzeile bitte prüfen.")
    return hinweise


def _png_pruefen(path: Path) -> list[str]:
    from PIL import Image
    hinweise: list[str] = []
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            breite, hoehe = img.size
            if img.format != "PNG":
                raise Fehler(f"Logo {path.name}: keine PNG-Datei.")
    except Fehler:
        raise
    except Exception as e:
        raise Fehler(f"Logo {path.name}: Bild nicht lesbar ({e}).")
    if max(breite, hoehe) > PNG_MAX_SIDE:
        raise Fehler(f"Logo {path.name}: {breite} × {hoehe} Pixel ist zu groß (höchstens {PNG_MAX_SIDE}).")
    if hoehe < PNG_MIN_HEIGHT:
        hinweise.append(f"Logo {path.name}: nur {hoehe} Pixel hoch – kann unscharf wirken (empfohlen ab {PNG_MIN_HEIGHT}).")
    return hinweise


def logo_pruefen(path: Path) -> list[str]:
    if not path.is_file():
        raise Fehler(f"Logo {path}: Datei nicht gefunden.")
    endung = path.suffix.lower()
    if endung not in {".svg", ".png"}:
        raise Fehler(f"Logo {path.name}: nur SVG oder PNG (empfohlen: SVG).")
    if path.stat().st_size > MAX_LOGO_BYTES:
        raise Fehler(f"Logo {path.name}: größer als 2 MB.")
    return _svg_pruefen(path) if endung == ".svg" else _png_pruefen(path)


# --- Texte -----------------------------------------------------------------

def text(value: str | None, feld: str) -> str | None:
    if value is None:
        return None
    clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    if len(clean) > MAX_TEXT[feld]:
        raise Fehler(f"{feld}: höchstens {MAX_TEXT[feld]} Zeichen (sind {len(clean)}).")
    return clean or None


# --- Erstellen -------------------------------------------------------------

LIESMICH = """Corporate Branding für Schwärzsiegel – {organisation}
{linie}

Inhalt dieses Ordners
  branding.json   Name, Hausfarbe {farbe}, Logo-Angaben{support_zeile}
  {logos}

Neu installieren oder aktualisieren
  1. Diesen Ordner „branding“ unverändert NEBEN die setup.exe legen
     (gleiche Ebene wie der Ordner „models“).
  2. setup.exe starten (oder still verteilen: setup.exe /S).
  Der Installer übernimmt das Branding. Bei späteren Updates OHNE diesen
  Ordner bleibt das vorhandene Branding erhalten.

Schon installiert, nur Branding nachrüsten
  Den Inhalt dieses Ordners kopieren nach
    <Installationsordner>\\sidecar\\branding\\
  (Standard: %LOCALAPPDATA%\\Schwärzsiegel\\sidecar\\branding\\)
  und Schwärzsiegel neu starten.

Hinweise
  - Der Produktname „Schwärzsiegel“ und die Lizenzhinweise (AGPL) im
    Info-Fenster bleiben sichtbar.
  - Geschwärzte PDFs enthalten kein Branding.
  - Ist eine Datei beschädigt, startet die App im Standarddesign; der Grund
    steht im Protokoll.
"""


def erstellen(args: argparse.Namespace) -> tuple[Path, list[str]]:
    organisation = text(args.organisation, "organisation")
    if not organisation:
        raise Fehler("Bitte den Namen der Organisation angeben (--organisation).")
    farbe, hinweise = farbe_pruefen(args.farbe, args.abdunkeln)

    logos: dict[str, Path] = {}
    if args.logo:
        hinweise += logo_pruefen(Path(args.logo))
        logos["logo"] = Path(args.logo)
    if args.logo_dunkel:
        if not args.logo:
            raise Fehler("--logo-dunkel nur zusammen mit --logo.")
        hinweise += logo_pruefen(Path(args.logo_dunkel))
        logos["logoDark"] = Path(args.logo_dunkel)

    support = {
        "name": text(args.support_name, "name"),
        "email": text(args.support_email, "email"),
        "phone": text(args.support_telefon, "telefon"),
        "url": text(args.support_url, "url"),
        "text": text(args.support_text, "text"),
    }
    if support["url"] and not re.match(r"https?://", support["url"]):
        raise Fehler("--support-url: nur Adressen mit http:// oder https://.")
    if support["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", support["email"]):
        raise Fehler(f"--support-email: „{support['email']}“ ist keine E-Mail-Adresse.")
    support = {k: v for k, v in support.items() if v}

    ziel = Path(args.ziel) if args.ziel else ROOT / "ausgabe" / ("branding-" + slug(organisation))
    ordner = ziel / "branding"
    if ordner.exists():
        shutil.rmtree(ordner)
    ordner.mkdir(parents=True)

    config: dict = {"customerName": organisation, "accent": farbe, "showPoweredBy": True}
    namen = {"logo": "logo", "logoDark": "logo-dark"}
    for key, quelle in logos.items():
        name = namen[key] + quelle.suffix.lower()
        shutil.copyfile(quelle, ordner / name)
        config[key] = name
    if support:
        config["support"] = support
    (ordner / "branding.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (ordner / "LIESMICH.txt").write_text(LIESMICH.format(
        organisation=organisation,
        linie="=" * (len("Corporate Branding für Schwärzsiegel – ") + len(organisation)),
        farbe=farbe,
        support_zeile=", Support-Kontakt" if support else "",
        logos="  ".join(config[k] for k in ("logo", "logoDark") if k in config) or "(kein Logo)",
    ), encoding="utf-8")

    hinweise += gegenpruefen(ordner)
    if args.zip:
        archiv = ziel / f"Schwaerzsiegel-Branding-{slug(organisation)}.zip"
        with zipfile.ZipFile(archiv, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(ordner.iterdir()):
                z.write(f, f"branding/{f.name}")
    return ordner, hinweise


def gegenpruefen(ordner: Path) -> list[str]:
    """Den fertigen Ordner so laden, wie die App es tut (sidecar/core/branding.py)."""
    if str(SIDECAR) not in sys.path:
        sys.path.insert(0, str(SIDECAR))
    vorher = os.environ.get("REDACTION_BRANDING_DIR")
    os.environ["REDACTION_BRANDING_DIR"] = str(ordner)
    try:
        from core import branding
        info = importlib.reload(branding).load()
    finally:
        # Umgebung wiederherstellen (wichtig, wenn Tests im selben Prozess laufen)
        if vorher is None:
            os.environ.pop("REDACTION_BRANDING_DIR", None)
        else:
            os.environ["REDACTION_BRANDING_DIR"] = vorher
        importlib.reload(branding)
    if info["warnings"]:
        raise Fehler("Die App würde das Branding so nicht übernehmen: " + "; ".join(info["warnings"]))
    if info["productName"] != "Schwärzsiegel" or not info["showPoweredBy"]:
        raise Fehler("Produktname oder Hinweis „bereitgestellt mit Schwärzsiegel“ verändert.")
    return []


def slug(name: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
    return re.sub(r"[^A-Za-z0-9]+", "-", name.translate(table)).strip("-")[:60] or "kunde"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Corporate Branding für Schwärzsiegel erstellen und prüfen.")
    p.add_argument("--organisation", required=True, help="Name der Organisation (Kopfzeile, Fenstertitel)")
    p.add_argument("--farbe", required=True, help="Hausfarbe als #RRGGBB")
    p.add_argument("--logo", help="Logo für helle Darstellung (SVG oder PNG)")
    p.add_argument("--logo-dunkel", help="Logo für dunkle Darstellung (optional)")
    p.add_argument("--support-name")
    p.add_argument("--support-email")
    p.add_argument("--support-telefon")
    p.add_argument("--support-url")
    p.add_argument("--support-text")
    p.add_argument("--ziel", help="Ausgabeordner (Standard: ausgabe/branding-<organisation>)")
    p.add_argument("--zip", action="store_true", help="zusätzlich eine ZIP-Datei erstellen")
    p.add_argument("--abdunkeln", action="store_true", help="zu helle Farbe automatisch abdunkeln")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        ordner, hinweise = erstellen(args)
    except Fehler as e:
        print(f"Nicht erstellt: {e}")
        return 1
    print(f"Branding erstellt und geprüft: {ordner}")
    for h in hinweise:
        print(f"  Hinweis: {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
