"""
Phase 17: Fälle aus dem Auftrag „Erkennungsqualität“ (27.10.2026) – Messung vorher/nachher.

Jede Zeile wird einzeln als PDF-Zeile erkannt (wie in der App: Textebene → ner.detect). Erwartung je Fall:
  ("ja", Kategorie)  soll mit dieser Kategorie vorgeschlagen UND vorausgewählt werden
  ("nein", None)     soll gar nicht vorgeschlagen werden (auch nicht abgewählt in der Liste)
  ("möglich", Kat.)  darf vorgeschlagen werden, aber nicht vorausgewählt

    cd sidecar
    python eval/phase17_faelle.py --mode fast --out eval/results/phase17-faelle-vorher-fast.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import fitz  # noqa: E402

# (Zeile, gemeinter Teil, Erwartung, Kategorie) – alle Angaben erfunden
FAELLE = [
    ("Geburtsdatum: 12.05.1980", "12.05.1980", "ja", "Geburtsdatum"),
    ("Herr Seidel ist geboren am 12.05.1980.", "12.05.1980", "ja", "Geburtsdatum"),
    ("Herr Becker ist am 02.02.2024 verstorben.", "02.02.2024", "ja", "Sterbedatum"),
    ("Sterbedatum: 17.08.2023", "17.08.2023", "ja", "Sterbedatum"),
    ("Org.-Z. 1", "1", "ja", "Organisationszeichen"),
    ("Org.-Z. 1.1", "1.1", "ja", "Organisationszeichen"),
    ("Org.-Z. 131", "131", "ja", "Organisationszeichen"),
    ("Org.-Z. 131.01", "131.01", "ja", "Organisationszeichen"),
    ("Org.Z. 1211.01a", "1211.01a", "ja", "Organisationszeichen"),
    ("Org.-Zeichen: 311.345c", "311.345c", "ja", "Organisationszeichen"),
    ("Organisationskennzeichen: 12b", "12b", "ja", "Organisationszeichen"),
    ("Praxis Dr. Erika Mustermann", "Erika Mustermann", "ja", None),
    ("Malermeister Jonas Becker e.K.", "Jonas Becker", "ja", None),
    ("Dokument erstellt am 12.05.2025", "12.05.2025", "nein", None),
    ("Besprechung am 15.06.2025", "15.06.2025", "nein", None),
    ("Frist bis zum 31.12.2025", "31.12.2025", "nein", None),
    ("Kiel, 03.02.2026", "03.02.2026", "nein", None),
    ("Eingang: 07.01.2026", "07.01.2026", "nein", None),
    ("Siemens AG", "Siemens AG", "nein", None),
    ("Deutsche Bahn AG", "Deutsche Bahn AG", "nein", None),
    ("Stadtverwaltung Kiel", "Stadtverwaltung Kiel", "nein", None),
    ("Bundesagentur für Arbeit", "Bundesagentur für Arbeit", "nein", None),
    ("Muster GmbH", "Muster GmbH", "nein", None),
    ("Müller GmbH", "Müller GmbH", "nein", None),
    ("Die Abrechnung erfolgt nach Ziffer 131.01 der Gebührenordnung.", "131.01", "nein", None),
    ("Artikel 311.345c wurde geliefert.", "311.345c", "nein", None),
    ("Org.-Z. 131.01d", "131.01d", "nein", None),
    ("Frau Albrecht, 04.11.1971, beantragt Elternzeit.", "04.11.1971", "möglich", None),
]


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold().strip(" ,.;:")


def messen(mode: str) -> dict:
    from core import ner
    from core import pdf_processor as pdf

    ergebnisse = []
    for zeile, teil, erwartung, kategorie in FAELLE:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 80), zeile, fontsize=10, fontname="helv")
        data = doc.tobytes(); doc.close()
        with fitz.open(stream=data, filetype="pdf") as d:
            p = pdf.extract_page(d[0], 0, detect_images=False)
        vorschlaege = ner.detect(p.words, use_llm=mode == "ai", use_gliner=mode in ("fast", "ai"), llm_cache={})
        t = _norm(teil)
        passend = [s for s in vorschlaege if t in _norm(s["text"]) or _norm(s["text"]) in t]
        gefunden = [{"label": s["label"], "text": s["text"], "vorausgewaehlt": bool(s["default_selected"])}
                    for s in passend]
        if erwartung == "ja":
            ok = any(g["vorausgewaehlt"] and (kategorie is None or g["label"] == kategorie) and
                     t in _norm(g["text"]) for g in gefunden)
        elif erwartung == "nein":
            ok = not gefunden
        else:
            ok = not any(g["vorausgewaehlt"] for g in gefunden)
        ergebnisse.append({"zeile": zeile, "erwartung": erwartung, "kategorie": kategorie, "ok": ok,
                           "gefunden": gefunden})
    return {"mode": mode, "ok": sum(e["ok"] for e in ergebnisse), "faelle": len(ergebnisse),
            "ergebnisse": ergebnisse}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "fast", "ai"], default="fast")
    ap.add_argument("--out")
    args = ap.parse_args()
    e = messen(args.mode)
    for r in e["ergebnisse"]:
        got = "; ".join(f"{g['label']}{' (vorausgewählt)' if g['vorausgewaehlt'] else ''}: {g['text']}"
                        for g in r["gefunden"]) or "–"
        print(f"{'OK  ' if r['ok'] else 'FEHL'} [{r['erwartung']:<7}] {r['zeile']:<62} → {got}")
    print(f"\nModus {e['mode']}: {e['ok']} von {e['faelle']} Fällen wie gewünscht")
    if args.out:
        Path(args.out).write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
