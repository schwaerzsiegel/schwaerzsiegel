"""
Einmaliges Laden der KI-Modelle beim Sidecar-Start (neu in v31).

Vorher wurden GLiNER und das LLM erst beim ersten Dokument geladen - das
erste Dokument nach dem Start war dadurch deutlich langsamer. Jetzt
startet das Laden direkt im Hintergrund, sobald der Sidecar läuft; die
Modelle bleiben danach für alle weiteren Dokumente im Speicher.

Läuft in einem eigenen Thread, damit der Sidecar sofort erreichbar ist
(Port-Handshake mit Tauri, Statusabfrage im Frontend). Kommt ein Dokument
an, bevor das Laden fertig ist, wartet die Verarbeitung einfach auf die
Lock in ner._get_model() bzw. llm._get_llm().
"""

import threading
import time

from core.timing import log

# "pending" | "loading" | "ready" | "error"
STATUS: dict = {
    "gliner": {"state": "pending", "device": None, "seconds": None, "error": None},
    # v38: Das LLM wird nur noch für den Modus "Gründlich mit KI" gebraucht
    # und deshalb erst bei Bedarf geladen ("idle" = noch nicht angefordert).
    "llm": {"state": "idle", "device": None, "seconds": None, "error": None},
}


def _load(key: str, loader):
    STATUS[key]["state"] = "loading"
    start = time.perf_counter()
    try:
        device = loader()
        STATUS[key].update(state="ready", device=device, seconds=round(time.perf_counter() - start, 1))
        log.info(f"[Modelle] {key} geladen in {STATUS[key]['seconds']}s auf {device}")
    except Exception as e:
        # Kein Absturz des Sidecars: der Fehler taucht spätestens bei der
        # Dokumentverarbeitung wieder auf (dort mit verständlicher Meldung
        # im Frontend), und im Log steht er sofort.
        STATUS[key].update(state="error", error=str(e), seconds=round(time.perf_counter() - start, 1))
        log.error(f"[Modelle] {key} konnte nicht geladen werden: {e}")


_llm_request_lock = threading.Lock()


def _preload_all():
    from core import ner
    _load("gliner", ner.load_model)


def start_preload():
    threading.Thread(target=_preload_all, name="model-preload", daemon=True).start()


def request_llm():
    """Startet das Laden des LLM im Hintergrund, falls noch nicht geschehen
    (v38: beim Einschalten von "Gründlich mit KI" oder beim ersten Dokument
    in diesem Modus)."""
    from core import llm
    with _llm_request_lock:
        if STATUS["llm"]["state"] != "idle":
            return
        STATUS["llm"]["state"] = "pending"
    threading.Thread(target=_load, args=("llm", llm.load_model), name="llm-load", daemon=True).start()


def unload_llm() -> bool:
    """Phase 3: Sprachmodell aus dem Speicher entfernen (ca. 1,7-3,5 GB),
    wenn der Modus "Gründliche KI" nicht mehr gewählt ist."""
    from core import llm
    with _llm_request_lock:
        if STATUS["llm"]["state"] not in ("ready", "error"):
            return False
        unloaded = llm.unload_model()
        STATUS["llm"].update(state="idle", device=None, seconds=None, error=None)
    if unloaded:
        log.info("[Modelle] llm entladen")
    return unloaded


def _done(key: str) -> bool:
    return STATUS[key]["state"] in ("ready", "error")


def gliner_ready() -> bool:
    return _done("gliner")


def llm_ready() -> bool:
    return _done("llm")
