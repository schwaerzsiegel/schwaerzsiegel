"""
Lädt GLiNER einmalig in den app-eigenen Zwischenspeicher models/hf-cache
(neu in v33). Danach lädt der Sidecar GLiNER nur noch von dort und baut
keine Internetverbindung mehr auf (siehe core/paths.py).

Aufruf über download_models.cmd (braucht einmal Internet).

Ablauf:
1. GLiNER ganz normal über from_pretrained laden, aber mit
   HF_HUB_CACHE=models/hf-cache - so landet automatisch alles darin, was
   die installierte gliner-Version tatsächlich braucht (Gewichte,
   Konfiguration, Tokenizer des Grundmodells mdeberta).
2. Gegenprobe in einem neuen Prozess mit HF_HUB_OFFLINE=1: Modell laden
   und einen Testsatz erkennen. Klappt das, läuft die App offline.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "models" / "hf-cache"
MODEL_ID = "urchade/gliner_multi-v2.1"
TEST_TEXT = "Herr Klaus Müller wohnt in der Hauptstraße 5 in 20095 Hamburg."


def download():
    CACHE.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_CACHE"] = str(CACHE)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from gliner import GLiNER
    print(f"Lade {MODEL_ID} nach {CACHE} ...")
    GLiNER.from_pretrained(MODEL_ID)
    print("Download fertig.")


def verify() -> bool:
    env = dict(os.environ, HF_HUB_CACHE=str(CACHE), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    code = (
        "from gliner import GLiNER\n"
        f"m = GLiNER.from_pretrained({MODEL_ID!r})\n"
        f"hits = m.predict_entities({TEST_TEXT!r}, ['Person', 'Adresse'], threshold=0.4)\n"
        "print('Gefunden:', ', '.join(h['text'] + ' (' + h['label'] + ')' for h in hits))\n"
    )
    print("Gegenprobe ohne Internet ...")
    result = subprocess.run([sys.executable, "-c", code], env=env)
    return result.returncode == 0


def size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


if __name__ == "__main__":
    if "--verify-only" not in sys.argv:
        download()
    if verify():
        print(f"OK: GLiNER laeuft offline aus {CACHE} ({size_mb(CACHE):.0f} MB).")
    else:
        print("FEHLER: GLiNER konnte nicht offline geladen werden, siehe Meldung oben.")
        sys.exit(1)
