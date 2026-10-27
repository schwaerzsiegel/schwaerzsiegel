"""
Native Textextraktion mit Koordinaten (PyMuPDF) statt pauschalem OCR,
echte Entfernung der Textebene bei der Redaction (nicht nur optisches
Überdecken) sowie Bereinigung von Metadaten und Revisionshistorie.
"""

import tempfile
from collections import OrderedDict
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import fitz  # PyMuPDF

# Phase 1: gemeinsame Schwärzungsroutine und Markierungsmodell. Der Import
# von redaction schaltet außerdem enge Zeichenboxen für alle Textauswertungen ein.
from core import marks, redaction
from core.redaction import VerificationResult, metadata_is_clean as _metadata_is_clean  # noqa: F401

# Rohe PDF-Bytes pro Dokument-ID - bewusst KEIN offenes, mutierbares
# fitz.Document mehr (siehe render_page_image()-Docstring für den Bug,
# den das verursacht hat). Jede Operation öffnet sich daraus ihre eigene,
# unabhängige Kopie.
_RAW_BYTES: dict[str, bytes] = {}
# Bounded PNG cache: repeated page visits avoid costly rasterization.
# Cache entries contain only rendered original-page previews, never redacted output.
_PREVIEW_CACHE_LIMIT = 32 * 1024 * 1024
_PREVIEW_CACHE: OrderedDict[tuple[str, int, float], bytes] = OrderedDict()
_PREVIEW_CACHE_SIZE = 0
_PREVIEW_LOCK = Lock()


def clear_preview_cache(doc_id: str | None = None) -> None:
    """Clear all cached previews or only those belonging to one document."""
    global _PREVIEW_CACHE_SIZE
    with _PREVIEW_LOCK:
        keys = list(_PREVIEW_CACHE) if doc_id is None else [k for k in _PREVIEW_CACHE if k[0] == doc_id]
        for key in keys:
            _PREVIEW_CACHE_SIZE -= len(_PREVIEW_CACHE.pop(key))


def _cache_preview(key: tuple[str, int, float], png: bytes) -> None:
    global _PREVIEW_CACHE_SIZE
    if len(png) > _PREVIEW_CACHE_LIMIT:
        return
    with _PREVIEW_LOCK:
        old = _PREVIEW_CACHE.pop(key, None)
        if old is not None:
            _PREVIEW_CACHE_SIZE -= len(old)
        while _PREVIEW_CACHE and _PREVIEW_CACHE_SIZE + len(png) > _PREVIEW_CACHE_LIMIT:
            _, evicted = _PREVIEW_CACHE.popitem(last=False)
            _PREVIEW_CACHE_SIZE -= len(evicted)
        _PREVIEW_CACHE[key] = png
        _PREVIEW_CACHE_SIZE += len(png)



@dataclass
class Page:
    index: int
    words: list[dict] = field(default_factory=list)  # {id, text, bbox, line, source: "native"|"ocr"}
    # Alle Markierungen der Seite (Erkennung + manuell), siehe core/marks.py
    suggestions: list[dict] = field(default_factory=list)
    geometry: dict = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)


def detect_embedded_images(pdf_page: "fitz.Page") -> list[dict]:
    """
    Schlanker Ansatz statt YOLO/CLIP/RetinaFace-Kaskade: jedes eingebettete
    Bild auf der Seite wird pauschal als Review-Kandidat markiert, ohne
    Klassifikation (Foto vs. Logo vs. Unterschrift). Mehr False Positives
    (auch Firmenlogos werden markiert), aber technisch trivial und lässt
    nichts durchrutschen. Signaturen, die als Vektor-/Freihandzeichnung
    statt eingebettetem Bild vorliegen, werden davon NICHT erfasst -
    dafür bleibt die manuelle Markierung (wie im MVP) der Weg.
    """
    suggestions = []
    page_box = (pdf_page.rect * pdf_page.derotation_matrix).normalize()
    page_area = max(page_box.get_area(), 1.0)
    for xref, *_ in pdf_page.get_images(full=True):
        rects = pdf_page.get_image_rects(xref)
        for rect in rects:
            # Ein Bild über (fast) die ganze Seite ist meist der Scan selbst
            # oder ein Seitenhintergrund. Phase 10 zeigte es noch als nicht
            # vorausgewählten Vorschlag; Phase 16b lässt es weg, weil der
            # Rahmen über der ganzen Seite stört (Rückmeldung des Nutzers).
            # Die Angaben darauf schlägt die Texterkennung einzeln vor,
            # Unterschriften und Stempel core/handschrift.py; eine ganze Seite
            # lässt sich mit dem Rechteck-Werkzeug schwärzen.
            if (fitz.Rect(rect) & page_box).get_area() >= 0.85 * page_area:
                continue
            suggestions.append({
                "id": str(uuid4()),
                "label": "Bild (ungeprüft)",
                "text": "Eingebettetes Bild",
                "reason": None,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "word_ids": [],
                "confidence": 0.5,  # bewusst mittig: weder automatisch bestätigt noch versteckt
                "source": "image_heuristik",
                "default_selected": True,
                "also_detected_as": [],
            })
    return suggestions


def extract_page(pdf_page: "fitz.Page", i: int, timer=None, on_step=None, detect_images: bool = True) -> Page:
    """
    Eine Seite lesen (nativ oder per OCR) + eingebettete Bilder markieren.
    Seitenweise ausgelagert (v31), damit main.py nach jeder Seite den
    Fortschritt melden und die Zeit pro Schritt messen kann.

    Phase 1: Jedes Wort trägt seine Zeile ("line"), damit mehrzeilige
    Treffer eine Box pro Zeile bekommen. Fehlt Tesseract, bleibt eine
    Scan-Seite ohne Wörter benutzbar (Bereiche lassen sich manuell
    markieren), statt die ganze Verarbeitung abzubrechen.
    """
    from contextlib import nullcontext

    def step(name):
        if on_step:
            on_step(name)
        return timer.step(name) if timer else nullcontext()

    page = Page(index=i, geometry=marks.geometry(pdf_page))
    with step("Text lesen"):
        raw_words = pdf_page.get_text("words")
    if raw_words:
        page.words = [
            {"id": f"p{i}_w{idx}", "text": w[4], "bbox": list(w[:4]), "line": f"{w[5]}-{w[6]}",
             "source": "native"}
            for idx, w in enumerate(raw_words)
        ]
    else:
        # Keine extrahierbare Textebene -> Scan-PDF, OCR-Fallback.
        # (Im MVP war pytesseract importiert, aber der Aufruf fehlte -
        # das wird hier nachgeholt.)
        try:
            from core import ocr
            with step("OCR"):
                page.words = ocr.extract_words(pdf_page, page_index=i)
        except Exception as e:
            reason = str(e).split(". ")[0]
            page.notices.append(
                f"Seite {i + 1}: Texterkennung (OCR) nicht möglich ({reason}). "
                "Bereiche lassen sich trotzdem manuell markieren und schwärzen."
            )
    if detect_images:
        with step("Bilder"):
            page.suggestions.extend(detect_embedded_images(pdf_page))
        if not raw_words:
            # Phase 16: Scan-Seite - Unterschriften, Stempel, Handschrift ("Tinte ohne Text")
            from core import handschrift
            with step("Handschrift"):
                page.suggestions.extend(handschrift.detect(pdf_page, page.words))
    return page


def register(doc_id: str, pdf_bytes: bytes) -> None:
    """Rohe Bytes für spätere Render-/Finalize-Schritte merken."""
    clear_preview_cache(doc_id)
    _RAW_BYTES[doc_id] = pdf_bytes


def forget(doc_id: str) -> None:
    """Dokument aus dem Speicher entfernen (Bytes und Vorschaubilder)."""
    clear_preview_cache(doc_id)
    _RAW_BYTES.pop(doc_id, None)


def load(pdf_bytes: bytes) -> tuple[str, list[Page]]:
    pdf_bytes, _notices = redaction.prepare_document(pdf_bytes)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [extract_page(pdf_page, i) for i, pdf_page in enumerate(doc)]
    for page in pages:
        marks.init_auto(page)
    doc_id = str(uuid4())
    # Nur die rohen Bytes werden gemerkt, kein offenes, mutierbares
    # Document-Objekt - siehe Kommentar bei render_page_image() für den
    # Grund (Bug: finalize hat sonst dasselbe Objekt verändert, das auch
    # fürs Rendern der "Original"-Ansicht benutzt wurde).
    register(doc_id, pdf_bytes)
    doc.close()
    return doc_id, pages


def render_external_page_image(pdf_path: str, page_num: int, zoom: float = 1.5) -> bytes:
    """
    Rendert eine Seite aus einer bereits auf Festplatte gespeicherten PDF
    (dem fertigen geschwärzten/pseudonymisierten Ergebnis) - anders als
    render_page_image() nicht aus dem In-Memory-_STORE, weil das fertige
    Ergebnis nach dem Finalisieren nicht als offenes Dokument im
    laufenden Zustand weitergeführt wird, sondern nur als Datei existiert.
    """
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def render_page_image(doc_id: str, page_num: int, zoom: float = 1.5) -> bytes:
    """
    Rendert eine Seite als PNG für die Vorschau im UI. Der zoom-Faktor
    wird bewusst vom Frontend als Query-Parameter vorgegeben (nicht hier
    fest verdrahtet), damit das Frontend beim Positionieren der
    Overlay-Boxen immer den exakt gleichen Wert kennt, mit dem gerendert
    wurde - bbox-Koordinaten liegen in PDF-Punkten vor, das Bild in
    Pixeln = Punkte * zoom.

    WICHTIG: Öffnet bei jedem Aufruf eine FRISCHE Kopie aus den rohen
    Bytes, statt ein einziges geteiltes Document-Objekt wiederzuverwenden.
    Vorher teilte sich diese Funktion ein Objekt mit finalize_redaction(),
    das dort direkt verändert (geschwärzt) wurde - nach dem Finalisieren
    zeigte die "Original"-Ansicht dadurch fälschlich die bereits
    geschwärzte Version. Frisches Öffnen pro Aufruf ist etwas teurer,
    aber schließt diese Fehlerklasse grundsätzlich aus.

    Gedrehte Seiten (Phase 1): Das Bild zeigt die Seite gedreht, wie ein
    PDF-Betrachter. Markierungen liefert die API deshalb in denselben
    Anzeige-Koordinaten (core/marks.py, to_display) - vorher lagen sie bei
    gedrehten Seiten an der falschen Stelle.
    """
    key = (doc_id, page_num, zoom)
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(key)
            return cached
    # Render outside lock so independent requests do not block each other.
    with fitz.open(stream=_RAW_BYTES[doc_id], filetype="pdf") as doc:
        page = doc[page_num]
        png_bytes = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    _cache_preview(key, png_bytes)
    return png_bytes


def generate_pseudonym_mapping(document: dict) -> list[dict]:
    """
    Erzeugt für jede bestätigte Markierung ein konsistentes Pseudonym -
    derselbe Originaltext bekommt überall im Dokument dasselbe Pseudonym
    (analog zu Schwärzwerks Modell: "Klaus Müller wird Nummer 237").

    Die zurückgegebene Tabelle enthält Klartext-Originalwerte und ist
    bewusst vom Dokument getrennt zu behandeln (Art. 4 Nr. 5 DSGVO:
    Pseudonym und Schlüssel gehören nicht ins selbe Archiv) - main.py
    speichert sie unter einem eigenen Dateinamen, nicht neben dem PDF.
    """
    counters: dict[str, int] = {}
    seen: dict[tuple[str, str], str] = {}
    mapping_table = []

    for page_data in document["pages"]:
        for suggestion in page_data.suggestions:
            if suggestion["status"] != marks.CONFIRMED:
                continue
            key = (suggestion["label"], suggestion.get("text", "").strip().lower())
            if key not in seen:
                counters[suggestion["label"]] = counters.get(suggestion["label"], 0) + 1
                seen[key] = f"[{suggestion['label']}-{counters[suggestion['label']]}]"
                if suggestion.get("text"):
                    mapping_table.append({
                        "original": suggestion["text"],
                        "pseudonym": seen[key],
                        "label": suggestion["label"],
                    })
            suggestion["_pseudonym"] = seen[key]

    return mapping_table


def result_file(prefix: str, doc_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"{prefix}-{doc_id}.pdf"


def finalize_pseudonymization(document: dict) -> tuple[Path, list[dict]]:
    """
    Wie finalize_redaction(), aber statt reiner Schwärzung wird das
    Pseudonym als Ersatztext in den ersten Bereich jeder Markierung
    gesetzt. Gleiche Routine (core/redaction.apply_marks) wie beim
    Schwärzen, nur mit grauer Fläche und Ersatztext.
    """
    doc_id = document["_doc_id"]
    mapping_table = generate_pseudonym_mapping(document)
    with fitz.open(stream=_RAW_BYTES[doc_id], filetype="pdf") as doc:
        targets = redaction.apply_marks(
            doc, marks.confirmed_by_page(document), fill=redaction.PSEUDONYM_FILL,
            replacement=lambda m: m.get("_pseudonym", "[PSEUDONYMISIERT]"),
        )
        document["result_actions"] = redaction.sanitize(doc, targets, [m["original"] for m in mapping_table])
        output_path = result_file("pseudonymized", doc_id)
        redaction.save(doc, output_path)
    return output_path, mapping_table


def verify_pseudonymization(output_path: Path, mapping_table: list[dict]) -> VerificationResult:
    """
    Sicherheitscheck für den Pseudonymisierungs-Modus: hier geht es nicht
    um "kein Text mehr da" (das Pseudonym IST Text), sondern darum, dass
    kein ORIGINALWERT mehr im Dokument auffindbar ist. Zusätzlich dieselben
    dokumentweiten Prüfungen wie beim Schwärzen (Annotationen, Links,
    Anhänge, Metadaten, Lesezeichen).
    """
    general = redaction.verify(output_path, [], [m["original"] for m in mapping_table])
    with fitz.open(output_path) as doc:
        full_text = "".join(page.get_text() for page in doc)
    residual = [
        f"Original '{item['original']}' -> Pseudonym '{item['pseudonym']}' noch im Klartext gefunden"
        for item in mapping_table
        if item["original"] in full_text
    ]
    problems = residual + general.residual_text_found
    return VerificationResult(
        passed=not problems,
        residual_text_found=problems,
        metadata_clean=general.metadata_clean,
        warnings=[],
        checks=general.checks + [{"key": "originals", "label": "Keine Originalwerte im Text",
                                  "ok": not residual, "detail": "keine gefunden" if not residual
                                  else f"{len(residual)} Befund(e)"}],
    )


def finalize_redaction(document: dict) -> tuple[Path, list[dict]]:
    """
    Wendet echte Redaction an (core/redaction.apply_marks): entfernt die
    Textebene, Bildpixel und vollständig abgedeckte Vektorgrafik in allen
    bestätigten Markierungen - manuell oder erkannt, mit derselben Routine.
    Danach Links, Anhänge, Metadaten usw. bereinigen und speichern.

    Gibt den Pfad und die geschwärzten Zielbereiche zurück ({"page",
    "rect"/"bbox", "mark_id", "kind"}); die durchgeführten
    Bereinigungsschritte stehen in document["result_actions"].
    """
    doc_id = document["_doc_id"]
    with fitz.open(stream=_RAW_BYTES[doc_id], filetype="pdf") as doc:
        targets = redaction.apply_marks(doc, marks.confirmed_by_page(document))
        document["result_actions"] = redaction.sanitize(doc, targets, marks.marked_texts(document))
        output_path = result_file("redacted", doc_id)
        redaction.save(doc, output_path)
    for t in targets:
        t["bbox"] = t["rect"]
    return output_path, targets


def verify_redaction(output_path: Path, document: dict, targets: list[dict]) -> VerificationResult:
    """Unabhängige Prüfung der gespeicherten Datei inkl. Hinweisen auf
    weitere Vorkommen und mitgelöschten Nachbartext."""
    return redaction.verify(
        output_path, targets, marks.marked_texts(document),
        source_bytes=_RAW_BYTES.get(document["_doc_id"]),
    )


def verify_no_residual_data(output_path: Path, selected: list[dict]) -> VerificationResult:
    """Prüfung ohne Dokumentkontext (nur Zielbereiche), z. B. für Tests.
    selected: [{"page", "bbox"}]"""
    return redaction.verify(output_path, selected, [s.get("text", "") for s in selected])
