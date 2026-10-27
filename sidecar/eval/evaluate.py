"""
Misst die Erkennungsqualität auf dem synthetischen Korpus (Phase 2).

    cd sidecar
    python eval/evaluate.py --mode rules            # nur Regeln (schnell, ohne KI)
    python eval/evaluate.py --mode fast             # Regeln + GLiNER (lokal, offline)
    python eval/evaluate.py --mode fast --seed 4711 --out eval/results/x.json

Zählweise (pro erwarteter Fundstelle, Überlappung auf Wortebene):
- Treffer (TP): eine Markierung überlappt die Stelle und hat die richtige Kategorie.
- Fehlklassifikation: überlappt, aber mit falscher Kategorie -> zählt als
  übersehen (FN) für die richtige und als Fehlalarm (FP) für die falsche Kategorie.
- Übersehen: keine Markierung überlappt die Stelle.
- Fehlalarm: Markierung ohne Überlappung mit einer erwarteten Stelle.
Markierungen der Kategorien Datum/Betrag/Organisation sind standardmäßig
nicht vorausgewählt und zählen nicht als Fehlalarm. "Abdeckung" = Anteil der
erwarteten Wörter, die eine vorausgewählte Markierung abdeckt (= würde
geschwärzt, wenn man alle vorausgewählten Vorschläge bestätigt).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import fitz  # noqa: E402

import corpus  # noqa: E402
from core import ner  # noqa: E402
from core import pdf_processor as pdf  # noqa: E402

# Beschriftungen der Erkennung -> Kategorien des Korpus (alte und neue Namen)
LABEL_TO_CATEGORY = {
    "Person": "person", "Adresse": "adresse", "Telefonnummer": "telefon", "E-Mail": "email",
    "Geburtsdatum": "geburtsdatum", "Geburtsort": "geburtsort", "Personalnummer": "personalnummer",
    "IBAN": "iban", "Bankverbindung": "bankverbindung", "Kontonummer": "bankverbindung",
    "Ausweisnummer": "ausweis", "Sozialversicherungsnummer": "sv_nummer", "Steuer-ID": "steuer_id",
    "Steuernummer": "steuer_id", "Krankenversichertennummer": "kv_nummer",
    "Gesundheitsdaten": "gesundheit", "Gesundheitsdaten (ICD-10)": "gesundheit",
}
NEUTRAL_LABELS = {"Datum", "Betrag", "Organisation", "Firmenanschrift", "Service-Rufnummer"}


def category_of(label: str) -> str:
    if label in NEUTRAL_LABELS:
        return "neutral"
    return LABEL_TO_CATEGORY.get(label, f"sonstige:{label}")


def build_pdf(doc: corpus.Document) -> bytes:
    out = fitz.open()
    page = out.new_page()
    y = 60
    for line in doc.lines:
        page.insert_text((50, y), line, fontsize=10, fontname="helv")
        y += 14
    data = out.tobytes()
    out.close()
    return data


def words_and_gold(doc: corpus.Document, page) -> tuple[list[dict], list[tuple[corpus.Gold, set[str]]]]:
    # Zeilen in Reihenfolge der y-Position; jede eingefügte Zeile = eine PDF-Zeile
    lines: dict[str, list[dict]] = defaultdict(list)
    for w in page.words:
        lines[w["line"]].append(w)
    ordered = sorted(lines.values(), key=lambda ws: ws[0]["bbox"][1])
    if len(ordered) != len(doc.lines):
        raise RuntimeError(f"{doc.name}: {len(ordered)} Zeilen extrahiert, {len(doc.lines)} erwartet")
    gold_words = []
    for g in doc.gold:
        line_words = ordered[g.line]
        pos, ids = 0, set()
        text = doc.lines[g.line]
        for w in line_words:
            start = text.find(w["text"], pos)
            end = start + len(w["text"])
            pos = end
            if start < g.start + len(g.text) and end > g.start:
                ids.add(w["id"])
        if not ids:
            raise RuntimeError(f"{doc.name}: '{g.text}' nicht gefunden")
        gold_words.append((g, ids))
    return page.words, gold_words


def evaluate(mode: str, seed: int, per_template: int, variants: bool = False, ambiguous: bool = False) -> dict:
    use_gliner = mode in ("fast", "ai")
    docs = corpus.generate(seed, per_template, variants, ambiguous)
    # Auswahl = was geschwärzt würde, wenn man alle vorausgewählten Vorschläge bestätigt
    sel_tp = sel_fn = sel_fp = 0
    selection_false = []
    tp, fn, fp = Counter(), Counter(), Counter()
    confusion = Counter()
    missed, false_alarms = [], []
    covered_words = total_words = 0
    start = time.perf_counter()

    for doc in docs:
        with fitz.open(stream=build_pdf(doc), filetype="pdf") as d:
            page = pdf.extract_page(d[0], 0, detect_images=False)
        words, gold = words_and_gold(doc, page)
        preds = ner.detect(words, use_llm=mode == "ai", use_gliner=use_gliner)
        for p in preds:
            p["_cat"] = category_of(p["label"])
            p["_ids"] = set(p.get("word_ids", []))

        relevant_gold = [(g, ids) for g, ids in gold if g.category != "-"]
        neutral_ids = set().union(*[ids for g, ids in gold if g.category == "-"]) if gold else set()

        for g, ids in relevant_gold:
            total_words += len(ids)
            selected = set().union(*[p["_ids"] for p in preds if p.get("default_selected")]) if preds else set()
            covered_words += len(ids & selected)
            overlapping = [p for p in preds if p["_ids"] & ids]
            if any(p["_cat"] == g.category for p in overlapping):
                tp[g.category] += 1
            elif overlapping:
                fn[g.category] += 1
                best = max(overlapping, key=lambda p: p["confidence"])
                confusion[(g.category, best["_cat"])] += 1
                if best["_cat"] != "neutral":
                    fp[best["_cat"]] += 1
                missed.append({"doc": doc.name, "text": g.text, "expected": g.category, "got": best["label"]})
            else:
                fn[g.category] += 1
                missed.append({"doc": doc.name, "text": g.text, "expected": g.category, "got": None})

        all_gold_ids = set().union(*[ids for _, ids in relevant_gold]) if relevant_gold else set()
        chosen = [p for p in preds if p.get("default_selected")]
        for g, ids in relevant_gold:
            if any(p["_ids"] & ids for p in chosen):
                sel_tp += 1
            else:
                sel_fn += 1
        for p in chosen:
            if not p["_ids"] & all_gold_ids and not p["_ids"] <= neutral_ids:
                sel_fp += 1
                selection_false.append({"doc": doc.name, "text": p["text"], "label": p["label"],
                                        "llm": p.get("llm_reason")})
        for p in preds:
            if p["_cat"] == "neutral" or p["_ids"] & all_gold_ids or p["_ids"] <= neutral_ids:
                continue
            fp[p["_cat"]] += 1
            false_alarms.append({"doc": doc.name, "text": p["text"], "label": p["label"],
                                 "confidence": round(p["confidence"], 2)})

    per_category = {}
    for cat in corpus.CATEGORIES:
        t, n, f = tp[cat], fn[cat], fp[cat]
        precision = t / (t + f) if t + f else None
        recall = t / (t + n) if t + n else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
        per_category[cat] = {"tp": t, "fn": n, "fp": f, "precision": precision, "recall": recall, "f1": f1}
    others = {k: v for k, v in fp.items() if k not in corpus.CATEGORIES}
    T, N = sum(tp.values()), sum(fn.values())
    F = sum(fp.values())
    precision = T / (T + F) if T + F else 0.0
    recall = T / (T + N) if T + N else 0.0
    return {
        "mode": mode, "seed": seed, "corpus": "mehrdeutig" if ambiguous else "varianten" if variants else "vorlagen", "documents": len(docs), "seconds": round(time.perf_counter() - start, 1),
        "micro": {"tp": T, "fn": N, "fp": F, "precision": precision, "recall": recall,
                  "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0},
        "coverage": covered_words / total_words if total_words else 0.0,
        "selection": _prf(sel_tp, sel_fn, sel_fp),
        "selection_false": selection_false,
        "per_category": per_category,
        "other_false_alarms": others,
        "confusion": {f"{a} -> {b}": c for (a, b), c in confusion.most_common()},
        "missed": missed,
        "false_alarms": false_alarms,
    }


def _prf(t, n, f) -> dict:
    p = t / (t + f) if t + f else 0.0
    r = t / (t + n) if t + n else 0.0
    return {"tp": t, "fn": n, "fp": f, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def fmt(v):
    return "–" if v is None else f"{v * 100:.0f} %"


def report(result: dict) -> str:
    lines = [
        f"Modus {result['mode']}, Korpus {result.get('corpus', 'vorlagen')}, Seed {result['seed']}, {result['documents']} Dokumente, {result['seconds']} s",
        f"Gesamt: Precision {fmt(result['micro']['precision'])}, Recall {fmt(result['micro']['recall'])}, "
        f"F1 {fmt(result['micro']['f1'])}, Abdeckung (vorausgewählt) {fmt(result['coverage'])}",
        f"Vorausgewählt (würde geschwärzt): Precision {fmt(result['selection']['precision'])}, "
        f"Recall {fmt(result['selection']['recall'])}, F1 {fmt(result['selection']['f1'])}, "
        f"falsch vorausgewählt {result['selection']['fp']}",
        "",
        f"{'Kategorie':<16}{'TP':>5}{'FN':>5}{'FP':>5}{'Precision':>11}{'Recall':>9}{'F1':>7}",
    ]
    for cat, m in result["per_category"].items():
        lines.append(f"{cat:<16}{m['tp']:>5}{m['fn']:>5}{m['fp']:>5}{fmt(m['precision']):>11}{fmt(m['recall']):>9}{fmt(m['f1']):>7}")
    if result["other_false_alarms"]:
        lines.append(f"Fehlalarme in sonstigen Kategorien: {result['other_false_alarms']}")
    lines.append("Fehlklassifikationen (erwartet -> erkannt): " + (", ".join(
        f"{k} ×{v}" for k, v in result["confusion"].items()) or "keine"))
    fa = Counter((x["label"], x["text"]) for x in result["false_alarms"])
    lines.append("Häufigste Fehlalarme: " + (", ".join(f"{l}: '{t}' ×{c}" for (l, t), c in fa.most_common(12)) or "keine"))
    ms = Counter((x["expected"], x["got"]) for x in result["missed"] if x["got"] is None)
    lines.append("Übersehen (Kategorie): " + (", ".join(f"{k} ×{c}" for (k, _), c in ms.most_common()) or "keine"))
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rules", "fast", "ai"], default="rules")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--per-template", type=int, default=10)
    parser.add_argument("--variants", action="store_true", help="anders formulierter Variantenkorpus")
    parser.add_argument("--ambiguous", action="store_true", help="Korpus mit mehrdeutigen Fällen (Phase 3)")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = evaluate(args.mode, args.seed, args.per_template, args.variants, args.ambiguous)
    print(report(result))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
