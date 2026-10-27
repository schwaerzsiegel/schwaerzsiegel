"""
Gemeinsame, sichere Schwärzungsroutine für manuelle und automatisch
erkannte Markierungen sowie die unabhängige Prüfung der gespeicherten
Ergebnisdatei (Phase 1).

Koordinaten: Markierungen liegen in ungedrehten PDF-Koordinaten vor -
dasselbe System wie page.get_text() und add_redact_annot(). Die Umrechnung
in die (gedrehte) Anzeige des Frontends übernimmt core/marks.py.

Warum in zwei Stufen geschwärzt wird:
MuPDF entfernt ein Zeichen, sobald eine Schwärzungsbox etwa 10 % seiner
vollen Zeichenbox überlappt (gemessen mit PyMuPDF 1.24). Bei normalem
Zeilenabstand überlappen sich die Zeichenboxen benachbarter Zeilen um mehr
als das. Eine Box über eine ganze Zeile - oder ein Rechteck mit etwas Rand -
hat deshalb Teile der Nachbarzeilen mitgelöscht, und knapp darunter blieben
angeschnittene Zeichen stehen, die die alte Prüfung als Resttext gemeldet hat
("Schwärzung nicht verifiziert"). Deshalb, für Text- und Bereichsmarkierungen:
  1. Es werden genau die Zeichen entfernt, die im Sinne der Prüfung im
     Bereich liegen (Mittelpunkt darin oder mindestens 25 % ihrer Fläche),
     jeweils über ein schmales Band in ihrer Mitte - das trifft keine
     Nachbarzeile.
  2. Danach wird die Fläche geschwärzt (Bildpixel, Vektorgrafik), ohne
     weiteren Text zu entfernen. Nur knapp angeschnittene Zeichen der
     Nachbarzeile bleiben stehen; ihr angeschnittener Teil liegt unter der
     schwarzen Fläche.
Schwärzung und Prüfung verwenden damit dasselbe Kriterium.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote_plus

import fitz

# Enge Zeichenboxen (Schriftgröße statt Zeilenhöhe) für alle Textauswertungen
# in PyMuPDF. Dadurch überlappen sich die Boxen benachbarter Zeilen nicht
# mehr. MuPDFs eigene Entscheidung beim Schwärzen ändert das nicht.
fitz.TOOLS.set_small_glyph_heights(True)

BLACK = (0.0, 0.0, 0.0)
PSEUDONYM_FILL = (0.92, 0.92, 0.92)

# Ab diesem Anteil seiner Fläche gilt ein verbliebenes Zeichen als Resttext
# eines Schwärzungsbereichs (oder wenn sein Mittelpunkt darin liegt).
# MuPDF entfernt Zeichen schon ab ~10 %, die Prüfung meldet also nur Zeichen,
# die sicher hätten verschwinden müssen.
RESIDUAL_OVERLAP = 0.25
# Stufe 1: je 30 % der Zeichenhöhe oben und unten frei lassen.
_BAND = 0.30
# Mindestgröße einer Markierung in Punkten.
MIN_SIZE = 1.0

_FLAGS = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES


@dataclass
class VerificationResult:
    passed: bool
    # Blockierende Befunde (Name aus der ersten Version beibehalten)
    residual_text_found: list[str] = field(default_factory=list)
    metadata_clean: bool = True
    warnings: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    def dict(self):
        return {
            "passed": self.passed,
            "problems": self.residual_text_found,
            "residual_text_found": self.residual_text_found,
            "metadata_clean": self.metadata_clean,
            "warnings": self.warnings,
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def rect_list(r: fitz.Rect) -> list[float]:
    return [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]


def unrotated_bounds(page: fitz.Page) -> fitz.Rect:
    return (page.rect * page.derotation_matrix).normalize()


def iter_chars(page: fitz.Page):
    """(Zeichen, enge Box, Schreibrichtung) für alle Textzeichen der Seite,
    auch unsichtbare (z. B. OCR-Textebenen)."""
    for block in page.get_text("rawdict", flags=_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            for span in line["spans"]:
                for ch in span["chars"]:
                    yield ch["c"], fitz.Rect(ch["bbox"]), direction


def _covers(target: fitz.Rect, box: fitz.Rect) -> bool:
    """Liegt das Zeichen (box) im Sinne der Prüfung im Bereich target?"""
    if box.is_empty:
        center = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        return target.contains(center)
    inter = fitz.Rect(box) & target
    if inter.is_empty:
        return False
    center = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
    return target.contains(center) or inter.get_area() >= RESIDUAL_OVERLAP * box.get_area()


def _center_in(rect: fitz.Rect, box: fitz.Rect) -> bool:
    return rect.contains(fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))


def _band(box: fitz.Rect, direction) -> fitz.Rect | None:
    """Schmales Band in der Mitte eines Zeichens, quer zur Schreibrichtung
    verkleinert. None bei schräger Schrift (dann Zeichenbox komplett)."""
    dx, dy = abs(direction[0]), abs(direction[1])
    if dx > 0.99:  # waagerechte Schrift
        h = box.height
        return fitz.Rect(box.x0, box.y0 + h * _BAND, box.x1, box.y1 - h * _BAND)
    if dy > 0.99:  # senkrechte Schrift (z. B. gedrehte Seite)
        w = box.width
        return fitz.Rect(box.x0 + w * _BAND, box.y0, box.x1 - w * _BAND, box.y1)
    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _link_text(link: dict) -> str:
    """Ziel eines Links, URL-dekodiert ("Erika%20Mustermann")."""
    return _normalize_text(unquote_plus(link.get("uri") or link.get("file") or ""))


# ---------------------------------------------------------------------------
# Vorbereitung beim Hochladen
# ---------------------------------------------------------------------------

def prepare_document(pdf_bytes: bytes) -> tuple[bytes, list[str]]:
    """
    Bringt das Dokument in einen Zustand, in dem Vorschau, Erkennung,
    Schwärzung und Prüfung dasselbe sehen:
    - bereits vorhandene, noch nicht angewendete Schwärzungsannotationen
      werden angewendet (ihr schwarzer Kasten verdeckt sonst nur Text),
    - Kommentare und Formularfelder werden in den Seiteninhalt übernommen,
      damit ihr sichtbarer Inhalt markiert, geschwärzt und geprüft werden
      kann (versteckte Kommentartexte entfallen dabei),
    - ausgeblendete Ebenen werden eingeblendet - ihr Inhalt wäre sonst
      weder in der Vorschau noch für die Prüfung sichtbar.
    Gibt die neuen Bytes und verständliche Hinweise zurück.
    """
    notices: list[str] = []
    changed = False
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pending = 0
        for page in doc:
            redacts = list(page.annots(types=[fitz.PDF_ANNOT_REDACT]))
            if redacts:
                pending += len(redacts)
                page.apply_redactions()
        if pending:
            changed = True
            notices.append(
                f"Das Dokument enthielt {pending} noch nicht angewendete Schwärzungsmarkierung(en) "
                "aus einem anderen Programm. Sie wurden angewendet."
            )

        annot_count = sum(1 for page in doc for _ in page.annots())
        widget_count = sum(1 for page in doc for _ in page.widgets())
        if annot_count or widget_count:
            changed = True
            try:
                doc.bake(annots=True, widgets=True)
                notices.append(
                    f"{annot_count} Kommentar(e)/Anmerkung(en) und {widget_count} Formularfeld(er) wurden "
                    "in den Seiteninhalt übernommen, damit sie mitgeprüft und geschwärzt werden können."
                )
            except Exception:
                notices.append(
                    "Kommentare/Formularfelder konnten nicht in den Seiteninhalt übernommen werden. "
                    "Sie werden beim Schwärzen vollständig entfernt."
                )

        ocgs = doc.get_ocgs()
        if ocgs and any(not info.get("on", True) for info in ocgs.values()):
            doc.set_layer(-1, on=list(ocgs), off=[])
            changed = True
            notices.append(
                "Das Dokument enthielt ausgeblendete Ebenen. Sie werden angezeigt, damit ihr Inhalt "
                "geprüft und geschwärzt werden kann."
            )

        if doc.embfile_count():
            notices.append(
                f"Das Dokument enthält {doc.embfile_count()} Dateianhang/-anhänge. "
                "Sie werden beim Schwärzen entfernt."
            )

        if not changed:
            return pdf_bytes, notices
        return doc.tobytes(), notices


# ---------------------------------------------------------------------------
# Schwärzen
# ---------------------------------------------------------------------------

def apply_marks(
    doc: fitz.Document,
    marks_by_page: dict[int, list[dict]],
    *,
    fill=BLACK,
    replacement=None,
) -> list[dict]:
    """
    Wendet bestätigte Markierungen an. marks_by_page: Seitenindex -> Liste
    von Markierungen mit "kind" ("text" | "area") und "rects" (ungedreht).
    replacement(mark) -> Ersatztext oder None (Pseudonymisierung).
    Gibt die geschwärzten Zielbereiche zurück: {"page", "rect", "mark_id", "kind"}.
    """
    targets: list[dict] = []
    for page_index, marks in sorted(marks_by_page.items()):
        page = doc[page_index]
        bounds = unrotated_bounds(page)
        rects: list[tuple[fitz.Rect, dict]] = []
        for mark in marks:
            for r in mark["rects"]:
                rect = fitz.Rect(r) & bounds
                if rect.is_empty or rect.width < MIN_SIZE / 2 or rect.height < MIN_SIZE / 2:
                    continue
                rects.append((rect, mark))
        if not rects:
            continue

        # Stufe 1: genau die Zeichen entfernen, die im Bereich liegen.
        bands = 0
        for ch, box, direction in iter_chars(page):
            if ch.isspace() or not any(_covers(rect, box) for rect, _ in rects):
                continue
            band = _band(box, direction) or box
            if band.is_empty:
                continue
            page.add_redact_annot(band)
            bands += 1
        if bands:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )

        # Stufe 2: Flächen schwärzen, ohne weiteren Text zu entfernen.
        # "Vollständig abgedeckte" Vektorgrafik entfernt MuPDF 1.24 nicht
        # zuverlässig (gemessen: Kurven einer Unterschrift bleiben stehen).
        # Wo freihandartige Grafik im Bereich liegt, wird sie deshalb ganz
        # entfernt, sobald sie den Bereich berührt.
        freehand = [(rect, mark) for rect, mark in rects if _freehand_in(page, [rect], fill)]
        regular = [(rect, mark) for rect, mark in rects if not any(rect is f for f, _ in freehand)]
        seen_marks: set[str] = set()
        for group, graphics in (
            (regular, fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED),
            (freehand, fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED),
        ):
            if not group:
                continue
            for rect, mark in group:
                text = None
                if replacement and mark["id"] not in seen_marks:
                    text = replacement(mark)
                seen_marks.add(mark["id"])
                if text:
                    page.add_redact_annot(rect, text=text, fontsize=9, fill=fill, text_color=(0, 0, 0))
                else:
                    page.add_redact_annot(rect, fill=fill)
                targets.append({"page": page_index, "rect": rect_list(rect), "mark_id": mark["id"],
                                "kind": mark.get("kind", "area")})
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_PIXELS,
                graphics=graphics,
                text=fitz.PDF_REDACT_TEXT_NONE,
            )
    return targets


def sanitize(doc: fitz.Document, targets: list[dict], marked_texts: list[str]) -> list[str]:
    """Entfernt Informationsquellen außerhalb des sichtbaren Seiteninhalts.
    Gibt verständliche Beschreibungen der durchgeführten Schritte zurück."""
    actions: list[str] = []
    marked = [_normalize_text(t) for t in marked_texts if len(_normalize_text(t)) >= 3]
    by_page: dict[int, list[fitz.Rect]] = {}
    for t in targets:
        by_page.setdefault(t["page"], []).append(fitz.Rect(t["rect"]))

    removed_links = launch_links = 0
    for page_index, page in enumerate(doc):
        rects = by_page.get(page_index, [])
        for link in page.get_links():
            area = fitz.Rect(link["from"])
            uri = _link_text(link)
            if link.get("kind") == fitz.LINK_LAUNCH:
                page.delete_link(link)
                launch_links += 1
            elif any(area.intersects(r) for r in rects) or any(m in uri for m in marked):
                page.delete_link(link)
                removed_links += 1
    if removed_links:
        actions.append(f"{removed_links} Link(s) im geschwärzten Bereich entfernt")
    if launch_links:
        actions.append(f"{launch_links} Link(s) zum Starten von Programmen/Dateien entfernt")

    # Normalerweise schon beim Hochladen in den Seiteninhalt übernommen;
    # was übrig ist (z. B. wenn das nicht ging), wird entfernt.
    leftover = 0
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
            leftover += 1
        for widget in list(page.widgets()):
            page.delete_widget(widget)
            leftover += 1
    if leftover:
        actions.append(f"{leftover} Kommentar(e)/Formularfeld(er) entfernt")

    names = doc.embfile_names()
    for name in names:
        doc.embfile_del(name)
    if names:
        actions.append(f"{len(names)} Dateianhang/-anhänge entfernt")
    actions.extend(_remove_associated_files(doc))

    toc = doc.get_toc()
    if toc and any(m in _normalize_text(entry[1]) for entry in toc for m in marked):
        doc.set_toc([])
        actions.append("Lesezeichen entfernt (enthielten geschwärzte Angaben)")

    doc.scrub(
        attached_files=True, clean_pages=True, embedded_files=True, hidden_text=False,
        javascript=True, metadata=True, redactions=False, redact_images=0, remove_links=False,
        reset_fields=False, reset_responses=True, thumbnails=True, xml_metadata=True,
    )
    # scrub() leert JavaScript nur, die Aktionen selbst bleiben stehen
    # (gemessen mit PyMuPDF 1.24) - deshalb ausdrücklich neutralisieren.
    _neutralize_active_content(doc)
    doc.set_metadata({})
    if doc.xref_xml_metadata():
        doc.del_xml_metadata()
    actions.append("Metadaten, XMP-Metadaten, JavaScript und Vorschaubilder entfernt")
    return actions


_ACTIVE_ACTION_TYPES = {"/JavaScript", "/Launch"}


def _is_active_action(doc: fitz.Document, xref: int, path: str = "") -> bool:
    kind, value = doc.xref_get_key(xref, path + "S")
    return kind == "name" and value in _ACTIVE_ACTION_TYPES


def _drop_keys(doc: fitz.Document, xref: int, drop: set[str]) -> None:
    """Objekt ohne die genannten Schlüssel neu schreiben. Ein auf "null"
    gesetzter Schlüssel bleibt beim Speichern als "/JS null" stehen (gemessen
    mit PyMuPDF 1.24) - die Prüfung findet ihn dann zu Recht."""
    keys = doc.xref_get_keys(xref)
    if not drop.intersection(keys):
        return
    parts = []
    for key in keys:
        if key in drop:
            continue
        kind, value = doc.xref_get_key(xref, key)
        parts.append(f"/{key} {fitz.get_pdf_str(value) if kind == 'string' else value}")
    doc.update_object(xref, "<<" + " ".join(parts) + ">>")


def _remove_associated_files(doc: fitz.Document) -> list[str]:
    """
    Phase 5 (Befund mit einem echten Testdokument): "Zugeordnete Dateien"
    (/AF, PDF 2.0 bzw. PDF/A-3) halten eine eingebettete Datei auch dann im
    Dokument, wenn sie aus der Anhangsliste gelöscht wurde - z.B. ein
    C2PA-Manifest ("Content Credentials" mit Herkunfts- und Bearbeitungs-
    angaben). Verweise entfernen und die Dateiinhalte leeren.
    """
    c2pa = False
    removed = 0
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        if not re.search(r"/(AF|EF|EmbeddedFile)\b", obj):
            continue
        try:
            if doc.xref_get_key(xref, "Type") == ("name", "/EmbeddedFile"):
                c2pa = c2pa or "c2pa" in doc.xref_get_key(xref, "Subtype")[1].lower()
                doc.update_stream(xref, b"")
                doc.update_object(xref, "<<>>")
                removed += 1
                continue
            if doc.xref_get_key(xref, "AFRelationship")[1].lower().endswith("c2pa_manifest"):
                c2pa = True
            _drop_keys(doc, xref, {"AF", "EF"})
        except Exception:
            continue
    if not removed:
        return []
    what = "Herkunftsangaben (C2PA-Manifest)" if c2pa else f"{removed} zugeordnete Datei(en)"
    return [f"{what} entfernt"]


def _neutralize_active_content(doc: fitz.Document) -> None:
    """
    Phase 5: nur die aktiven Teile entfernen, nicht das ganze Objekt. Bis
    dahin wurde jedes Objekt geleert, in dem irgendwo "/S /JavaScript"
    stand - bei einer Aktion direkt im Katalog, in einer Seite oder einem
    Link war damit das Dokument, die Seite bzw. der Link zerstört. Dokument-
    JavaScript im Namensbaum direkt im Katalog blieb dagegen stehen, und die
    Prüfung sperrte das Ergebnis.
    """
    catalog = doc.pdf_catalog()
    kind, names = doc.xref_get_key(catalog, "Names")
    if kind == "dict":
        # Ein "null" in einem verschachtelten Eintrag bliebe beim Speichern
        # stehen - als eigenes Objekt wird /JavaScript unten wirklich entfernt.
        own = doc.get_new_xref()
        doc.update_object(own, names)
        doc.xref_set_key(catalog, "Names", f"{own} 0 R")
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        if not re.search(r"/(JavaScript|JS|Launch|AA)\b", obj):
            continue
        try:
            if _is_active_action(doc, xref):
                # eigenständiges Aktionsobjekt (per Verweis eingebunden)
                doc.update_object(xref, "<<>>")
                continue
            # Dokument-JavaScript (Namensbaum), Skripte, Zusatzaktionen und
            # eingebettete Aktionen eines Links, Lesezeichens oder beim Öffnen
            drop = {"JS", "JavaScript", "AA"}
            for key in ("A", "OpenAction", "Next"):
                kind, value = doc.xref_get_key(xref, key)
                if (kind == "dict" and _is_active_action(doc, xref, key + "/")) or (
                        kind == "xref" and _is_active_action(doc, int(value.split()[0]))):
                    drop.add(key)
            _drop_keys(doc, xref, drop)
        except Exception:
            continue


def save(doc: fitz.Document, path) -> None:
    doc.save(path, garbage=4, deflate=True, clean=True, encryption=fitz.PDF_ENCRYPT_NONE)


# ---------------------------------------------------------------------------
# Prüfung der gespeicherten Datei
# ---------------------------------------------------------------------------

# Nur diese Felder können personenbezogene Informationen enthalten (Titel/
# Autor könnte einen Namen tragen). producer/format sind technisch.
_PRIVACY_RELEVANT_METADATA_FIELDS = {"title", "author", "subject", "keywords", "creator"}
_ACTIVE_CONTENT = re.compile(rb"/(JavaScript|JS|EmbeddedFile|Launch|RichMedia)\b")


def metadata_is_clean(doc: fitz.Document) -> bool:
    return not any((doc.metadata or {}).get(f) for f in _PRIVACY_RELEVANT_METADATA_FIELDS)


def _open_all_layers(path) -> fitz.Document:
    """Öffnet die Datei so, dass auch ausgeblendete Ebenen extrahiert werden
    (nur Ein-/Ausschalten reicht dafür nicht, erst nach erneutem Öffnen)."""
    doc = fitz.open(path)
    ocgs = doc.get_ocgs()
    if ocgs and any(not info.get("on", True) for info in ocgs.values()):
        doc.set_layer(-1, on=list(ocgs), off=[])
        data = doc.tobytes()
        doc.close()
        doc = fitz.open(stream=data, filetype="pdf")
    return doc


def _page_samples(pix: fitz.Pixmap, rect: fitz.Rect, step: int = 1):
    """Zeilenweise Bytes eines Pixel-Rechtecks (rect in Pixeln)."""
    x0, y0 = max(0, int(rect.x0)), max(0, int(rect.y0))
    x1, y1 = min(pix.width, int(rect.x1)), min(pix.height, int(rect.y1))
    if x1 <= x0 or y1 <= y0:
        return
    samples, n, stride = pix.samples, pix.n, pix.stride
    for y in range(y0, y1, step):
        start = y * stride
        yield samples[start + x0 * n:start + x1 * n], n


def _rgb(pix: fitz.Pixmap) -> fitz.Pixmap:
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    if pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix


def _check_pixels(page: fitz.Page, rects: list[fitz.Rect], fill) -> list[int]:
    """Indizes der Bereiche, in denen nach dem Rendern nicht nur die
    Schwärzungsfarbe zu sehen ist."""
    zoom = 2.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=False)
    expected = [int(round(c * 255)) for c in fill]
    bad = []
    rot = page.rotation_matrix
    for i, r in enumerate(rects):
        shown = (r * rot).normalize()
        inner = fitz.Rect(shown.x0 + 1, shown.y0 + 1, shown.x1 - 1, shown.y1 - 1)
        if inner.is_empty or inner.width < 1 or inner.height < 1:
            continue
        px = fitz.Rect(inner.x0 * zoom, inner.y0 * zoom, inner.x1 * zoom, inner.y1 * zoom)
        step = max(1, int(px.height // 400))
        for row, n in _page_samples(pix, px, step):
            for c in range(min(n, 3)):
                channel = row[c::n]
                if channel and (max(channel) > expected[c] + 40 or min(channel) < expected[c] - 40):
                    bad.append(i)
                    break
            else:
                continue
            break
    return bad


def _check_images(doc: fitz.Document, page: fitz.Page, rects: list[fitz.Rect]) -> list[str]:
    """Bilddaten unter einem Schwärzungsbereich müssen einheitlich sein (MuPDF
    ersetzt die Pixel). Sonst läge das Bild nur unter einem schwarzen Kasten."""
    problems = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            placements = page.get_image_rects(xref, transform=True)
        except Exception:
            placements = []
        pix = None
        for bbox, matrix in placements:
            for r in rects:
                inter = fitz.Rect(bbox) & r
                if inter.is_empty or inter.width < 2 or inter.height < 2:
                    continue
                inner = fitz.Rect(inter.x0 + 0.75, inter.y0 + 0.75, inter.x1 - 0.75, inter.y1 - 0.75)
                try:
                    if pix is None:
                        pix = _rgb(fitz.Pixmap(doc, xref))
                    inv = ~fitz.Matrix(matrix)
                    pts = [fitz.Point(p) * inv for p in (inner.tl, inner.tr, inner.bl, inner.br)]
                    us = [min(max(p.x, 0.0), 1.0) * pix.width for p in pts]
                    vs = [min(max(p.y, 0.0), 1.0) * pix.height for p in pts]
                    area = fitz.Rect(min(us), min(vs), max(us), max(vs))
                    # Rand um ein Pixel verkleinern (Rundung)
                    area = fitz.Rect(area.x0 + 1, area.y0 + 1, area.x1 - 1, area.y1 - 1)
                    if area.is_empty:
                        continue
                    step = max(1, int(area.height // 300))
                    lo, hi = [255] * 3, [0] * 3
                    for row, n in _page_samples(pix, area, step):
                        for c in range(min(n, 3)):
                            channel = row[c::n]
                            if channel:
                                lo[c] = min(lo[c], min(channel))
                                hi[c] = max(hi[c], max(channel))
                    if any(hi[c] - lo[c] > 12 for c in range(3) if hi[c] >= lo[c]):
                        problems.append(f"Seite {page.number + 1}: Bilddaten unter der Schwärzung sind noch vorhanden")
                except Exception:
                    problems.append(f"Seite {page.number + 1}: Bild im Schwärzungsbereich konnte nicht geprüft werden")
    return problems


def _freehand_in(page: fitz.Page, rects: list[fitz.Rect], fill) -> bool:
    """True, wenn freihandartige Vektorgrafik (Kurven, viele kurze Linien -
    z. B. eine gezeichnete Unterschrift) in einen der Bereiche ragt.
    Beim Schwärzen: Grafik wird dann ganz entfernt. Bei der Prüfung: ihre
    Pfaddaten lägen sonst unter dem schwarzen Kasten noch in der Datei."""
    for drawing in page.get_drawings():
        area = fitz.Rect(drawing["rect"])
        is_fill = drawing.get("fill") and all(abs(a - b) < 0.05 for a, b in zip(drawing["fill"], fill))
        if is_fill and any(max(abs(a - b) for a, b in zip(area, r)) < 1 for r in rects):
            continue  # die Schwärzungsfläche selbst
        for r in rects:
            inner = fitz.Rect(r.x0 + 0.5, r.y0 + 0.5, r.x1 - 0.5, r.y1 - 0.5)
            if inner.is_empty or not area.intersects(inner):
                continue
            curves = short_lines = 0
            for item in drawing["items"]:
                if item[0] == "c":
                    box = fitz.Rect(item[1], item[1]) | item[2] | item[3] | item[4]
                    if box.intersects(inner) or inner.contains(item[1]):
                        curves += 1
                elif item[0] == "l":
                    p1, p2 = item[1], item[2]
                    seg = fitz.Rect(p1, p1) | p2
                    if abs(p1 - p2) < 15 and (seg.intersects(inner) or inner.contains(p1)):
                        short_lines += 1
            if curves or short_lines >= 5:
                return True
    return False


def verify(output_path, targets: list[dict], marked_texts: list[str],
           source_bytes: bytes | None = None, fill=BLACK) -> VerificationResult:
    """
    Prüft die gespeicherte Datei unabhängig vom Schwärzungsschritt (neu
    geöffnet). Blockierend: Restzeichen, sichtbare Inhalte, Bilddaten,
    Vektorgrafik, Annotationen, Links, Anhänge/JavaScript, Metadaten,
    Lesezeichen. Hinweise: weitere Vorkommen geschwärzter Angaben,
    mitgelöschter Nachbartext, Scan-Seiten.

    Das ist eine Verteidigung in der Tiefe, kein mathematischer Beweis.
    """
    problems: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def check(key, label, found: list[str], ok_text: str):
        problems.extend(found)
        checks.append({"key": key, "label": label, "ok": not found,
                       "detail": ok_text if not found else f"{len(found)} Befund(e)"})

    by_page: dict[int, list[fitz.Rect]] = {}
    for t in targets:
        by_page.setdefault(t["page"], []).append(fitz.Rect(t.get("rect") or t.get("bbox")))
    marked = sorted({_normalize_text(t) for t in marked_texts if len(_normalize_text(t)) >= 3})
    # Für Hinweise die Schreibweise aus dem Dokument zeigen, nicht die normalisierte
    display = {}
    for t in marked_texts:
        display.setdefault(_normalize_text(t), " ".join(t.split()))

    doc = _open_all_layers(output_path)
    try:
        residual, visible, image_problems, vector_pages = [], [], [], []
        for page_index, rects in sorted(by_page.items()):
            if page_index >= doc.page_count:
                residual.append(f"Seite {page_index + 1} fehlt im Ergebnis")
                continue
            page = doc[page_index]
            left = [ch for ch, box, _ in iter_chars(page)
                    if not ch.isspace() and any(_covers(r, box) for r in rects)]
            if left:
                sample = "".join(left[:12]) + ("…" if len(left) > 12 else "")
                residual.append(f"Seite {page_index + 1}: {len(left)} Zeichen Resttext im Schwärzungsbereich („{sample}“)")
            if _check_pixels(page, rects, fill):
                visible.append(f"Seite {page_index + 1}: Im Schwärzungsbereich ist noch Inhalt sichtbar")
            image_problems.extend(_check_images(doc, page, rects))
            if _freehand_in(page, rects, fill):
                vector_pages.append(
                    f"Seite {page_index + 1}: Vektorgrafik (z. B. gezeichnete Unterschrift) im "
                    "Schwärzungsbereich wurde nicht entfernt."
                )
        check("text", "Kein Text im Schwärzungsbereich", residual, "keine Restzeichen gefunden")
        check("pixels", "Schwärzungsbereich vollständig abgedeckt", visible, "nur Schwärzungsfarbe sichtbar")
        check("images", "Bilddaten unter der Schwärzung entfernt", image_problems, "keine Bildreste")
        check("vectors", "Vektorgrafik entfernt", vector_pages, "keine herausragende Freihandgrafik")

        annots = [f"Seite {i + 1}: enthält Kommentare/Annotationen" for i, p in enumerate(doc) if p.first_annot]
        annots += [f"Seite {i + 1}: enthält Formularfelder" for i, p in enumerate(doc) if p.first_widget]
        check("annotations", "Keine Kommentare oder Formularfelder", annots, "keine vorhanden")

        links = []
        for i, page in enumerate(doc):
            for link in page.get_links():
                area = fitz.Rect(link["from"])
                uri = _link_text(link)
                if any(area.intersects(r) for r in by_page.get(i, [])) or any(m in uri for m in marked):
                    links.append(f"Seite {i + 1}: Link im geschwärzten Bereich oder mit geschwärzter Angabe")
        check("links", "Keine Links auf geschwärzte Angaben", links, "keine gefunden")

        active = []
        if doc.embfile_count():
            active.append("PDF enthält eingebettete Dateien")
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref, compressed=True).encode("latin-1", "replace")
            except Exception:
                continue
            if _ACTIVE_CONTENT.search(obj):
                active.append("PDF enthält Anhänge oder aktive Inhalte (JavaScript)")
                break
        check("active", "Keine Anhänge oder aktiven Inhalte", active, "keine vorhanden")

        meta_clean = metadata_is_clean(doc)
        meta = [] if meta_clean else ["PDF enthält Metadaten (Titel, Autor …)"]
        if doc.xref_xml_metadata():
            meta.append("PDF enthält XML-Metadaten")
        check("metadata", "Metadaten entfernt", meta, "leer")

        toc_hits = [f"Lesezeichen „{entry[1][:40]}“ enthält eine geschwärzte Angabe"
                    for entry in doc.get_toc() if any(m in _normalize_text(entry[1]) for m in marked)]
        check("bookmarks", "Lesezeichen ohne geschwärzte Angaben", toc_hits, "unauffällig")

        # Hinweise -----------------------------------------------------
        page_texts = [_normalize_text(p.get_text()) for p in doc]
        for m in marked:
            hits = [i + 1 for i, text in enumerate(page_texts) if m in text]
            if hits:
                count = sum(text.count(m) for text in page_texts)
                pages = ", ".join(str(p) for p in hits[:10])
                warnings.append(
                    f"„{display.get(m, m)}“ kommt im Ergebnis noch {count}× ungeschwärzt vor (Seite {pages}). "
                    "Falls gewünscht, diese Stellen zusätzlich markieren."
                )

        if source_bytes is not None:
            with fitz.open(stream=source_bytes, filetype="pdf") as src:
                for page_index, rects in sorted(by_page.items()):
                    if page_index >= src.page_count or page_index >= doc.page_count:
                        continue
                    lost = _collateral(src[page_index], doc[page_index], rects)
                    if lost:
                        sample = "".join(lost[:30]) + ("…" if len(lost) > 30 else "")
                        warnings.append(
                            f"Seite {page_index + 1}: {len(lost)} angeschnittene(s) Zeichen außerhalb der "
                            f"Markierungen wurde(n) mit entfernt („{sample}“)."
                        )
                    src_page = src[page_index]
                    if not src_page.get_text().strip() and src_page.get_images():
                        warnings.append(
                            f"Seite {page_index + 1} hat keine Textebene (Scan). Geprüft wurden die "
                            "sichtbaren Pixel und die Bilddaten im Bereich."
                        )
    finally:
        doc.close()

    return VerificationResult(
        passed=not problems,
        residual_text_found=problems,
        metadata_clean=meta_clean,
        warnings=warnings,
        checks=checks,
    )


def _collateral(src_page: fitz.Page, out_page: fitz.Page, rects: list[fitz.Rect]) -> list[str]:
    """Zeichen, die außerhalb aller Bereiche lagen und trotzdem fehlen."""
    remaining: dict[tuple, int] = {}
    for ch, box, _ in iter_chars(out_page):
        key = (ch, round(box.x0), round(box.y0))
        remaining[key] = remaining.get(key, 0) + 1
    lost = []
    for ch, box, _ in iter_chars(src_page):
        if ch.isspace() or any(_center_in(r, box) for r in rects):
            continue
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                key = (ch, round(box.x0) + dx, round(box.y0) + dy)
                if remaining.get(key):
                    remaining[key] -= 1
                    break
            else:
                continue
            break
        else:
            lost.append(ch)
    return lost
