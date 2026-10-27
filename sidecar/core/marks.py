"""
Einheitliches Markierungsmodell (Phase 1): manuell gezogene Bereiche,
manuell ausgewählter Text sowie Treffer aus Regeln und KI landen in
derselben Liste pro Seite (Page.suggestions) und werden mit derselben
Routine geschwärzt (core/redaction.py).

Eine Markierung:
  id, label (Kategorie), text, kind ("text" | "area"),
  rects   - Schwärzungsbereiche in ungedrehten PDF-Koordinaten,
            bei Text eine Box pro Zeile,
  origin  - "manual" | "rule" | "ai",
  status  - "suggested" | "confirmed" | "rejected"
            ("redacted" wird nur in der Ausgabe abgeleitet, solange ein
            gültiges, geprüftes Ergebnis zur aktuellen Auswahl existiert),
  sowie die bisherigen Felder der Erkennung (confidence, source, ...).

Das Frontend arbeitet in Anzeige-Koordinaten (gedrehte Seite in Punkten);
public() und from_display() rechnen um.
"""

from __future__ import annotations

import hashlib
import math
from uuid import uuid4

import fitz

SUGGESTED, CONFIRMED, REJECTED, REDACTED = "suggested", "confirmed", "rejected", "redacted"
EDITABLE_STATUSES = {SUGGESTED, CONFIRMED, REJECTED}
MAX_RECTS = 200
MAX_LABEL = 80

_ORIGIN_BY_SOURCE = {"regex": "rule", "image_heuristik": "rule", "handschrift_heuristik": "rule", "gliner": "ai",
                     "manual": "manual"}


def geometry(pdf_page: fitz.Page) -> dict:
    """Was zur Umrechnung zwischen Anzeige und PDF gebraucht wird, ohne
    das Dokument später erneut öffnen zu müssen."""
    bounds = (pdf_page.rect * pdf_page.derotation_matrix).normalize()
    return {
        "width": pdf_page.rect.width,
        "height": pdf_page.rect.height,
        "rotation": pdf_page.rotation,
        "rot": tuple(pdf_page.rotation_matrix),
        "derot": tuple(pdf_page.derotation_matrix),
        "bounds": tuple(bounds),
    }


def _rect(values) -> fitz.Rect:
    return fitz.Rect(*values)


def _as_list(r: fitz.Rect) -> list[float]:
    return [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]


def _union(rects: list[list[float]]) -> list[float]:
    if not rects:
        return [0, 0, 0, 0]
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


def line_rects(words: list[dict]) -> list[list[float]]:
    """Eine Box pro Textzeile statt einer Box über alle Zeilen - sonst würde
    bei mehrzeiligen Treffern auch fremder Text daneben geschwärzt."""
    groups: dict[str, list[list[float]]] = {}
    order: list[str] = []
    for w in words:
        key = w.get("line")
        if key is None:
            # Ohne Zeileninfo: nach senkrechter Mitte gruppieren
            mid = (w["bbox"][1] + w["bbox"][3]) / 2
            key = f"y{round(mid / 3)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(w["bbox"])
    return [[round(v, 2) for v in _union(groups[k])] for k in order]


def to_display(page, rect) -> list[float]:
    return _as_list((_rect(rect) * fitz.Matrix(page.geometry["rot"])).normalize())


def from_display(page, rect) -> list[float] | None:
    """Anzeige -> ungedrehte PDF-Koordinaten, auf die Seite beschnitten.
    None bei ungültigen oder zu kleinen Rechtecken."""
    if len(rect) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in rect):
        return None
    r = (_rect(rect).normalize() * fitz.Matrix(page.geometry["derot"])).normalize()
    r &= _rect(page.geometry["bounds"])
    if r.is_empty or r.width < 1 or r.height < 1:
        return None
    return _as_list(r)


def init_auto(page) -> None:
    """Ergänzt die Treffer der Erkennung um die Felder des Markierungsmodells."""
    words_by_id = {w["id"]: w for w in page.words}
    for s in page.suggestions:
        if "status" in s:
            continue
        words = [words_by_id[i] for i in s.get("word_ids", []) if i in words_by_id]
        if words:
            s["kind"] = "text"
            s["rects"] = line_rects(words)
        else:
            s["kind"] = "area"
            s["rects"] = [list(s["bbox"])]
        s["origin"] = "ai" if s.get("llm_reviewed") else _ORIGIN_BY_SOURCE.get(s.get("source"), "rule")
        s["status"] = SUGGESTED


def new_manual(page, *, kind: str, rects: list[list[float]], text: str = "", label: str | None = None,
               word_ids: list[str] | None = None) -> dict:
    return {
        "id": str(uuid4()),
        "label": (label or ("Markierter Text" if kind == "text" else "Manueller Bereich"))[:MAX_LABEL],
        "text": text,
        "bbox": _union(rects),
        "rects": rects,
        "kind": kind,
        "word_ids": word_ids or [],
        "confidence": 1.0,
        "source": "manual",
        "origin": "manual",
        # Selbst gezogen = gewollt. Geschwärzt wird trotzdem erst nach der
        # Freigabe der gesamten Auswahl und "Schwärzen".
        "status": CONFIRMED,
        "default_selected": True,
        "also_detected_as": [],
    }


def set_rects(mark: dict, rects: list[list[float]]) -> None:
    """Geänderte Boxen passen nicht mehr zu einzelnen Wörtern - die
    Markierung wird zum Bereich (alles darin wird entfernt)."""
    mark["rects"] = rects
    mark["bbox"] = _union(rects)
    if mark.get("kind") == "text":
        mark["kind"] = "area"
        mark["edited"] = True


def public(mark: dict, page, redacted: bool = False) -> dict:
    rects = [to_display(page, r) for r in mark["rects"]]
    status = REDACTED if redacted and mark["status"] == CONFIRMED else mark["status"]
    return {
        "id": mark["id"],
        "page": page.index,
        "label": mark["label"],
        "text": mark.get("text", ""),
        "kind": mark.get("kind", "area"),
        "origin": mark.get("origin", "rule"),
        "status": status,
        "rects": rects,
        "bbox": _union(rects),
        "confidence": mark.get("confidence", 1.0),
        "source": mark.get("source", ""),
        "default_selected": mark.get("default_selected", True),
        "edited": mark.get("edited", False),
        # Phase 2: warum die Erkennung das vorschlägt (Kontext, Prüfziffer, KI ...)
        "reason": mark.get("reason"),
        "llm_reviewed": mark.get("llm_reviewed"),
        "llm_reason": mark.get("llm_reason"),
        "also_detected_as": mark.get("also_detected_as", []),
    }


def public_word(word: dict, page) -> dict:
    return {"id": word["id"], "text": word["text"], "bbox": to_display(page, word["bbox"]),
            "line": word.get("line")}


def signature(document: dict) -> str:
    """Freigabe-Signatur (Prinzip aus dem MVP): ändert sich irgendetwas an
    der Auswahl - Status, Kategorie, Art oder Koordinaten -, ist eine
    vorherige Freigabe ungültig."""
    values = sorted(
        (m["id"], m["status"], m.get("kind", ""), m.get("label", ""),
         tuple(round(v, 1) for r in m["rects"] for v in r))
        for page in document["pages"]
        for m in page.suggestions
    )
    return hashlib.sha256(repr(values).encode()).hexdigest()


def confirmed_by_page(document: dict) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for page in document["pages"]:
        chosen = [m for m in page.suggestions if m["status"] == CONFIRMED]
        if chosen:
            result[page.index] = chosen
    return result


def marked_texts(document: dict) -> list[str]:
    """Texte der bestätigten Textmarkierungen (für die Suche nach weiteren
    Vorkommen und nach Links/Lesezeichen mit diesen Angaben)."""
    return [m["text"] for page in document["pages"] for m in page.suggestions
            if m["status"] == CONFIRMED and m.get("text") and (m.get("kind") == "text" or m.get("edited"))]
