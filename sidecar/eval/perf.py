"""
Leistungsmessung der Analyse-Modi (Phase 3): Ladezeit, Arbeitsspeicher,
CPU-Last und Dauer bei unterschiedlich großen Dokumenten (synthetisch).

    cd sidecar
    python eval/perf.py                 # alle Modi, je eigener Prozess
    python eval/perf.py --mode fast --pages 10 100

Gemessen wird die echte Pipeline (Vorbereitung, Textextraktion, Erkennung,
ggf. Sprachmodell) ohne Web-Server.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))


def build_pdf(pages: int, ambiguous: bool, long_pages: bool = False) -> bytes:
    import fitz
    import corpus
    docs = corpus.generate(99, per_template=max(1, pages // 5 + 1), ambiguous=ambiguous)
    out = fitz.open()
    filler = ("Der Bericht beschreibt den Ablauf der Besprechung und die vereinbarten nächsten Schritte "
              "im Detail, damit alle Beteiligten informiert sind. ")
    for i in range(pages):
        doc = docs[i % len(docs)]
        page = out.new_page()
        lines = list(doc.lines)
        if long_pages:  # volle Textseite: ca. 600 Wörter
            lines = lines[:2] + [filler] * 40 + lines[2:]
        y = 40
        for line in lines:
            page.insert_textbox(fitz.Rect(40, y, 560, y + 60), line, fontsize=8)
            y += 13 if not long_pages or line != filler else 12
            if y > 800:
                break
    data = out.tobytes()
    out.close()
    return data


class PeakMemory:
    def __init__(self):
        import psutil
        self.proc = psutil.Process()
        self.peak = self.proc.memory_info().rss
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.05)

    def stop(self) -> float:
        self._stop.set()
        return max(self.peak, self.proc.memory_info().rss) / 1e9


def run_mode(mode: str, page_counts: list[int]) -> dict:
    import fitz
    import psutil
    from core import ner, pdf_processor, redaction

    proc = psutil.Process()
    result = {"mode": mode, "cores": os.cpu_count(), "base_rss_gb": round(proc.memory_info().rss / 1e9, 2),
              "runs": []}
    if mode in ("fast", "ai"):
        t = time.perf_counter()
        ner.load_model()
        result["gliner_load_s"] = round(time.perf_counter() - t, 1)
    if mode == "ai":
        from core import llm
        t = time.perf_counter()
        result["llm_device"] = llm.load_model()
        result["llm_load_s"] = round(time.perf_counter() - t, 1)
    result["loaded_rss_gb"] = round(proc.memory_info().rss / 1e9, 2)

    cases = [(n, False) for n in page_counts]
    if mode == "fast":
        cases.append((5, True))  # volle Textseiten (Abschnitte für GLiNER)
    for pages, long_pages in cases:
        data = build_pdf(pages, ambiguous=mode == "ai", long_pages=long_pages)
        peak = PeakMemory()
        wall, cpu = time.perf_counter(), time.process_time()
        llm_calls = 0
        data, _ = redaction.prepare_document(data)
        cache: dict = {}
        with fitz.open(stream=data, filetype="pdf") as doc:
            words_total = 0
            for i, pdf_page in enumerate(doc):
                page = pdf_processor.extract_page(pdf_page, i)
                words_total += len(page.words)
                if mode != "manual":
                    page.suggestions.extend(ner.detect(page.words, use_llm=mode == "ai", use_gliner=mode != "rules",
                                                       llm_cache=cache))
        llm_calls = len(cache)
        wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
        result["runs"].append({
            "pages": pages, "full_text_pages": long_pages, "words": words_total,
            "seconds": round(wall, 2), "seconds_per_page": round(wall / pages, 2),
            "cpu_load_percent": round(100 * cpu / wall / os.cpu_count()),
            "peak_rss_gb": round(peak.stop(), 2), "llm_calls": llm_calls,
        })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rules", "fast", "ai"])
    parser.add_argument("--pages", type=int, nargs="*")
    parser.add_argument("--out", default=str(HERE / "results" / "leistung.json"))
    args = parser.parse_args()
    if args.mode:
        print(json.dumps(run_mode(args.mode, args.pages or [1, 10, 100])))
        return
    plan = {"rules": [1, 10, 100], "fast": [1, 10, 100], "ai": [1, 10]}
    results = []
    for mode, pages in plan.items():
        out = subprocess.run([sys.executable, __file__, "--mode", mode, "--pages", *map(str, pages)],
                             capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        line = [l for l in out.stdout.splitlines() if l.startswith("{")]
        if not line:
            print(out.stderr[-3000:])
            raise SystemExit(f"Messung {mode} fehlgeschlagen")
        results.append(json.loads(line[-1]))
        r = results[-1]
        print(f"\n{mode}: Grundbedarf {r['base_rss_gb']} GB, nach Modellen {r['loaded_rss_gb']} GB"
              + (f", GLiNER geladen in {r['gliner_load_s']} s" if "gliner_load_s" in r else "")
              + (f", Sprachmodell geladen in {r['llm_load_s']} s ({r['llm_device']})" if "llm_load_s" in r else ""))
        for run in r["runs"]:
            print(f"  {run['pages']:>4} Seiten{' (volle Textseiten)' if run['full_text_pages'] else ''}: "
                  f"{run['seconds']:>7.1f} s ({run['seconds_per_page']} s/Seite), CPU {run['cpu_load_percent']} %, "
                  f"RAM-Spitze {run['peak_rss_gb']} GB" + (f", {run['llm_calls']} KI-Prüfungen" if run['llm_calls'] else ""))
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
