"""
Phase 6: Lizenzhinweise der mitgelieferten Software und Modelle sammeln.

Erzeugt
- src/assets/drittanbieter.json   (Übersicht für den Info-Dialog)
- src-tauri/lizenzhinweise/DRITTANBIETER-LIZENZEN.txt  (mit Lizenztexten,
  wird in den Installationsordner gelegt)
- src-tauri/lizenzhinweise/schwaerzsiegel-sbom.cdx.json  (Phase 10:
  Bausteinliste im Standardformat CycloneDX 1.5, ebenfalls im Installationsordner)

Quellen (alles im Projektordner, keine Netzwerkzugriffe):
- Python-Pakete, die PyInstaller in den Sidecar gebündelt hat (laut
  sidecar/build und sidecar/dist - deshalb nach build-sidecar.cmd ausführen;
  Metadaten und Lizenzdateien aus der Projekt-.venv)
- JavaScript-Pakete, die in die Oberfläche gebündelt werden (node_modules)
- Rust-Crates der App-Hülle (cargo metadata --offline). Phase 10: Die
  Lizenztexte werden - mit ausdrücklicher Erlaubnis des Nutzers, nur lesend -
  aus dem Cargo-Zwischenspeicher (%USERPROFILE%\\.cargo\\registry) übernommen.
- Modelle und Programme: feste Liste unten (Angaben der Herausgeber)

    .venv\\Scripts\\python.exe tools\\lizenzhinweise.py
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_OUT = ROOT / "src" / "assets" / "drittanbieter.json"
TEXT_OUT = ROOT / "src-tauri" / "lizenzhinweise" / "DRITTANBIETER-LIZENZEN.txt"
SBOM_OUT = ROOT / "src-tauri" / "lizenzhinweise" / "schwaerzsiegel-sbom.cdx.json"
MANUFACTURER = "Cem Karatas"

# Werkzeuge, die nur zum Bauen dienen und nicht mit ausgeliefert werden
BUILD_ONLY = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
              "pywin32-ctypes", "packaging-legacy"}

COMPONENTS = [
    {"name": f"Python {sys.version_info.major}.{sys.version_info.minor}", "version": "", "license": "PSF-2.0",
     "note": "Laufzeit des Erkennungsdienstes"},
    {"name": "GLiNER multi v2.1 (urchade/gliner_multi-v2.1)", "version": "", "license": "Apache-2.0",
     "note": "Erkennungsmodell"},
    {"name": "mDeBERTa-v3-base (microsoft/mdeberta-v3-base)", "version": "", "license": "MIT",
     "note": "Grundmodell von GLiNER"},
    {"name": "Qwen3-1.7B (GGUF, Q4_K_M)", "version": "", "license": "Apache-2.0", "note": "Sprachmodell (Gründliche KI)"},
    {"name": "Tesseract OCR", "version": "", "license": "Apache-2.0", "note": "Texterkennung für Scans"},
    {"name": "tessdata_fast – deu.traineddata", "version": "", "license": "Apache-2.0", "note": "Sprachpaket Deutsch"},
    {"name": "MuPDF (über PyMuPDF)", "version": "", "license": "AGPL-3.0-or-later",
     "note": "PDF-Verarbeitung"},
    {"name": "llama.cpp (über llama-cpp-python)", "version": "", "license": "MIT", "note": "Ausführung des Sprachmodells"},
    {"name": "IBM Plex Sans / IBM Plex Mono", "version": "", "license": "OFL-1.1", "note": "Schriftarten der Oberfläche"},
    {"name": "Microsoft Edge WebView2", "version": "", "license": "Microsoft-Software-Lizenz",
     "note": "Bestandteil von Windows, nicht mitgeliefert"},
]

SIDECAR_BUILD = ROOT / "sidecar" / "build" / "redaction-sidecar"
SIDECAR_DIST = ROOT / "sidecar" / "dist" / "redaction-sidecar" / "_internal"

LICENSE_FILE_HINTS = ("license", "licence", "copying", "notice", "authors")


def _short(text: str | None) -> str:
    text = (text or "").strip()
    if not text or "\n" in text or len(text) > 200:
        return ""
    return text


def bundled_python_distributions() -> set[str]:
    """Namen der Pakete, deren Module tatsächlich im gebauten Sidecar stecken."""
    toc = SIDECAR_BUILD / "PYZ-00.toc"
    if not toc.is_file() or not SIDECAR_DIST.is_dir():
        raise SystemExit("Kein gebauter Sidecar gefunden - zuerst build-sidecar.cmd ausführen.")
    top = set(re.findall(r"\('([A-Za-z0-9_]+)[.']", toc.read_text(encoding="utf-8", errors="replace")))
    top |= {p.name.split(".")[0] for p in SIDECAR_DIST.iterdir()}
    owners = md.packages_distributions()
    return {d.lower() for name in top for d in owners.get(name, [])}


def python_packages() -> tuple[list[dict], dict[str, str]]:
    items, texts = [], {}
    bundled = bundled_python_distributions()
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in BUILD_ONLY or name.lower() not in bundled:
            continue
        meta = dist.metadata
        lic = _short(meta.get("License-Expression")) or _short(meta.get("License"))
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
            lic = ", ".join(c for c in classifiers if c != "OSI Approved") or "siehe Lizenztext"
        items.append({"name": name, "version": dist.version, "license": lic})
        parts = []
        for f in dist.files or []:
            if any(h in f.name.lower() for h in LICENSE_FILE_HINTS) and f.suffix.lower() in ("", ".txt", ".md", ".rst"):
                try:
                    content = Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue
                if content and len(content) < 200_000:
                    parts.append(content)
        if parts:
            texts[f"{name} {dist.version}"] = "\n\n".join(dict.fromkeys(parts))
    items.sort(key=lambda i: i["name"].lower())
    return items, texts


def js_packages() -> tuple[list[dict], dict[str, str]]:
    root = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    todo, seen, items, texts = list(root.get("dependencies", {})), set(), [], {}
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        folder = ROOT / "node_modules" / name
        pkg = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        lic = pkg.get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
        items.append({"name": name, "version": pkg.get("version", ""), "license": lic or "siehe Lizenztext"})
        todo += list(pkg.get("dependencies", {}))
        files = [p for p in folder.iterdir() if p.is_file() and any(p.name.lower().startswith(h) for h in LICENSE_FILE_HINTS)]
        if files:
            texts[f"{name} {pkg.get('version', '')}"] = "\n\n".join(p.read_text(encoding="utf-8", errors="replace").strip()
                                                                    for p in sorted(files))
    items.sort(key=lambda i: i["name"].lower())
    return items, texts


def rust_crates() -> tuple[list[dict], dict[str, str]]:
    out = subprocess.run(["cargo", "metadata", "--format-version", "1", "--offline", "--filter-platform",
                          "x86_64-pc-windows-msvc", "--manifest-path", str(ROOT / "src-tauri" / "Cargo.toml")],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    meta = json.loads(out.stdout)
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    # nur, was in die App eingebaut wird: normale Abhängigkeiten ab der App
    todo, seen = [root], set()
    while todo:
        node = nodes[todo.pop()]
        for dep in node["deps"]:
            if any(k["kind"] is None for k in dep["dep_kinds"]) and dep["pkg"] not in seen:
                seen.add(dep["pkg"])
                todo.append(dep["pkg"])
    items, texts = [], {}
    for i in seen:
        pkg = packages[i]
        lic = (pkg.get("license") or "siehe Crate").replace("/", " OR ")
        items.append({"name": pkg["name"], "version": pkg["version"], "license": lic})
        folder = Path(pkg["manifest_path"]).parent
        files = [folder / pkg["license_file"]] if pkg.get("license_file") else []
        files += [f for f in folder.iterdir() if f.is_file()
                  and any(f.name.lower().startswith(h) for h in LICENSE_FILE_HINTS) and f not in files]
        parts = []
        for f in sorted(files):
            try:
                content = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if content and len(content) < 200_000:
                parts.append(content)
        if parts:
            texts[f"{pkg['name']} {pkg['version']}"] = "\n\n".join(dict.fromkeys(parts))
    items.sort(key=lambda i: (i["name"], i["version"]))
    return items, texts


def grouped(texts: dict[str, str]) -> list[tuple[list[str], str]]:
    """Gleiche Lizenztexte nur einmal aufführen (viele Rust-Crates tragen
    wortgleiche MIT- und Apache-Texte), mit allen Paketen, für die sie gelten."""
    by_text: dict[str, list[str]] = {}
    for name in sorted(texts, key=str.lower):
        by_text.setdefault(texts[name], []).append(name)
    return sorted(((names, text) for text, names in by_text.items()), key=lambda x: x[0][0].lower())


# SPDX-Kennungen, die in den Paketangaben vorkommen (https://spdx.org/licenses/)
_SPDX_IDS = {"0BSD", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0",
             "CNRI-Python", "ISC", "LLVM-exception", "MIT", "MIT-0", "MIT-CMU", "MPL-2.0", "OFL-1.1", "PSF-2.0",
             "Unicode-3.0", "Unlicense", "Zlib"}
# Eindeutige Freitext-Angaben einiger Python-Pakete
_SPDX_ALIASES = {"MIT License": "MIT", "Apache 2.0": "Apache-2.0", "Apache License 2.0": "Apache-2.0",
                 "Apache 2.0 License": "Apache-2.0", "3-Clause BSD License": "BSD-3-Clause"}


def _cdx_licenses(lic: str) -> list[dict]:
    """Lizenzangabe als SPDX-Kennung bzw. -Ausdruck, sonst als freier Name
    (z. B. "Microsoft-Software-Lizenz" oder "BSD License" ohne Variante)."""
    lic = " ".join((lic or "").split())
    lic = _SPDX_ALIASES.get(lic, lic)
    tokens = [t for t in re.split(r"[\s()]+", lic) if t]
    if tokens and all(t in _SPDX_IDS or t in ("AND", "OR", "WITH") for t in tokens) and lic.count("(") == lic.count(")"):
        return [{"license": {"id": lic}}] if len(tokens) == 1 else [{"expression": lic}]
    return [{"license": {"name": lic}}]


def sbom(groups: dict[str, list[dict]]) -> dict:
    """Phase 10: Bausteinliste nach CycloneDX 1.5 (JSON) - für Behörden und
    für die technische Dokumentation nach dem Cyber Resilience Act."""
    import uuid
    from datetime import datetime, timezone

    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    components = []
    for eco, items in groups.items():
        for i in items:
            comp = {"type": "library", "name": i["name"]}
            if i.get("version"):
                comp["version"] = i["version"]
            if eco == "application":
                note = i.get("note", "")
                comp["type"] = ("machine-learning-model" if "modell" in note.lower() and "Sprachpaket" not in note
                                else "data" if "Sprachpaket" in note or "Schrift" in note else "application")
                comp["description"] = note
            else:
                name = i["name"].lower() if eco == "pypi" else i["name"]
                comp["purl"] = f"pkg:{eco}/{name}@{i['version']}"
            comp["licenses"] = _cdx_licenses(i["license"])
            comp["bom-ref"] = f"{eco}:{i['name']}@{i.get('version', '')}"
            components.append(comp)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "tools": {"components": [{"type": "application", "name": "tools/lizenzhinweise.py"}]},
            "component": {"type": "application", "bom-ref": "schwaerzsiegel", "name": "Schwärzsiegel",
                          "version": version, "supplier": {"name": MANUFACTURER},
                          "description": "Lokale Schwärzung personenbezogener Daten in PDFs (Windows)"},
            "manufacture": {"name": MANUFACTURER},
        },
        "components": components,
    }


def main() -> int:
    py, py_texts = python_packages()
    js, js_texts = js_packages()
    rs, rs_texts = rust_crates()
    groups = [
        {"title": "Modelle und Programme", "items": COMPONENTS},
        {"title": "Python-Pakete (Erkennungsdienst)", "items": py},
        {"title": "JavaScript-Pakete (Oberfläche)", "items": js},
        {"title": "Rust-Crates (App-Hülle)", "items": rs},
    ]
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps({"generated": date.today().isoformat(), "groups": groups}, ensure_ascii=False,
                                   separators=(",", ":")) + "\n", encoding="utf-8")

    lines = ["Schwärzsiegel – Lizenzen mitgelieferter Software und Modelle",
             f"Stand: {date.today():%d.%m.%Y} (erzeugt mit tools/lizenzhinweise.py)", ""]
    for g in groups:
        lines += ["=" * 78, g["title"], "=" * 78]
        for i in g["items"]:
            version = f" {i['version']}" if i["version"] else ""
            note = f" – {i['note']}" if i.get("note") else ""
            lines.append(f"{i['name']}{version}: {i['license']}{note}")
        lines.append("")
    fonts = {"IBM Plex Sans / IBM Plex Mono": (ROOT / "src" / "assets" / "fonts" / "OFL-IBM-Plex.txt").read_text(
        encoding="utf-8", errors="replace").strip()}
    for title, texts in (("Lizenztexte der Python-Pakete", py_texts), ("Lizenztexte der JavaScript-Pakete", js_texts),
                         ("Lizenztext der Schriftarten", fonts)):
        lines += ["=" * 78, title, "=" * 78, ""]
        for name in sorted(texts, key=str.lower):
            lines += ["-" * 78, name, "-" * 78, texts[name], ""]
    lines += ["=" * 78, "Lizenztexte der Rust-Crates (gleiche Texte zusammengefasst)", "=" * 78, ""]
    for names, text in grouped(rs_texts):
        lines += ["-" * 78, "Gilt für: " + ", ".join(names), "-" * 78, text, ""]
    missing = sorted(f"{i['name']} {i['version']}" for i in rs if f"{i['name']} {i['version']}" not in rs_texts)
    if missing:
        lines += ["-" * 78, "Ohne eigene Lizenzdatei im Paket (es gilt die Lizenzangabe oben): " + ", ".join(missing), ""]
    TEXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    TEXT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SBOM_OUT.write_text(json.dumps(sbom({"application": COMPONENTS, "pypi": py, "npm": js, "cargo": rs}),
                                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(py)} Python-Pakete ({len(py_texts)} mit Lizenztext), {len(js)} JavaScript-Pakete "
          f"({len(js_texts)} mit Lizenztext), {len(rs)} Rust-Crates ({len(rs_texts)} mit Lizenztext, "
          f"{len(missing)} ohne: {', '.join(missing) or '-'})")
    print(f"-> {JSON_OUT.relative_to(ROOT)}\n-> {TEXT_OUT.relative_to(ROOT)}\n-> {SBOM_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
