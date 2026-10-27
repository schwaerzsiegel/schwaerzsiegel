"""
Phase 16: synthetische lange Dokumente zur Messung von „Rauschen“ und Unterschriften.

Alle Namen, Firmen, Nummern und Adressen sind erfunden. Auszeichnung wie im Korpus (corpus.py):
[[kategorie|Text]] = erwartete Fundstelle, die geschwärzt werden soll; [[org|Text]] = Organisation, die
in der Regel NICHT geschwärzt werden soll (zählt als Rauschen, wenn sie vorgeschlagen oder vorausgewählt
wird).

    cd sidecar
    python eval/lange_dokumente.py --out eval/results/phase16-vorher-ai.json --mode ai

Gemessen wird wie in der App: Seite für Seite pdf_processor.extract_page + ner.detect (mit LLM-Cache je
Dokument). Der gescannte Brief wird als reines Bild-PDF erzeugt (Texterkennung nötig) und enthält eine
gezeichnete Unterschrift, einen Stempel und eine handschriftliche Notiz.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import fitz  # noqa: E402

MARKUP = re.compile(r"\[\[([a-z_-]+)\|(.+?)\]\]")

MAILVERLAUF = """
Von: [[person|Jonas Becker]] <[[email|j.becker@musterbau-nord.example.org]]>
Gesendet: Dienstag, 14. Oktober 2026 09:12
An: [[person|Sophie Wagner]] <[[email|s.wagner@stadtwerke-beispielstadt.example.org]]>
Cc: [[person|Mehmet Yilmaz]] <[[email|m.yilmaz@musterbau-nord.example.org]]>
Betreff: AW: Angebot Leitungsbau Lindenallee – Rückfragen
|
Sehr geehrte Frau [[person|Wagner]],
|
vielen Dank für Ihre Rückmeldung. Wir können den Termin am 21.10.2026 um 10 Uhr bestätigen. Herr
[[person|Yilmaz]] bringt die überarbeiteten Pläne mit. Die Abstimmung mit der [[org|Deutschen Telekom]] und
der [[org|Vodafone GmbH]] wegen der Leerrohre läuft noch; die Genehmigung des [[org|Tiefbauamts]] liegt vor.
|
Wegen der Zufahrt für das Grundstück von Frau [[person|Erika Musterfrau]], [[adresse|Lindenallee 17]], sprechen wir
direkt mit ihr. Sie ist unter [[telefon|0176 23456789]] erreichbar.
|
Mit freundlichen Grüßen
[[person|Jonas Becker]]
Projektleitung Tiefbau
|
[[org|Musterbau Nord GmbH]] · Hafenstraße 4 · 24103 Kiel
Telefon 0431 555 120 · Zentrale 0431 555 100
Geschäftsführer: [[person|Paul Hoffmann]] · [[org|Amtsgericht Kiel]] HRB 12345 · USt-IdNr. DE123456789
Mitglied im [[org|Bauindustrieverband Nord e. V.]]
|
-----Ursprüngliche Nachricht-----
Von: [[person|Sophie Wagner]] <[[email|s.wagner@stadtwerke-beispielstadt.example.org]]>
Gesendet: Montag, 13. Oktober 2026 16:40
An: [[person|Jonas Becker]] <[[email|j.becker@musterbau-nord.example.org]]>
Betreff: AW: Angebot Leitungsbau Lindenallee – Rückfragen
|
Sehr geehrter Herr [[person|Becker]],
|
im Namen der [[org|Stadtwerke Beispielstadt GmbH]] bedanke ich mich für das Angebot. Unsere Kollegin
[[person|Hannah Schulz]] aus dem [[org|Netzbetrieb]] hat noch zwei Fragen zur Bauzeit. Außerdem bittet der
[[org|Zweckverband Wasser Ostholstein]] um eine Kopie des Bauzeitenplans. Die [[org|Landesbehörde für Straßenbau]]
hat keine Einwände.
|
Können Sie am 21.10. vor Ort sein? Bitte bringen Sie die Unterlagen der [[org|TÜV Nord AG]] mit.
|
Viele Grüße
[[person|Sophie Wagner]]
Abteilung Netzplanung
[[org|Stadtwerke Beispielstadt GmbH]] · Am Mühlbach 2 · 24937 Beispielstadt
Telefon 0461 777 30 · Durchwahl 0461 777 3412
Aufsichtsratsvorsitzender: Bürgermeister [[person|Felix Vogel]]
|
-----Ursprüngliche Nachricht-----
Von: [[person|Jonas Becker]] <[[email|j.becker@musterbau-nord.example.org]]>
Gesendet: Freitag, 10. Oktober 2026 11:05
An: [[person|Sophie Wagner]] <[[email|s.wagner@stadtwerke-beispielstadt.example.org]]>
Betreff: Angebot Leitungsbau Lindenallee
|
Sehr geehrte Frau [[person|Wagner]],
|
anbei unser Angebot über die Erneuerung der Wasserleitung in der Lindenallee. Als Nachunternehmer sind
die [[org|Elektro Brandt KG]] und die [[org|Krüger Kanaltechnik GmbH]] vorgesehen. Die Baustelle wird nach den
Vorgaben der [[org|BG BAU]] eingerichtet; die Fachkraft für Arbeitssicherheit stellt die [[org|Arbeitsschutz Nord GmbH]].
|
Für Rückfragen steht Ihnen auch mein Kollege [[person|Mehmet Yilmaz]] zur Verfügung, mobil [[telefon|0151 98765432]].
|
Mit freundlichen Grüßen
[[person|Jonas Becker]]
Projektleitung Tiefbau
|
[[org|Musterbau Nord GmbH]] · Hafenstraße 4 · 24103 Kiel
Telefon 0431 555 120 · Zentrale 0431 555 100
Geschäftsführer: [[person|Paul Hoffmann]] · [[org|Amtsgericht Kiel]] HRB 12345 · USt-IdNr. DE123456789
Mitglied im [[org|Bauindustrieverband Nord e. V.]]
"""

ARBEITSVERTRAG = """
ARBEITSVERTRAG
|
zwischen der [[org|Nordlicht Logistik GmbH]], Industrieweg 9, 24539 Neumünster, vertreten durch die
Geschäftsführerin [[person|Clara Dietrich]] (im Folgenden „Arbeitgeber“)
|
und
|
Herrn [[person|Lukas Seidel]], geboren am [[geburtsdatum|12.05.1990]] in [[geburtsort|Flensburg]], wohnhaft
[[adresse|Birkenring 8, 24768 Rendsburg]] (im Folgenden „Arbeitnehmer“).
|
§ 1 Beginn und Tätigkeit
Das Arbeitsverhältnis beginnt am 01.01.2027. Der Arbeitnehmer wird als Disponent im Standort Neumünster
der [[org|Nordlicht Logistik GmbH]] eingestellt. Die [[org|Nordlicht Logistik GmbH]] kann ihm andere zumutbare
Tätigkeiten übertragen.
|
§ 2 Probezeit
Die ersten sechs Monate gelten als Probezeit. Während der Probezeit kann das Arbeitsverhältnis mit einer
Frist von zwei Wochen gekündigt werden.
|
§ 3 Vergütung
Der Arbeitnehmer erhält ein monatliches Bruttogehalt von 3.450,00 Euro. Die Zahlung erfolgt bargeldlos auf das
Konto [[iban|DE89 3704 0044 0532 0130 00]] bei der [[org|Förde Sparkasse]]. Die [[org|Nordlicht Logistik GmbH]]
zahlt einen Zuschuss zur betrieblichen Altersversorgung bei der [[org|Pensionskasse Nord VVaG]].
|
§ 4 Arbeitszeit
Die regelmäßige Arbeitszeit beträgt 39 Stunden pro Woche. Es gilt die Betriebsvereinbarung mit dem
[[org|Betriebsrat]] der [[org|Nordlicht Logistik GmbH]] vom 01.03.2025.
|
§ 5 Urlaub
Der Arbeitnehmer hat Anspruch auf 30 Arbeitstage Urlaub im Kalenderjahr.
|
§ 6 Arbeitsverhinderung
Bei Arbeitsunfähigkeit legt der Arbeitnehmer spätestens am dritten Tag eine Bescheinigung vor. Die
Krankenkasse des Arbeitnehmers ist die [[org|AOK NordWest]], Versichertennummer [[kv_nummer|A123456780]].
|
§ 7 Angaben für die Lohnabrechnung
Steuer-Identifikationsnummer: [[steuer_id|86095742719]]
Sozialversicherungsnummer: [[sv_nummer|65 120590 S 001]]
Personalnummer: [[personalnummer|NL-20471]]
|
§ 8 Verschwiegenheit
Der Arbeitnehmer verpflichtet sich, über alle Betriebs- und Geschäftsgeheimnisse der
[[org|Nordlicht Logistik GmbH]] sowie ihrer Kunden, insbesondere der [[org|Hansa Retail AG]] und der
[[org|Baltic Foods GmbH]], Stillschweigen zu bewahren.
|
§ 9 Nebentätigkeit
Nebentätigkeiten bedürfen der vorherigen Zustimmung des Arbeitgebers.
|
§ 10 Kündigung
Nach der Probezeit gelten die gesetzlichen Kündigungsfristen. Die Kündigung bedarf der Schriftform.
|
§ 11 Ausschlussfristen
Ansprüche aus dem Arbeitsverhältnis verfallen, wenn sie nicht innerhalb von drei Monaten nach Fälligkeit
schriftlich geltend gemacht werden. Dies gilt nicht für Ansprüche nach dem Mindestlohngesetz.
|
§ 12 Schlussbestimmungen
Änderungen bedürfen der Schriftform. Gerichtsstand ist das [[org|Arbeitsgericht Neumünster]].
|
Neumünster, 15.11.2026
|
[[person|Clara Dietrich]], Geschäftsführerin                [[person|Lukas Seidel]]
"""

BRIEF_SCAN = """
[[person|Olga Nowak]]
[[adresse|Rosenweg 3]]
[[adresse|24211 Preetz]]
|
[[org|Amt Preetz-Land]]
Bürgerservice
Am Markt 1
24211 Preetz
|
Preetz, 02.10.2026
|
Antrag auf Akteneinsicht, Ihr Zeichen 31-2026/118
|
Sehr geehrte Damen und Herren,
|
hiermit beantrage ich Einsicht in die Unterlagen zum Bauvorhaben auf dem Nachbargrundstück. Bitte
senden Sie mir die Unterlagen an die oben genannte Anschrift oder per E-Mail an [[email|olga.nowak@example.org]].
|
Mit freundlichen Grüßen
"""


def parse(text: str):
    """Zeilen ohne Auszeichnung und Liste (zeile, kategorie, text)."""
    lines, gold = [], []
    for raw in text.strip("\n").split("\n"):
        raw = "" if raw.strip() == "|" else raw
        clean, pos, last = "", 0, 0
        for m in MARKUP.finditer(raw):
            clean += raw[last:m.start()]
            gold.append((len(lines), m.group(1), m.group(2)))
            clean += m.group(2)
            last = m.end()
        clean += raw[last:]
        lines.append(clean)
    return lines, gold


def text_pdf(lines: list[str]) -> tuple[bytes, list[int]]:
    """Mehrseitiges Text-PDF; liefert die Seitennummer jeder Zeile."""
    out = fitz.open()
    page, y, page_of = out.new_page(), 60, []
    for line in lines:
        if y > 790:
            page, y = out.new_page(), 60
        page.insert_text((50, y), line, fontsize=10, fontname="helv")
        page_of.append(out.page_count - 1)
        y += 14
    data = out.tobytes()
    out.close()
    return data, page_of


def _signature_strokes(page, x: float, y: float, color=(0.08, 0.12, 0.45)):
    """Erfundene Unterschrift als Schwung von Bézier-Kurven (wie mit Kugelschreiber)."""
    shape = page.new_shape()
    pts = [(x, y), (x + 18, y - 22), (x + 30, y + 8), (x + 44, y - 18), (x + 58, y + 6), (x + 74, y - 26),
           (x + 88, y + 4), (x + 104, y - 12), (x + 128, y + 2), (x + 150, y - 8)]
    for a, b in zip(pts, pts[1:]):
        c1 = (a[0] + (b[0] - a[0]) / 3, a[1] - 10)
        c2 = (a[0] + 2 * (b[0] - a[0]) / 3, b[1] + 10)
        shape.draw_bezier(a, c1, c2, b)
    shape.draw_bezier((x + 10, y + 12), (x + 60, y + 18), (x + 110, y + 6), (x + 160, y + 14))  # Unterstrich
    shape.finish(color=color, width=1.6, closePath=False)
    shape.commit()
    return fitz.Rect(x - 4, y - 32, x + 166, y + 22)


def _stamp(page, center: tuple[float, float]):
    shape = page.new_shape()
    shape.draw_circle(center, 34)
    shape.draw_circle(center, 28)
    shape.finish(color=(0.6, 0.1, 0.1), width=1.4)
    shape.commit()
    page.insert_text((center[0] - 22, center[1] + 3), "EINGANG", fontsize=8, color=(0.6, 0.1, 0.1))
    return fitz.Rect(center[0] - 36, center[1] - 36, center[0] + 36, center[1] + 36)


def _note(page, x: float, y: float):
    """Handschriftliche Randnotiz: kurze, krakelige Striche statt Buchstaben."""
    shape = page.new_shape()
    for row in range(2):
        yy = y + row * 16
        xx = x
        for i in range(6):
            shape.draw_bezier((xx, yy), (xx + 4, yy - 9), (xx + 8, yy + 7), (xx + 13, yy - 2))
            xx += 15
    shape.finish(color=(0.1, 0.1, 0.4), width=1.2, closePath=False)
    shape.commit()
    return fitz.Rect(x - 4, y - 12, x + 96, y + 26)


def scan_pdf(lines: list[str], dpi: int = 200) -> tuple[bytes, dict[str, list[float]]]:
    """Brief mit Unterschrift, Stempel und Notiz, dann als reines Bild-PDF (wie gescannt)."""
    src = fitz.open()
    page = src.new_page()
    y = 70
    for line in lines:
        page.insert_text((60, y), line, fontsize=11, fontname="helv")
        y += 16
    areas = {
        "unterschrift": list(_signature_strokes(page, 64, y + 26)),
        "stempel": list(_stamp(page, (470, 120))),
        "notiz": list(_note(page, 380, y + 120)),  # freie Stelle unter dem Brief
    }
    page.insert_text((60, y + 62), "Olga Nowak", fontsize=11, fontname="helv")  # Name unter der Unterschrift
    pix = page.get_pixmap(dpi=dpi)
    out = fitz.open()
    scan = out.new_page(width=page.rect.width, height=page.rect.height)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    data = out.tobytes()
    out.close()
    src.close()
    return data, areas


DOKUMENTE = {"mailverlauf": MAILVERLAUF, "arbeitsvertrag": ARBEITSVERTRAG, "brief_scan": BRIEF_SCAN}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def _overlap(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def messen(mode: str) -> dict:
    from core import marks, ner
    from core import pdf_processor as pdf

    use_gliner, use_llm = mode in ("fast", "ai"), mode == "ai"
    ergebnis = {"mode": mode, "dokumente": {}}
    for name, quelltext in DOKUMENTE.items():
        lines, gold = parse(quelltext)
        areas = {}
        if name == "brief_scan":
            data, areas = scan_pdf(lines)
            page_of = [0] * len(lines)
        else:
            data, page_of = text_pdf(lines)
        start = time.perf_counter()
        suggestions = []
        cache: dict = {}
        with fitz.open(stream=data, filetype="pdf") as d:
            for i, p in enumerate(d):
                page = pdf.extract_page(p, i, detect_images=True)
                page.suggestions.extend(ner.detect(page.words, llm_cache=cache, use_llm=use_llm, use_gliner=use_gliner))
                marks.init_auto(page)
                for s in page.suggestions:
                    suggestions.append({"page": i, "label": s["label"], "text": s.get("text", ""),
                                        "bbox": s.get("bbox"), "selected": bool(s.get("default_selected")),
                                        "confidence": round(s.get("confidence", 0), 2), "source": s.get("source")})
        sekunden = round(time.perf_counter() - start, 1)

        def treffer(seite: int, text: str):
            t = _norm(text)
            return [s for s in suggestions if s["page"] == seite and s["text"] and
                    (t in _norm(s["text"]) or _norm(s["text"]) in t)]

        relevant = [(page_of[z], k, t) for z, k, t in gold if k != "org"]
        orgs = [(page_of[z], k, t) for z, k, t in gold if k == "org"]
        gefunden = [x for x in relevant if treffer(x[0], x[2])]
        ausgewaehlt = [x for x in relevant if any(s["selected"] for s in treffer(x[0], x[2]))]
        org_vorschlaege = [s for s in suggestions if s["label"] == "Organisation"]
        org_texte = {_norm(t) for _, _, t in orgs}
        org_ausgewaehlt = [s for s in suggestions if s["selected"] and
                           (s["label"] == "Organisation" or _norm(s["text"]) in org_texte)]
        flaechen = {k: any(s["bbox"] and _overlap(s["bbox"], b) for s in suggestions if not s["text"] or
                           s["label"].startswith("Unterschrift")) for k, b in areas.items()}
        ergebnis["dokumente"][name] = {
            "seiten": max(page_of) + 1,
            "sekunden": sekunden,
            "vorschlaege": len(suggestions),
            "vorausgewaehlt": sum(s["selected"] for s in suggestions),
            "je_kategorie": dict(Counter(s["label"] for s in suggestions).most_common()),
            "erwartet": len(relevant),
            "gefunden": len(gefunden),
            "vorausgewaehlt_erwartet": len(ausgewaehlt),
            "uebersehen": [t for _, _, t in relevant if (_, _, t) not in gefunden][:0] or
                          [x[2] for x in relevant if x not in gefunden],
            "nicht_vorausgewaehlt": [x[2] for x in relevant if x in gefunden and x not in ausgewaehlt],
            "organisationen_im_text": len(orgs),
            "organisation_vorschlaege": len(org_vorschlaege),
            "organisationen_vorausgewaehlt": sorted({s["text"] + " (" + s["label"] + ")" for s in org_ausgewaehlt}),
            "flaechen_gefunden": flaechen,
        }
    return ergebnis


def bericht(e: dict) -> str:
    zeilen = [f"Modus {e['mode']}"]
    for name, d in e["dokumente"].items():
        zeilen.append(f"\n== {name}: {d['seiten']} Seite(n), {d['sekunden']} s")
        zeilen.append(f"Vorschläge {d['vorschlaege']}, davon vorausgewählt {d['vorausgewaehlt']}")
        zeilen.append(f"Erwartete Angaben {d['erwartet']}: gefunden {d['gefunden']}, vorausgewählt {d['vorausgewaehlt_erwartet']}")
        zeilen.append(f"Organisationen im Text {d['organisationen_im_text']}, als Organisation vorgeschlagen "
                      f"{d['organisation_vorschlaege']}, vorausgewählt: {len(d['organisationen_vorausgewaehlt'])}")
        for o in d["organisationen_vorausgewaehlt"]:
            zeilen.append(f"   - {o}")
        zeilen.append("Je Kategorie: " + ", ".join(f"{k} {v}" for k, v in d["je_kategorie"].items()))
        if d["uebersehen"]:
            zeilen.append("Übersehen: " + "; ".join(d["uebersehen"]))
        if d["nicht_vorausgewaehlt"]:
            zeilen.append("Gefunden, aber nicht vorausgewählt: " + "; ".join(d["nicht_vorausgewaehlt"]))
        if d["flaechen_gefunden"]:
            zeilen.append("Handschrift/Stempel erkannt: " + ", ".join(f"{k} {'ja' if v else 'nein'}"
                                                                         for k, v in d["flaechen_gefunden"].items()))
    return "\n".join(zeilen)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "fast", "ai"], default="ai")
    ap.add_argument("--out")
    ap.add_argument("--pdf-ordner", help="erzeugte Test-PDFs zusätzlich hier ablegen (zum Ansehen)")
    args = ap.parse_args()
    if args.pdf_ordner:
        ziel = Path(args.pdf_ordner); ziel.mkdir(parents=True, exist_ok=True)
        for name, quelltext in DOKUMENTE.items():
            lines, _ = parse(quelltext)
            data = scan_pdf(lines)[0] if name == "brief_scan" else text_pdf(lines)[0]
            (ziel / f"phase16-{name}.pdf").write_bytes(data)
    e = messen(args.mode)
    print(bericht(e))
    if args.out:
        Path(args.out).write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
