"""Erzeugt die Vorher/Nachher-Tabellen für den Bericht aus eval/results/*.json.

    python eval/compare.py > tabellen.md
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows-Konsole: Pfeile und Umlaute

RESULTS = Path(__file__).resolve().parent / "results"


def load(name):
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pct(v):
    return "–" if v is None else f"{v * 100:.0f} %"


print("## Gesamt (Micro-Durchschnitt)\n")
print("| Modus | Korpus | Seed | F1 vorher | F1 nachher | Precision vorher → nachher | Recall vorher → nachher | Abdeckung vorher → nachher | Fehlalarme vorher → nachher |")
print("|---|---|---|---|---|---|---|---|---|")
for mode, mode_name in (("rules", "Nur Regeln"), ("fast", "Regeln + GLiNER")):
    for corpus, corpus_name in (("vorlagen", "Vorlagen"), ("varianten", "Varianten")):
        for seed in (2026, 4711):
            before, after = load(f"vorher-{mode}-{corpus}-{seed}"), load(f"nachher-{mode}-{corpus}-{seed}")
            if not before or not after:
                continue
            b, a = before["micro"], after["micro"]
            print(f"| {mode_name} | {corpus_name} | {seed} | {pct(b['f1'])} | **{pct(a['f1'])}** | "
                  f"{pct(b['precision'])} → {pct(a['precision'])} | {pct(b['recall'])} → {pct(a['recall'])} | "
                  f"{pct(before['coverage'])} → {pct(after['coverage'])} | {b['fp']} → {a['fp']} |")

first = load("erste-runde-fast-varianten-2026")
if first:
    print(f"\nVariantenkorpus beim ersten, ungesehenen Durchlauf (Regeln + GLiNER, Seed 2026): "
          f"F1 {pct(first['micro']['f1'])}, Precision {pct(first['micro']['precision'])}, "
          f"Recall {pct(first['micro']['recall'])}.")

for mode, mode_name in (("fast", "Regeln + GLiNER"), ("rules", "Nur Regeln")):
    for corpus in ("vorlagen", "varianten"):
        before, after = load(f"vorher-{mode}-{corpus}-4711"), load(f"nachher-{mode}-{corpus}-4711")
        if not before or not after:
            continue
        print(f"\n## Je Kategorie – {mode_name}, {corpus.capitalize()}, Seed 4711\n")
        print("| Kategorie | Fundstellen | F1 vorher | F1 nachher | Übersehen vorher → nachher | Fehlalarme vorher → nachher |")
        print("|---|---|---|---|---|---|")
        for cat, a in after["per_category"].items():
            b = before["per_category"][cat]
            total = a["tp"] + a["fn"]
            if not total:
                continue
            print(f"| {cat} | {total} | {pct(b['f1'])} | {pct(a['f1'])} | {b['fn']} → {a['fn']} | {b['fp']} → {a['fp']} |")
        print(f"\nFehlklassifikationen vorher: {', '.join(f'{k} ×{v}' for k, v in before['confusion'].items()) or 'keine'}  ")
        print(f"Fehlklassifikationen nachher: {', '.join(f'{k} ×{v}' for k, v in after['confusion'].items()) or 'keine'}")
