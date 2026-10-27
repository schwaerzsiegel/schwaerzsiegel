"""
Zentrale Pfade des Sidecars (neu in v32).

Bis v31 hing alles am aktuellen Arbeitsverzeichnis ("models/...",
"audit.db", "sidecar.log") - das funktioniert nur, wenn man
`python main.py` im sidecar-Ordner startet. Als gebündelte .exe
(PyInstaller) oder von Tauri gestartet stimmt das Arbeitsverzeichnis
nicht mehr, deshalb werden die Pfade jetzt hier festgelegt:

- APP_DIR: Ordner mit den mitgelieferten Dateien (models/, vendor/).
  Entwicklung: der sidecar-Ordner. Gebündelt: der Ordner, in dem
  redaction-sidecar.exe liegt.
- DATA_DIR: Ordner für Dateien, die die App schreibt (Log, Audit-DB,
  Port-Datei). Entwicklung: ebenfalls der sidecar-Ordner, wie bisher.
  Gebündelt: %LOCALAPPDATA%\\Schwaerzsiegel, weil der Programmordner nach
  einer Installation (C:\\Program Files\\...) nicht beschreibbar ist.
  Tauri kann den Ordner über REDACTION_APP_DATA_DIR vorgeben.
"""

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    if not FROZEN:
        return APP_DIR
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Schwaerzsiegel"


DATA_DIR = Path(os.environ.get("REDACTION_APP_DATA_DIR") or _default_data_dir())
DATA_DIR.mkdir(parents=True, exist_ok=True)

# v34: per Umgebungsvariable überschreibbar - `npm run dev:exe` lässt die
# gebaute .exe so die Modelle aus sidecar\models nutzen, weil sie nicht
# mehr neben die .exe kopiert werden (Installer-Größe, siehe build-installer.cmd).
MODELS_DIR = Path(os.environ.get("REDACTION_MODELS_DIR") or APP_DIR / "models")
VENDOR_DIR = APP_DIR / "vendor"

# v33: GLiNER komplett offline. download_models.py legt das Modell (samt
# Tokenizer und Konfiguration des Grundmodells mdeberta) in einen eigenen
# Hugging-Face-Zwischenspeicher unter models/hf-cache. Gibt es den Ordner,
# wird ausschließlich daraus geladen - jeder Versuch, etwas aus dem
# Internet nachzuladen, schlägt dann mit einer Fehlermeldung fehl, statt
# still eine Verbindung aufzubauen. Muss gesetzt sein, bevor
# huggingface_hub/transformers importiert werden (siehe core/ner.py).
HF_CACHE_DIR = MODELS_DIR / "hf-cache"
GLINER_MODEL_ID = "urchade/gliner_multi-v2.1"

if True:  # v39: Offline-Modus immer aktiv, auch bei fehlendem Modellcache.
    os.environ["HF_HUB_CACHE"] = str(HF_CACHE_DIR)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
