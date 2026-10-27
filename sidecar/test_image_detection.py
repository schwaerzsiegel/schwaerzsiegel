"""
Isolierter Test für pdf_processor.py - insbesondere die neue
detect_embedded_images()-Funktion. Braucht nur PyMuPDF + Pillow,
NICHT GLiNER/llama-cpp-python (die laden erst beim Aufruf von
core/ner.py bzw. core/llm.py, die hier bewusst nicht verwendet werden).

Baut ein Test-PDF mit Text + einem eingebetteten Bild und prüft, ob
beides korrekt erkannt wird.
"""

import io
import sys

import fitz
from PIL import Image

sys.path.insert(0, ".")
from core import pdf_processor


def build_test_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Kontakt: max.mustermann@example.de")
    page.insert_text((50, 80), "IBAN: DE89370400440532013000")

    # Ein simples Bild einbetten (rotes Rechteck als Platzhalter für z.B. ein Passfoto)
    img = Image.new("RGB", (100, 60), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 120, 150, 180), stream=buf.getvalue())

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def main():
    print("Baue Test-PDF (Text + eingebettetes Bild)...")
    pdf_bytes = build_test_pdf()

    print("Lade PDF über pdf_processor.load()...")
    doc_id, pages = pdf_processor.load(pdf_bytes)
    page = pages[0]

    print(f"\n--- Ergebnis ---")
    print(f"Erkannte Wörter (native Extraktion): {len(page.words)}")
    for w in page.words:
        print(f"  {w['id']}: '{w['text']}' @ {w['bbox']}")

    image_suggestions = [s for s in page.suggestions if s["source"] == "image_heuristik"]
    print(f"\nErkannte Bild-Vorschläge: {len(image_suggestions)}")
    for s in image_suggestions:
        print(f"  {s['label']} @ {s['bbox']} (confidence={s['confidence']})")

    if len(image_suggestions) == 1:
        print("\n✅ Test bestanden: genau 1 eingebettetes Bild korrekt erkannt.")
    else:
        print(f"\n❌ Test fehlgeschlagen: erwartet 1 Bild, gefunden {len(image_suggestions)}.")


if __name__ == "__main__":
    main()
