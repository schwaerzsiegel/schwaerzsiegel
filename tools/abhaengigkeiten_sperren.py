"""
Phase 12: Feste Paketliste für reproduzierbare Builds (auch im Cloud-Build).

Liest sidecar/requirements.txt (plus die Bauwerkzeuge PyInstaller) und löst daraus aus der aktuellen
Python-Umgebung alle tatsächlich benötigten Pakete mit ihren **exakten** Versionen auf – nur das, was
die App braucht, nicht alles, was zufällig installiert ist. Ergebnis: sidecar/requirements-lock.txt

    .venv\\Scripts\\python.exe tools\\abhaengigkeiten_sperren.py

Nach jeder Änderung an requirements.txt (und dem Installieren der neuen Versionen) neu ausführen und
die Datei mit sichern.
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "sidecar" / "requirements.txt"
LOCK = ROOT / "sidecar" / "requirements-lock.txt"
BAUWERKZEUGE = ["pyinstaller", "pyinstaller-hooks-contrib"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    umgebung = default_environment()
    start = [Requirement(z.split("#")[0].strip()) for z in REQ.read_text(encoding="utf-8").splitlines()
             if z.split("#")[0].strip()]
    start += [Requirement(n) for n in BAUWERKZEUGE]
    offen = [(r, ()) for r in start]
    gefunden: dict[str, str] = {}
    while offen:
        req, extras = offen.pop()
        name = canonicalize_name(req.name)
        if name in gefunden:
            continue
        try:
            dist = md.distribution(req.name)
        except md.PackageNotFoundError:
            print(f"FEHLER: {req.name} ist nicht installiert.")
            return 1
        if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
            print(f"FEHLER: {req.name} {dist.version} passt nicht zu {req.specifier}")
            return 1
        gefunden[name] = dist.version
        for sub in dist.requires or []:
            r = Requirement(sub)
            if r.marker is None or any(r.marker.evaluate({**umgebung, "extra": e}) for e in (req.extras or {""})):
                offen.append((r, tuple(r.extras)))
    zeilen = [
        "# Erzeugt mit tools/abhaengigkeiten_sperren.py – nicht von Hand ändern.",
        f"# Python {sys.version.split()[0]}, Windows x64. Grundlage: sidecar/requirements.txt + PyInstaller.",
        "# llama-cpp-python gibt es nur als Quellcode; es wird beim Installieren übersetzt (siehe Build-Ablauf).",
    ]
    zeilen += [f"{n}=={v}" for n, v in sorted(gefunden.items())]
    LOCK.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    print(f"{len(gefunden)} Pakete -> {LOCK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
