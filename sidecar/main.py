"""
Lokaler Sidecar-Prozess. Bindet ausschließlich an 127.0.0.1 auf einem
zur Laufzeit gewählten freien Port und meldet diesen beim Start als Zeile
"SIDECAR_PORT=<port>" auf stdout (seit v32, wird von Tauri bzw. vom
Vite-Dev-Server gelesen) und zusätzlich in der Datei sidecar.port im
Datenordner.

Es gibt bewusst keinen Code-Pfad, der eine externe Netzwerkverbindung
aufbaut - alle Modelle (GLiNER, LLM, YOLO) laufen ausschließlich lokal.
"""

import multiprocessing
import os
import secrets
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import fitz
import uvicorn
from fastapi import FastAPI, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

# Ausgabe immer als UTF-8: Wird der Sidecar von Tauri oder npm gestartet,
# geht stdout in eine Leitung statt in ein Konsolenfenster, und Windows
# würde sonst cp1252 nehmen (Umlaute und Sonderzeichen kaputt).
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Phase 3: Netzwerksperre VOR dem Import der KI-Bibliotheken - ab hier ist
# nur noch 127.0.0.1 erreichbar (siehe core/offline.py).
from core import offline

offline.install()

# Phase 10: eigener Arbeitsordner für temporäre Dateien, wird beim Beenden
# und spätestens beim nächsten Start gelöscht (siehe core/workdir.py).
import atexit  # noqa: E402

from core import workdir  # noqa: E402

workdir.start()
atexit.register(workdir.cleanup_session)

from core import audit, branding, marks, models, ner, pdf_processor, redaction  # noqa: E402
from core.paths import DATA_DIR, FROZEN  # noqa: E402
from core.timing import StepTimer, log, setup_logging

LOG_PATH = setup_logging()
for _name in workdir.cleanup_stale():
    log.info(f"[Arbeitsordner] Reste einer früheren Sitzung gelöscht: {_name}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # v31: KI-Modelle direkt beim Start im Hintergrund laden, nicht erst
    # beim ersten Dokument (siehe core/models.py).
    models.start_preload()
    yield


SESSION_TOKEN = os.environ.get("REDACTION_SESSION_TOKEN", "")

app = FastAPI(title="Redaction Sidecar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def require_session_token(request: Request, call_next):
    # No endpoint, including images and downloads, is exempt.
    # A missing token is a startup/configuration failure, not an open API.
    if not SESSION_TOKEN or not secrets.compare_digest(
        request.headers.get("X-Redaction-Session", ""), SESSION_TOKEN
    ):
        return Response(status_code=401, content="Unauthorized")
    return await call_next(request)


# Conservative resource limits; configurable for controlled deployments.
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 1000
MAX_RENDER_ZOOM = 3.0

# Nur für den Browser-Dev-Modus nötig (Vite-Dev-Server auf einem anderen
# Port als der Sidecar) - in der fertigen Tauri-App entfällt das, da dort
# kein Cross-Origin-Request im klassischen Browser-Sinn stattfindet.
# Bewusst auf 127.0.0.1 beschränkt statt "*", auch im Dev-Modus.
app.add_middleware(
    CORSMiddleware,
    # tauri.localhost / tauri://localhost: Herkunft der fertigen Tauri-App
    # unter Windows bzw. macOS/Linux (v32).
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-Memory-Dokumentenstatus für die Skizze. In der echten Implementierung:
# persistente, verschlüsselte lokale Ablage pro Dokument-Session.
DOCUMENTS: dict[str, dict] = {}
# Phase 10: schützt das Entfernen eines Dokuments gegen eine gleichzeitig
# zu Ende laufende Analyse (die sonst die PDF-Bytes erneut ablegen würde).
_DOCUMENTS_LOCK = threading.Lock()


def _mapping_path(doc_id: str) -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / f"pseudonym-schluessel-{doc_id}.json"


def _forget_document(doc_id: str) -> bool:
    """Phase 10: Dokument vollständig aus der Sitzung entfernen - Arbeitsstand
    mit allen Texten und Markierungen, PDF-Bytes, Vorschaubilder sowie die
    Ergebnisdateien (geschwärztes bzw. pseudonymisiertes PDF und
    Zuordnungstabelle). Eine laufende Analyse wird abgebrochen.
    False, wenn das Dokument nicht (mehr) bekannt ist."""
    with _DOCUMENTS_LOCK:
        document = DOCUMENTS.pop(doc_id, None)
        pdf_processor.forget(doc_id)
    if document is None:
        return False
    document["_cancel"].set()
    _invalidate_result(document)
    # Auch Dateien eines Finalisierens, das nicht bis zum Vermerk im Dokument kam
    for prefix in ("redacted", "pseudonymized"):
        pdf_processor.result_file(prefix, doc_id).unlink(missing_ok=True)
    _mapping_path(doc_id).unlink(missing_ok=True)
    document["pages"] = []
    document["_deleted"].clear()
    audit.log(doc_id, "document_closed", {})
    return True


class Decision(BaseModel):
    suggestion_id: str
    accepted: bool


class FinalizeRequest(BaseModel):
    mode: str = "redact"  # "redact" (irreversibel) | "pseudonymize" (umkehrbar)


class MarkCreate(BaseModel):
    """Phase 1: manuelle Markierung. kind "area" = Rechteck(e) in
    Anzeige-Koordinaten (Punkte, gedrehte Seite); kind "text" = Wort-IDs
    der Seite, die Boxen (eine pro Zeile) bestimmt der Sidecar."""
    page: int
    kind: str = "area"
    rects: list[list[float]] | None = None
    word_ids: list[str] | None = None
    label: str | None = None


class MarkUpdate(BaseModel):
    rects: list[list[float]] | None = None
    label: str | None = None
    status: str | None = None


# Analyse-Modi: "manual" = keine automatische Erkennung, "rules" = nur
# Regeln, "fast" = Regeln + GLiNER (Standard), "ai" = zusätzlich LLM.
ANALYSIS_MODES = {"manual", "rules", "fast", "ai"}


# Verständliche Texte für die Fortschrittsanzeige im Frontend, je
# Schrittname aus der Zeitmessung (pdf_processor.extract_page / ner.detect).
_STEP_LABELS = {
    "Text lesen": "Text wird gelesen",
    "OCR": "Gescannte Seite wird per Texterkennung (OCR) gelesen",
    "Bilder": "Eingebettete Bilder werden gesucht",
    "Regex": "Muster werden geprüft (IBAN, E-Mail, Telefon ...)",
    "GLiNER": "KI sucht Namen, Adressen und weitere Angaben",
    "LLM": "KI prüft unsichere Treffer im Kontext",
}

# Es wird immer nur ein Dokument gleichzeitig analysiert - das LLM ist
# nicht threadsicher, und parallel wäre es auf demselben Rechner ohnehin
# nicht schneller.
_PROCESSING_LOCK = threading.Lock()


def _mode_availability() -> dict:
    """Phase 3: welche Analyse-Modi gerade nutzbar sind - mit verständlichem
    Grund, wenn nicht. Fehlt ein Modell, bleibt die App trotzdem benutzbar."""
    from core import llm
    gliner = models.STATUS["gliner"]
    gliner_problem = gliner["error"] if gliner["state"] == "error" else None
    llm_info = llm.model_info()
    llm_problem = llm_info["problem"] or (models.STATUS["llm"]["error"] if models.STATUS["llm"]["state"] == "error" else None)
    return {
        "manual": {"available": True, "problem": None},
        "rules": {"available": True, "problem": None},
        "fast": {"available": gliner_problem is None, "problem": gliner_problem,
                 "loading": gliner["state"] in ("pending", "loading")},
        "ai": {"available": gliner_problem is None and llm_problem is None,
               "problem": gliner_problem or llm_problem, "model": llm_info,
               "loading": models.STATUS["llm"]["state"] in ("pending", "loading")},
    }


@app.get("/status")
def sidecar_status():
    """Ladezustand der KI-Modelle und verfügbare Analyse-Modi (für das Frontend)."""
    # v38: "ready" = Erkennung bereit (GLiNER). Das LLM lädt erst bei Bedarf.
    return {"models": models.STATUS, "ready": models.gliner_ready(), "modes": _mode_availability(),
            "offline_guard": offline.is_installed(), "ocr": _ocr_available()}


def _ocr_available() -> bool:
    """Phase 4: ist die Texterkennung (Tesseract) für Scans vorhanden?"""
    try:
        import shutil
        from core import ocr  # setzt den Pfad zur mitgelieferten Kopie
        import pytesseract
        cmd = pytesseract.pytesseract.tesseract_cmd
        # Phase 5: ohne deutsches Sprachpaket scheitert jede Scan-Seite
        return bool(Path(cmd).is_file() or shutil.which(cmd)) and ocr.language_available()
    except Exception:
        return False


@app.post("/models/llm/load")
def load_llm():
    """v38: KI-Prüfmodell vorab laden, sobald "Gründliche KI" gewählt wird."""
    models.request_llm()
    return {"models": models.STATUS}


@app.post("/models/llm/unload")
def unload_llm():
    """Phase 3: Arbeitsspeicher des Sprachmodells freigeben, wenn ein anderer
    Modus gewählt wird. Nicht während einer laufenden Analyse."""
    if not _PROCESSING_LOCK.acquire(blocking=False):
        return {"unloaded": False, "models": models.STATUS}
    try:
        return {"unloaded": models.unload_llm(), "models": models.STATUS}
    finally:
        _PROCESSING_LOCK.release()


@app.get("/branding")
def get_branding():
    """Phase 4: Kunden-Branding (Name, Akzentfarbe, Logo, Support-Kontakt).
    Fehlt es oder ist es ungültig, gilt das Standarddesign (siehe warnings)."""
    info = branding.load()
    for warning in info["warnings"]:
        log.warning(f"[Branding] {warning}")
    return info


@app.get("/branding/logo")
def get_branding_logo(variant: str = "light"):
    found = branding.logo("dark" if variant == "dark" else "light")
    if not found:
        raise HTTPException(status_code=404, detail="Kein Logo hinterlegt.")
    content, media_type = found
    # Nur als Bild verwenden; falls doch direkt geöffnet: keine Skripte
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@app.post("/documents")
async def create_document(file: UploadFile, mode: str = Form("fast")):
    """Nimmt ein PDF entgegen und startet die Analyse im Hintergrund.

    Neu in v31: antwortet sofort mit document_id und Seitenzahl, statt bis
    zum Ende der Analyse zu blockieren. Den Fortschritt fragt das Frontend
    über GET /documents/{id}/progress ab (Seite x von y + aktueller Schritt).
    Ausschließlich lokal, keine Netzwerk-Calls.

    Modi: "manual" (keine Erkennung), "rules" (A: nur Regeln), "fast"
    (B: Regeln + GLiNER, Standard), "ai" (C: zusätzlich Sprachmodell für
    unsichere Treffer)."""
    if mode not in ANALYSIS_MODES:
        raise HTTPException(status_code=400, detail="Unbekannter Analyse-Modus.")
    # Stream with a hard limit instead of buffering an unbounded upload.
    chunks = []
    total_size = 0
    while chunk := await file.read(1024 * 1024):
        total_size += len(chunk)
        if total_size > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="PDF überschreitet das Limit von 100 MB.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Keine gültige PDF-Datei.")
    try:
        with fitz.open(stream=content, filetype="pdf") as probe:
            if probe.needs_pass:
                raise HTTPException(status_code=400, detail="Das PDF ist passwortgeschützt und kann nicht geprüft werden.")
            page_count = probe.page_count
            if page_count < 1 or page_count > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDF überschreitet das Seitenlimit von 1000 Seiten.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="PDF konnte nicht geöffnet werden.")

    # Phase 10: Die App bearbeitet immer nur ein Dokument. Ein neues ersetzt
    # das bisherige, das dabei vollständig entfernt wird.
    for old_id in list(DOCUMENTS):
        _forget_document(old_id)

    doc_id = str(uuid4())
    DOCUMENTS[doc_id] = {
        "_doc_id": doc_id,
        "analysis_mode": mode,
        "pages": [],
        "notices": [],
        "review_signature": None,
        "_deleted": {},
        "_cancel": threading.Event(),
        "progress": {"state": "queued", "page": 0, "page_count": page_count,
                     "step": "Wartet auf Start", "error": None},
    }
    threading.Thread(target=_process_document, args=(doc_id, content, mode), daemon=True).start()
    return {"document_id": doc_id, "page_count": page_count}


class _Cancelled(Exception):
    pass


def _wait_until(ready, progress, step: str, label: str, cancel: threading.Event | None = None):
    if ready():
        return
    progress["step"] = step
    wait_start = time.perf_counter()
    while not ready():
        if cancel is not None and cancel.is_set():
            raise _Cancelled()
        time.sleep(0.2)
    log.info(f"[Zeit] Auf {label} gewartet: {time.perf_counter() - wait_start:.2f}s")


def _prepare_models(mode: str, progress: dict, cancel: threading.Event, notices: list[str]) -> tuple[bool, bool]:
    """Auf die benötigten Modelle warten. Phase 1: Fehlt ein Modell, wird mit
    dem gearbeitet, was da ist (Regeln bzw. ohne Sprachmodell) - mit Hinweis,
    statt das Dokument abzulehnen. Gibt (use_gliner, use_ai) zurück."""
    use_gliner = mode in ("fast", "ai")
    use_ai = mode == "ai"
    if cancel.is_set():
        raise _Cancelled()
    if use_gliner:
        _wait_until(models.gliner_ready, progress,
                    "KI-Modelle werden geladen (einmalig nach dem Start)", "GLiNER", cancel)
        if models.STATUS["gliner"]["state"] == "error":
            use_gliner = use_ai = False
            notices.append(
                f"KI-Erkennung nicht verfügbar ({models.STATUS['gliner']['error']}). "
                "Es wurden nur Regeln verwendet - bitte besonders sorgfältig prüfen und fehlende "
                "Stellen manuell markieren."
            )
    if use_ai:
        models.request_llm()
        _wait_until(models.llm_ready, progress, "Sprachmodell wird geladen", "LLM", cancel)
        if models.STATUS["llm"]["state"] == "error":
            use_ai = False
            notices.append(
                f"Gründliche KI-Prüfung nicht verfügbar ({models.STATUS['llm']['error']}). "
                "Unsichere Treffer wurden nicht vom Sprachmodell geprüft – bitte selbst prüfen."
            )
    return use_gliner, use_ai


def _process_document(doc_id: str, content: bytes, mode: str = "fast"):
    from core.llm import Cancelled as LlmCancelled

    document = DOCUMENTS[doc_id]
    progress = document["progress"]
    notices = document["notices"]
    cancel: threading.Event = document["_cancel"]

    def check_cancel():
        if cancel.is_set():
            raise _Cancelled()

    with _PROCESSING_LOCK:
        progress["state"] = "processing"
        try:
            use_gliner, use_ai = _prepare_models(mode, progress, cancel, notices)

            progress["step"] = "Dokument wird vorbereitet"
            content, prepare_notices = redaction.prepare_document(content)
            notices.extend(prepare_notices)

            doc = fitz.open(stream=content, filetype="pdf")
            page_count = doc.page_count
            total = StepTimer()
            llm_cache: dict = {}
            doc_start = time.perf_counter()
            pages = []

            def on_step(name):
                progress["step"] = _STEP_LABELS.get(name, name)

            def on_llm_progress(i, n):
                progress["step"] = f"KI prüft unsichere Treffer im Kontext ({i} von {n})"

            try:
                for i, pdf_page in enumerate(doc):
                    check_cancel()
                    progress["page"] = i + 1
                    timer = StepTimer()
                    page = pdf_processor.extract_page(pdf_page, i, timer=timer, on_step=on_step,
                                                      detect_images=mode != "manual")
                    if mode != "manual":
                        page.suggestions.extend(ner.detect(
                            page.words, timer=timer, llm_cache=llm_cache, on_step=on_step, use_llm=use_ai,
                            use_gliner=use_gliner, llm_progress=on_llm_progress, should_cancel=cancel.is_set))
                    marks.init_auto(page)
                    notices.extend(page.notices)
                    pages.append(page)
                    total.add(timer)
                    log.info(f"[Zeit] Seite {i + 1}/{page_count}: {timer.format()} | Summe {timer.total():.2f}s")
            finally:
                doc.close()

            with _DOCUMENTS_LOCK:
                if doc_id not in DOCUMENTS:  # inzwischen geschlossen
                    raise _Cancelled()
                pdf_processor.register(doc_id, content)
            document["pages"] = pages
            elapsed = time.perf_counter() - doc_start
            log.info(f"[Zeit] Dokument fertig ({mode}): {page_count} Seiten in {elapsed:.2f}s - {total.format()}")
            audit.log(doc_id, "document_loaded", {"page_count": page_count, "mode": mode,
                                                  "seconds": round(elapsed, 1)})
            progress.update(state="done", step="Fertig")
        except (_Cancelled, LlmCancelled):
            log.info(f"Analyse von {doc_id} abgebrochen")
            audit.log(doc_id, "analysis_cancelled", {"page": progress.get("page")})
            progress.update(state="cancelled", step="Abgebrochen", error=None)
        except Exception as e:
            # Fehler (z.B. fehlende Tesseract-Installation) landen in der
            # Fortschrittsabfrage und werden im Frontend als Meldung
            # angezeigt, statt still zu verschwinden.
            log.exception(f"Verarbeitung von {doc_id} fehlgeschlagen")
            message = "zu wenig Arbeitsspeicher" if isinstance(e, MemoryError) else str(e)
            progress.update(state="error", error=f"Verarbeitung fehlgeschlagen: {message}")


@app.get("/documents/{doc_id}/progress")
def get_progress(doc_id: str):
    document = DOCUMENTS.get(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Dokument unbekannt.")
    return document["progress"]


@app.delete("/documents/{doc_id}")
def close_document(doc_id: str):
    """Phase 10: Dokument schließen - entfernt Arbeitsstand und Ergebnisdateien
    (siehe _forget_document). Nicht gespeicherte Ergebnisse sind danach weg."""
    if not _forget_document(doc_id):
        raise HTTPException(status_code=404, detail="Dokument unbekannt.")
    return {"status": "closed"}


@app.post("/documents/{doc_id}/cancel")
def cancel_analysis(doc_id: str):
    """Phase 3: laufende Analyse abbrechen (wirkt zwischen zwei Seiten bzw.
    zwei Anfragen an das Sprachmodell)."""
    document = DOCUMENTS.get(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Dokument unbekannt.")
    if document["progress"]["state"] in ("queued", "processing"):
        document["_cancel"].set()
        document["progress"]["step"] = "Wird abgebrochen ..."
    return document["progress"]


class ReanalyzeRequest(BaseModel):
    mode: str = "fast"


@app.post("/documents/{doc_id}/reanalyze")
def reanalyze(doc_id: str, body: ReanalyzeRequest):
    """Phase 4: Dokument in einem anderen Modus erneut analysieren.
    Manuelle Markierungen und alle bereits bestätigten oder verworfenen
    Treffer bleiben erhalten; nur offene Vorschläge werden ersetzt.
    Bricht man ab oder schlägt es fehl, bleiben die Markierungen unverändert."""
    document = _require_ready(doc_id)
    if body.mode not in ("rules", "fast", "ai"):
        raise HTTPException(status_code=400, detail="Unbekannter Analyse-Modus.")
    document["_cancel"] = threading.Event()
    document["progress"].update(state="queued", page=0, step="Wartet auf Start", error=None, kind="reanalyze")
    threading.Thread(target=_reanalyze_document, args=(doc_id, body.mode), daemon=True).start()
    return document["progress"]


def _same_box(a, b) -> bool:
    return max(abs(x - y) for x, y in zip(a, b)) < 2


def _reanalyze_document(doc_id: str, mode: str):
    from core.llm import Cancelled as LlmCancelled

    document = DOCUMENTS[doc_id]
    progress = document["progress"]
    cancel: threading.Event = document["_cancel"]
    notices: list[str] = []
    with _PROCESSING_LOCK:
        progress["state"] = "processing"
        try:
            use_gliner, use_ai = _prepare_models(mode, progress, cancel, notices)

            def on_llm_progress(i, n):
                progress["step"] = f"KI prüft unsichere Treffer im Kontext ({i} von {n})"

            found_by_page = {}
            llm_cache: dict = {}
            with fitz.open(stream=pdf_processor._RAW_BYTES[doc_id], filetype="pdf") as doc:
                for page in document["pages"]:
                    if cancel.is_set():
                        raise _Cancelled()
                    progress["page"] = page.index + 1
                    progress["step"] = _STEP_LABELS["GLiNER" if use_gliner else "Regex"]
                    found = ner.detect(page.words, llm_cache=llm_cache, use_llm=use_ai, use_gliner=use_gliner,
                                       llm_progress=on_llm_progress, should_cancel=cancel.is_set)
                    found.extend(pdf_processor.detect_embedded_images(doc[page.index]))
                    found_by_page[page.index] = found

            # Erst jetzt übernehmen - ein Abbruch vorher ändert nichts.
            replaced = added = 0
            for page in document["pages"]:
                kept = [m for m in page.suggestions if m.get("origin") == "manual" or m["status"] != marks.SUGGESTED]
                replaced += len(page.suggestions) - len(kept)
                kept_words = {w for m in kept for w in m.get("word_ids", [])}
                kept_boxes = [m["bbox"] for m in kept]
                fresh = [s for s in found_by_page[page.index]
                         if not set(s.get("word_ids", [])) & kept_words
                         and not (not s.get("word_ids") and any(_same_box(s["bbox"], b) for b in kept_boxes))]
                holder = pdf_processor.Page(index=page.index, words=page.words, suggestions=fresh, geometry=page.geometry)
                marks.init_auto(holder)
                page.suggestions = kept + fresh
                added += len(fresh)
            document["analysis_mode"] = mode
            document["notices"].extend(notices)
            _invalidate_result(document)
            audit.log(doc_id, "reanalyzed", {"mode": mode, "replaced": replaced, "added": added})
            progress.update(state="done", step="Fertig")
        except (_Cancelled, LlmCancelled):
            progress.update(state="done", step="Erneute Analyse abgebrochen – Markierungen unverändert", error=None)
        except Exception as e:
            log.exception(f"Erneute Analyse von {doc_id} fehlgeschlagen")
            document["notices"].append(f"Erneute Analyse fehlgeschlagen ({e}) – Markierungen unverändert.")
            progress.update(state="done", step="Erneute Analyse fehlgeschlagen – Markierungen unverändert", error=None)


def _validate_page(doc_id: str, page_num: int) -> dict:
    document = _require_ready(doc_id)
    if page_num < 0 or page_num >= len(document["pages"]):
        raise HTTPException(status_code=404, detail="Seite nicht vorhanden.")
    return document

def _validate_zoom(zoom: float) -> None:
    import math
    if not math.isfinite(zoom) or not (0.25 <= zoom <= MAX_RENDER_ZOOM):
        raise HTTPException(status_code=400, detail="Ungültiger Zoomwert.")

def _require_ready(doc_id: str) -> dict:
    document = DOCUMENTS.get(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Dokument unbekannt.")
    if document["progress"]["state"] != "done":
        raise HTTPException(status_code=409, detail="Dokument wird noch analysiert.")
    return document


def _signature(document: dict) -> str:
    return marks.signature(document)


def _invalidate_result(document: dict) -> None:
    """Jede Änderung an der Auswahl macht ein vorhandenes Ergebnis ungültig -
    es darf nicht mehr als "aktuelles, geprüftes" Ergebnis exportiert werden."""
    for key in ("result_path", "mapping_table_path"):
        path = document.pop(key, None)
        if path:
            Path(path).unlink(missing_ok=True)
    for key in ("result_mode", "result_signature", "result_targets", "verification", "result_actions"):
        document.pop(key, None)


def _result_is_current(document: dict) -> bool:
    return bool(document.get("result_path")) and document.get("result_signature") == _signature(document)


def _find_mark(document: dict, mark_id: str):
    for page in document["pages"]:
        for mark in page.suggestions:
            if mark["id"] == mark_id:
                return page, mark
    raise HTTPException(status_code=404, detail="Markierung nicht gefunden.")


def _public_mark(document: dict, page, mark: dict) -> dict:
    redacted = _result_is_current(document) and document.get("result_mode") == "redact"
    return marks.public(mark, page, redacted=redacted)


def _display_rects(page, rects: list[list[float]] | None) -> list[list[float]]:
    if not rects:
        raise HTTPException(status_code=400, detail="Keine Bereiche angegeben.")
    if len(rects) > marks.MAX_RECTS:
        raise HTTPException(status_code=400, detail="Zu viele Bereiche in einer Markierung.")
    converted = [marks.from_display(page, r) for r in rects]
    if any(r is None for r in converted):
        raise HTTPException(status_code=400, detail="Ungültiger oder zu kleiner Bereich (mindestens 1 Punkt).")
    return converted


def _changed(doc_id: str, document: dict, event: str, payload: dict) -> None:
    _invalidate_result(document)
    audit.log(doc_id, event, payload)


@app.post("/documents/{doc_id}/review/confirm")
def confirm_review(doc_id: str):
    """
    Übernommen aus eurem MVP (dort: finish_review()). Der Nutzer bestätigt
    explizit die aktuelle Auswahl; erst danach ist Finalisieren möglich.
    """
    document = _require_ready(doc_id)
    document["review_signature"] = _signature(document)
    audit.log(doc_id, "review_confirmed", {})
    return {"status": "confirmed"}


@app.get("/documents/{doc_id}/pages/{page_num}")
def get_page(doc_id: str, page_num: int):
    """Phase 1: alles, was die Seitenansicht braucht - Größe in Punkten
    (gedreht wie angezeigt), Wörter für die Textauswahl und Markierungen,
    alles in Anzeige-Koordinaten."""
    document = _validate_page(doc_id, page_num)
    page = document["pages"][page_num]
    return {
        "index": page_num,
        "width": page.geometry["width"],
        "height": page.geometry["height"],
        "rotation": page.geometry["rotation"],
        "words": [marks.public_word(w, page) for w in page.words],
        "marks": [_public_mark(document, page, m) for m in page.suggestions],
    }


@app.get("/documents/{doc_id}/pages/{page_num}/suggestions")
def get_suggestions(doc_id: str, page_num: int):
    document = _validate_page(doc_id, page_num)
    page = document["pages"][page_num]
    return {"suggestions": [_public_mark(document, page, m) for m in page.suggestions]}


@app.get("/documents/{doc_id}/marks")
def list_marks(doc_id: str):
    """Gemeinsame Liste aller Markierungen des Dokuments (alle Seiten)."""
    document = _require_ready(doc_id)
    items = [_public_mark(document, page, m) for page in document["pages"] for m in page.suggestions]
    counts = {s: 0 for s in (marks.SUGGESTED, marks.CONFIRMED, marks.REJECTED, marks.REDACTED)}
    for item in items:
        counts[item["status"]] += 1
    return {
        "marks": items,
        "counts": counts,
        "notices": document.get("notices", []),
        "review_confirmed": document.get("review_signature") == _signature(document),
        "result_current": _result_is_current(document),
        "analysis_mode": document.get("analysis_mode"),
    }


@app.post("/documents/{doc_id}/marks")
def create_mark(doc_id: str, body: MarkCreate):
    document = _validate_page(doc_id, body.page)
    page = document["pages"][body.page]
    if body.kind == "text":
        wanted = set(body.word_ids or [])
        words = [w for w in page.words if w["id"] in wanted]
        if not words or len(words) != len(wanted):
            raise HTTPException(status_code=400, detail="Ungültige Textauswahl.")
        mark = marks.new_manual(page, kind="text", rects=marks.line_rects(words),
                                text=" ".join(w["text"] for w in words), label=body.label,
                                word_ids=[w["id"] for w in words])
    elif body.kind == "area":
        mark = marks.new_manual(page, kind="area", rects=_display_rects(page, body.rects), label=body.label)
    else:
        raise HTTPException(status_code=400, detail="Unbekannte Art der Markierung.")
    page.suggestions.append(mark)
    _changed(doc_id, document, "mark_created", {"mark_id": mark["id"], "page": body.page, "kind": mark["kind"]})
    return _public_mark(document, page, mark)


@app.patch("/documents/{doc_id}/marks/{mark_id}")
def update_mark(doc_id: str, mark_id: str, body: MarkUpdate):
    document = _require_ready(doc_id)
    page, mark = _find_mark(document, mark_id)
    changes = {}
    if body.rects is not None:
        marks.set_rects(mark, _display_rects(page, body.rects))
        changes["rects"] = len(mark["rects"])
    if body.label is not None:
        label = body.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="Kategorie darf nicht leer sein.")
        mark["label"] = label[:marks.MAX_LABEL]
        changes["label"] = mark["label"]
    if body.status is not None:
        if body.status not in marks.EDITABLE_STATUSES:
            raise HTTPException(status_code=400, detail="Ungültiger Status.")
        mark["status"] = body.status
        changes["status"] = body.status
    _changed(doc_id, document, "mark_updated", {"mark_id": mark_id, **changes})
    return _public_mark(document, page, mark)


@app.delete("/documents/{doc_id}/marks/{mark_id}")
def delete_mark(doc_id: str, mark_id: str):
    document = _require_ready(doc_id)
    page, mark = _find_mark(document, mark_id)
    page.suggestions.remove(mark)
    # Für "Rückgängig" aufbewahren (nur im Arbeitsspeicher dieser Sitzung)
    document["_deleted"][mark_id] = (page.index, mark)
    _changed(doc_id, document, "mark_deleted", {"mark_id": mark_id})
    return {"status": "deleted"}


@app.post("/documents/{doc_id}/marks/{mark_id}/restore")
def restore_mark(doc_id: str, mark_id: str):
    document = _require_ready(doc_id)
    entry = document["_deleted"].pop(mark_id, None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Markierung kann nicht wiederhergestellt werden.")
    page_index, mark = entry
    page = document["pages"][page_index]
    page.suggestions.append(mark)
    _changed(doc_id, document, "mark_restored", {"mark_id": mark_id})
    return _public_mark(document, page, mark)


@app.get("/documents/{doc_id}/pages/{page_num}/image")
def get_page_image(doc_id: str, page_num: int, zoom: float = 1.5):
    _validate_page(doc_id, page_num)
    _validate_zoom(zoom)
    png_bytes = pdf_processor.render_page_image(doc_id, page_num, zoom)
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/documents/{doc_id}/result/pages/{page_num}/image")
def get_result_page_image(doc_id: str, page_num: int, zoom: float = 1.5):
    document = DOCUMENTS.get(doc_id)
    if not document or not _result_is_current(document):
        raise HTTPException(status_code=404, detail="Noch kein finalisiertes Ergebnis für dieses Dokument vorhanden.")
    _validate_zoom(zoom)
    if page_num < 0 or page_num >= len(document.get("pages", [])):
        raise HTTPException(status_code=404, detail="Seite nicht vorhanden.")
    png_bytes = pdf_processor.render_external_page_image(document["result_path"], page_num, zoom)
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/documents/{doc_id}/pages/{page_num}/decisions")
def submit_decision(doc_id: str, page_num: int, decision: Decision):
    """Ältere Schnittstelle (Bestätigen/Ablehnen), bildet auf den Status ab."""
    document = _validate_page(doc_id, page_num)
    page, mark = _find_mark(document, decision.suggestion_id)
    if page.index != page_num:
        raise HTTPException(status_code=404, detail="Markierung nicht auf dieser Seite.")
    mark["status"] = marks.CONFIRMED if decision.accepted else marks.REJECTED
    _changed(doc_id, document, "decision", decision.model_dump())
    return {"status": "ok"}


def _locked(detail: str, check) -> HTTPException:
    """Fehlgeschlagene Prüfung: Ergebnis wird gelöscht, Export bleibt gesperrt.
    Der Prüfbericht geht mit, damit die Oberfläche die Gründe zeigen kann."""
    return HTTPException(status_code=422, detail={"message": detail, "verification": check.dict()})


@app.post("/documents/{doc_id}/finalize")
def finalize(doc_id: str, request: FinalizeRequest = FinalizeRequest()):
    """Führt die finale Verarbeitung aus - je nach Modus entweder echte,
    irreversible Redaction oder umkehrbare Pseudonymisierung - und prüft
    die gespeicherte Datei anschließend unabhängig.

    Bricht ab, wenn sich die Auswahl seit der letzten Review-Bestätigung
    verändert hat (gleiches Prinzip wie im MVP: Freigabe wird durch
    nachträgliche Änderungen automatisch entwertet)."""
    document = _require_ready(doc_id)
    if request.mode not in ("redact", "pseudonymize"):
        raise HTTPException(status_code=400, detail="Unbekannter Modus.")
    current_signature = _signature(document)
    if document.get("review_signature") != current_signature:
        raise HTTPException(
            status_code=409,
            detail="Auswahl wurde nach der letzten Freigabe verändert. Bitte Review erneut bestätigen.",
        )
    if not marks.confirmed_by_page(document):
        raise HTTPException(status_code=409, detail="Keine bestätigten Markierungen - es gibt nichts zu schwärzen.")
    _invalidate_result(document)

    try:
        if request.mode == "pseudonymize":
            result_path, mapping_table = pdf_processor.finalize_pseudonymization(document)
            check = pdf_processor.verify_pseudonymization(result_path, mapping_table)

            if not check.passed:
                result_path.unlink(missing_ok=True)
                audit.log(doc_id, "finalize_blocked", {"mode": "pseudonymize", "problems": len(check.residual_text_found)})
                raise _locked("Pseudonymisierung nicht verifiziert; Ergebnis gesperrt.", check)

            # Zuordnungstabelle bewusst unter eigenem Dateinamen/Pfad speichern,
            # NIE neben dem PDF im selben Ordner - Art. 4 Nr. 5 DSGVO verlangt
            # die Trennung von Pseudonym und Schlüssel.
            import json
            mapping_path = _mapping_path(doc_id)
            mapping_path.write_text(json.dumps(mapping_table, ensure_ascii=False, indent=2), encoding="utf-8")

            audit.log(doc_id, "finalized", {"mode": "pseudonymize", "entity_count": len(mapping_table), "verification_passed": check.passed})
            document.update(result_path=str(result_path), result_mode="pseudonymize",
                            mapping_table_path=str(mapping_path), result_signature=current_signature,
                            verification=check.dict())
            return {
                "output_path": str(result_path),
                "mode": "pseudonymize",
                "mapping_table_path": str(mapping_path),
                "verification": check.dict(),
                "actions": document.get("result_actions", []),
                "warning": "Die Zuordnungstabelle enthält Klartext-Originaldaten. "
                           "Getrennt vom pseudonymisierten Dokument aufbewahren - "
                           "niemals im selben Ordner oder Archiv speichern. Bitte jetzt speichern: "
                           "Beim Öffnen eines anderen Dokuments und beim Beenden der App wird sie gelöscht.",
            }

        result_path, targets = pdf_processor.finalize_redaction(document)
        check = pdf_processor.verify_redaction(result_path, document, targets)
        if not check.passed:
            result_path.unlink(missing_ok=True)
            audit.log(doc_id, "finalize_blocked", {"mode": "redact", "problems": len(check.residual_text_found)})
            raise _locked("Schwärzung nicht verifiziert; Ergebnis gesperrt.", check)
        audit.log(doc_id, "finalized", {"mode": "redact", "verification_passed": check.passed,
                                        "areas": len(targets), "warnings": len(check.warnings)})
        document.update(result_path=str(result_path), result_mode="redact", result_signature=current_signature,
                        result_targets=targets, verification=check.dict())
        return {"output_path": str(result_path), "mode": "redact", "verification": check.dict(),
                "actions": document.get("result_actions", [])}
    except HTTPException:
        raise
    except Exception as e:
        # Unbehandelte Exceptions hier würden sonst ohne CORS-Header beim
        # Browser ankommen und dort nur als nichtssagendes "Failed to
        # fetch" erscheinen, statt der eigentlichen Fehlermeldung - siehe
        # z.B. den Windows-/tmp-Pfad-Bug, der genau das ausgelöst hat.
        log.exception("Finalisieren fehlgeschlagen")
        _invalidate_result(document)
        raise HTTPException(status_code=500, detail=f"Finalisieren fehlgeschlagen: {e}")


@app.post("/documents/{doc_id}/result/verify")
def reverify_result(doc_id: str):
    """Prüft das vorhandene Ergebnis erneut (Schaltfläche "Verifizierung
    starten"). Schlägt die Prüfung fehl, wird das Ergebnis gesperrt."""
    document = _require_ready(doc_id)
    if not _result_is_current(document):
        raise HTTPException(status_code=404, detail="Kein aktuelles Ergebnis vorhanden - erst schwärzen.")
    if document.get("result_mode") == "redact":
        check = pdf_processor.verify_redaction(Path(document["result_path"]), document, document["result_targets"])
    else:
        import json
        table = json.loads(Path(document["mapping_table_path"]).read_text(encoding="utf-8"))
        check = pdf_processor.verify_pseudonymization(Path(document["result_path"]), table)
    audit.log(doc_id, "reverified", {"passed": check.passed})
    if not check.passed:
        _invalidate_result(document)
        raise _locked("Erneute Prüfung fehlgeschlagen; Ergebnis gesperrt.", check)
    document["verification"] = check.dict()
    return {"verification": check.dict()}


@app.get("/documents/{doc_id}/audit-report")
def get_audit_report(doc_id: str):
    return audit.report(doc_id)


@app.get("/documents/{doc_id}/result/download")
def download_result(doc_id: str):
    document = DOCUMENTS.get(doc_id)
    # Nur ein geprüftes Ergebnis, das zur aktuellen Auswahl passt
    if not document or not _result_is_current(document):
        raise HTTPException(status_code=404, detail="Kein aktuelles, geprüftes Ergebnis vorhanden - erst schwärzen.")
    filename ="pseudonymisiert.pdf" if document.get("result_mode") == "pseudonymize" else "geschwaerzt.pdf"
    return FileResponse(document["result_path"], media_type="application/pdf", filename=filename)


@app.get("/documents/{doc_id}/result/mapping-download")
def download_mapping(doc_id: str):
    document = DOCUMENTS.get(doc_id)
    if not document or not _result_is_current(document) or not document.get("mapping_table_path"):
        raise HTTPException(status_code=404, detail="Keine Zuordnungstabelle vorhanden.")
    return FileResponse(
        document["mapping_table_path"], media_type="application/json", filename="pseudonym-schluessel.json"
    )


def _downloads_dir() -> Path:
    """Windows-Ordner "Downloads" des angemeldeten Benutzers (auch wenn er
    verschoben wurde), sonst <Benutzerordner>/Downloads."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
            folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                             (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            path_ptr = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None,
                                                          ctypes.byref(path_ptr)) == 0:
                result = Path(path_ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                return result
        except Exception:
            log.exception("Downloads-Ordner nicht ermittelbar, nehme Standardpfad")
    return Path.home() / "Downloads"


@app.post("/documents/{doc_id}/result/save")
def save_result(doc_id: str, kind: str = "pdf"):
    """
    v37: Speichert das Ergebnis direkt im Downloads-Ordner und zeigt es im
    Explorer. Ersetzt den Download über das App-Fenster (v36), der unter
    Windows nicht zuverlässig funktionierte - der Sidecar hat die Datei
    ohnehin schon und kann sie selbst kopieren.
    """
    import shutil
    import subprocess

    document = DOCUMENTS.get(doc_id)
    if not document or not _result_is_current(document):
        raise HTTPException(status_code=404, detail="Kein aktuelles, geprüftes Ergebnis vorhanden - erst schwärzen.")
    if kind == "mapping":
        source = document and document.get("mapping_table_path")
        name = "pseudonym-schluessel.json"
    else:
        source = document and document.get("result_path")
        name = "pseudonymisiert.pdf" if document and document.get("result_mode") == "pseudonymize" else "geschwaerzt.pdf"
    if not source:
        raise HTTPException(status_code=404, detail="Kein Ergebnis vorhanden - erst finalisieren.")

    try:
        target_dir = _downloads_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        stem, ext = os.path.splitext(name)
        target = target_dir / name
        i = 1
        while target.exists():
            target = target_dir / f"{stem} ({i}){ext}"
            i += 1
        shutil.copyfile(source, target)
    except Exception as e:
        log.exception("Speichern im Downloads-Ordner fehlgeschlagen")
        raise HTTPException(status_code=500, detail=f"Speichern fehlgeschlagen: {e}")

    log.info(f"Ergebnis gespeichert: {target}")
    audit.log(doc_id, "saved", {"kind": kind})
    if sys.platform == "win32":
        try:
            subprocess.Popen(f'explorer /select,"{target}"')
        except Exception:
            log.exception("Explorer konnte nicht geöffnet werden")
    return {"path": str(target)}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_port_file(port: int):
    (DATA_DIR / "sidecar.port").write_text(str(port))


def _exit_when_parent_closes_stdin():
    """Mit --exit-with-parent gestartet (von Tauri bzw. dem Vite-Dev-Server):
    Endet der Elternprozess, schließt sich unsere stdin-Leitung, und der
    Sidecar beendet sich selbst. So bleibt nach dem Schließen der App
    kein verwaister Python-Prozess mit mehreren GB Modellen im Speicher."""
    try:
        while sys.stdin.buffer.read(1024):
            pass
    except Exception:
        pass
    log.info("Elternprozess beendet - Sidecar wird geschlossen.")
    # os._exit überspringt atexit - Arbeitsordner deshalb hier löschen
    workdir.cleanup_session()
    os._exit(0)


if __name__ == "__main__":
    multiprocessing.freeze_support()  # nötig für die PyInstaller-.exe
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[0]) if args else _free_port()
    _write_port_file(port)
    if "--exit-with-parent" in sys.argv and sys.stdin is not None:
        threading.Thread(target=_exit_when_parent_closes_stdin, daemon=True).start()
    log.info(f"Sidecar startet auf Port {port} ({'gebündelte .exe' if FROZEN else 'Python'}), "
             f"Log und Daten in {DATA_DIR}")
    # Maschinenlesbare Zeile für Tauri / Vite - Format nicht ändern.
    print(f"SIDECAR_PORT={port}", flush=True)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        # Ohne Konsole (z.B. Fenster-Build) gibt es kein stdout - dann
        # nicht in uvicorns Standard-Logging schreiben lassen.
        log_config=None if sys.stdout is None else uvicorn.config.LOGGING_CONFIG,
    )
