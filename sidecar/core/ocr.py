"""
OCR-Fallback für Scan-PDFs ohne extrahierbare Textebene.

Rendert die Seite als Bild und extrahiert Wörter mit Bounding-Boxes über
Tesseract - die Koordinaten werden dabei zurück in PDF-Punkte skaliert,
damit sie mit den nativ extrahierten Wörtern (pdf_processor.py) im
selben Koordinatensystem liegen und Redaction-Boxen exakt passen.
"""

import io
import os
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from core.paths import MODELS_DIR, VENDOR_DIR

LANG = "deu"


def _configure_tesseract_path() -> None:
    """
    Sucht zuerst nach der mitgelieferten Tesseract-Kopie unter
    vendor/tesseract/ (seit v32 legt build-sidecar.cmd sie dort neben die
    .exe, damit Nutzer nichts selbst installieren müssen), dann am
    Standard-Windows-Installationsort (viele Installer fügen Tesseract
    nicht automatisch zum PATH hinzu, das ist der häufigste Stolperstein),
    und fällt sonst auf die System-PATH-Suche zurück.

    Tesseract steht unter Apache-2.0 und darf mitausgeliefert werden.
    TODO für macOS/Linux: Binary-Name ohne ".exe" ergänzen, sobald andere
    Plattformen gebaut werden - aktuell nur Windows-Pfade.
    """
    bundled = VENDOR_DIR / "tesseract" / "tesseract.exe"
    if bundled.exists():
        pytesseract.pytesseract.tesseract_cmd = str(bundled)
        # Die mitgelieferte Kopie soll ihre eigenen Sprachdateien nehmen,
        # nicht die einer evtl. zusätzlich installierten Tesseract-Version.
        os.environ["TESSDATA_PREFIX"] = str(bundled.parent / "tessdata")
        return
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            _use_project_tessdata(candidate.parent / "tessdata")
            return


def _use_project_tessdata(system_tessdata: Path) -> None:
    """
    Phase 5: Der Standard-Installer von Tesseract bringt nur Englisch mit.
    Fehlt dort das deutsche Paket, wird models/tessdata/deu.traineddata
    genutzt (tessdata_fast, Apache-2.0) - so ist keine weitere Installation
    mit Administratorrechten nötig.
    """
    project = MODELS_DIR / "tessdata"
    if not (system_tessdata / f"{LANG}.traineddata").is_file() and (project / f"{LANG}.traineddata").is_file():
        os.environ["TESSDATA_PREFIX"] = str(project)


def language_available() -> bool:
    """Liegt das deutsche Sprachpaket dort, wo Tesseract es sucht?"""
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        return (Path(prefix) / f"{LANG}.traineddata").is_file()
    cmd = Path(pytesseract.pytesseract.tesseract_cmd)
    return (cmd.parent / "tessdata" / f"{LANG}.traineddata").is_file()


_configure_tesseract_path()


def extract_words(pdf_page: "fitz.Page", page_index: int, zoom: float = 2.0) -> list[dict]:
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = Image.open(io.BytesIO(pix.tobytes("png")))

    try:
        data = pytesseract.image_to_data(image, lang=LANG,output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as e:
        raise RuntimeError(
            "Tesseract-OCR-Engine nicht gefunden. Das Python-Paket 'pytesseract' ist nur "
            "ein Wrapper - die eigentliche Engine ist ein separates Programm, das zusätzlich "
            "installiert werden muss. Windows: https://github.com/UB-Mannheim/tesseract/wiki "
            "- danach ggf. in ocr.py 'pytesseract.pytesseract.tesseract_cmd' auf den "
            "Installationspfad setzen, falls Tesseract nicht automatisch im PATH gefunden wird."
        ) from e
    except pytesseract.TesseractError as e:
        raise RuntimeError(
            f"Tesseract-Fehler bei der OCR-Verarbeitung (möglicherweise fehlt das deutsche "
            f"Sprachpaket 'deu'): {e}"
        ) from e

    words = []
    idx = 0
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        # Pixmap ist um `zoom` vergrößert gerendert -> zurückrechnen auf
        # PDF-Punkte, damit native und OCR-Koordinaten kompatibel sind.
        shown = fitz.Rect(
            data["left"][i] / zoom,
            data["top"][i] / zoom,
            (data["left"][i] + data["width"][i]) / zoom,
            (data["top"][i] + data["height"][i]) / zoom,
        )
        # Die Pixmap zeigt die Seite gedreht; Wörter werden wie der native
        # Text in ungedrehten PDF-Koordinaten gespeichert (Phase 1).
        bbox = (shown * pdf_page.derotation_matrix).normalize()
        words.append({
            "id": f"p{page_index}_w{idx}",
            "text": text,
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            # Zeile für mehrzeilige Markierungen (eine Box pro Zeile)
            "line": f"{data['block_num'][i]}-{data['par_num'][i]}-{data['line_num'][i]}",
            "source": "ocr",
            # Phase 16: Sicherheit der Texterkennung (0-100); Zeichensalat mit niedriger Sicherheit
            # stammt oft von Handschrift und wird in core/handschrift.py nicht als Text gewertet.
            "conf": int(float(data["conf"][i])),
        })
        idx += 1
    return words
