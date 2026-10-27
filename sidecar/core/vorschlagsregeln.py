"""
Phase 17: Zentrale Vorschlagsregeln – welche erkannten Kategorien als Schwärzungsvorschlag erscheinen.

Die Erkennung (Regeln, GLiNER, Sprachmodell) bleibt unverändert und findet weiter alles, was sie kennt.
Hier wird in ner.detect entschieden, was davon dem Menschen vorgeschlagen wird:

  VORSCHLAGEN            immer (Vorauswahl wie bisher über rules.DEFAULT_OFF_LABELS)
  NUR_MIT_PERSONENBEZUG  nur, wenn ein Bezug zu einer natürlichen Person erkennbar ist (siehe unten)
  NICHT_VORSCHLAGEN      nie

Spätere Änderungen erfolgen nur in VORSCHLAG. Eine Einstellung je Organisation (z. B. über den
Branding-Ordner) kann diese Tabelle später überschreiben.

Sicherheitsregel: Ein Treffer wird nur weggelassen, wenn KEINE der für dieselben Wörter gefundenen
Kategorien vorgeschlagen werden soll. Erkennt GLiNER dieselben Wörter als „Person“ und „Organisation“,
bleibt der Treffer – als „Person“.

Zwei Schritte (ner.detect):
  vor_der_ki   allgemeine Daten früh entfernen (spart Fragen an das Sprachmodell und vermeidet Fehlurteile)
  nach_der_ki  Kategorien ohne Personenbezug entfernen, nachdem das Sprachmodell geurteilt hat
"""

from __future__ import annotations

import re

VORSCHLAGEN, NUR_MIT_PERSONENBEZUG, NICHT_VORSCHLAGEN = "vorschlagen", "nur mit Personenbezug", "nicht vorschlagen"

# Standard nach dem Auftrag vom 27.10.2026 (Abschnitt 6). Nicht genannte Kategorien: VORSCHLAGEN
# (Anschriften, Telefon, E-Mail usw. wie bisher; Betrag, Firmenanschrift, Service-Rufnummer bleiben
# vorgeschlagen, aber nicht vorausgewählt – Entscheidung E4).
VORSCHLAG = {
    "Person": VORSCHLAGEN,
    "Geburtsdatum": VORSCHLAGEN,
    "Sterbedatum": VORSCHLAGEN,
    "Organisationszeichen": VORSCHLAGEN,
    "Datum": NUR_MIT_PERSONENBEZUG,          # Person im selben Satz: „Personenbezug möglich“ (E1)
    "Organisation": NUR_MIT_PERSONENBEZUG,   # Einzelunternehmen, Titel + voller Name, KI-Urteil (E2)
}


def regel(label: str) -> str:
    return VORSCHLAG.get(label, VORSCHLAGEN)


# --- Satzgrenzen -------------------------------------------------------------------------------------
# Ein Punkt beendet einen Satz nur vor Leerzeichen + Großbuchstabe und nicht nach einer Ziffer
# („04.11.1971, …“) oder einer Abkürzung aus einem Buchstaben („Dr. Erika“ hat zwei, „e. K.“ einen).
_SATZENDE = re.compile(r"\n|[!?]|(?<!\d)(?<!\b[A-Za-zÄÖÜäöü])(?<!\bDr)(?<!\bProf)(?<!\bHr)(?<!\bFr)\.(?= [A-ZÄÖÜ])")


def satz(text: str, start: int, end: int) -> tuple[int, int]:
    """Zeichenbereich des Satzes (höchstens die Zeile), in dem start..end liegt."""
    anfang = 0
    for m in _SATZENDE.finditer(text, 0, start):
        anfang = m.end()
    m = _SATZENDE.search(text, end)
    return anfang, m.start() if m else len(text)


# --- Hinweise ----------------------------------------------------------------------------------------
# Nur mit diesem Hinweis im selben Satz darf GLiNER ein Datum „Geburtsdatum“ nennen.
GEBURT_HINWEIS = re.compile(r"(?i)(?<![\w])geb\.|(?<![\w])geb(?![\w])|geboren|geburt")

_UPPER, _LOWER = "A-ZÄÖÜ", "a-zäöüß"
_NAME_WORD = rf"[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)?"
# Einzelunternehmen und Praxen: Kennzeichen des Handelsrechts bzw. „Inhaber“, oder Titel vor einem
# vollständigen Namen. Ein Nachname allein („Müller GmbH“) genügt bewusst nicht.
_EINZELUNTERNEHMEN = re.compile(r"(?<![\w])(?:e\. ?K\.|e\. ?Kfm\.|e\. ?Kfr\.|Inh\.|Inhaber(?:in)?)(?![\w])")
_TITEL_VOLLER_NAME = re.compile(rf"(?<![\w])(?:Dr\.|Prof\.)(?: (?:Dr\.|med\.|jur\.))* {_NAME_WORD} {_NAME_WORD}")


def organisation_mit_personenbezug(text: str) -> bool:
    """Organisationsname, der eine natürliche Person erkennen lässt (ohne KI-Urteil)."""
    return bool(_EINZELUNTERNEHMEN.search(text) or _TITEL_VOLLER_NAME.search(text))


def _labels(s: dict) -> list[str]:
    return [s["label"]] + [a["label"] for a in s.get("also_detected_as", []) if not a.get("_abgestuft")]


def _andere_vorschlagbare(s: dict) -> list[dict]:
    """Weitere Erkennungen derselben Wörter, deren Kategorie vorgeschlagen werden soll."""
    return [a for a in s.get("also_detected_as", [])
            if not a.get("_abgestuft") and a["label"] != s["label"] and regel(a["label"]) == VORSCHLAGEN]


def vor_der_ki(text: str, suggestions: list[dict]) -> list[dict]:
    """GLiNER-„Geburtsdatum“ ohne Hinweis auf Geburt wird „Datum“; ein Datum ohne Person im selben Satz
    entfällt; mit Person bleibt es als „Personenbezug möglich“ (nicht vorausgewählt, die Gründliche KI
    prüft es)."""
    personen = [s["_span"] for s in suggestions if "Person" in _labels(s)]
    out = []
    for s in suggestions:
        a, b = s["_span"]
        satz_a, satz_b = satz(text, a, b)
        if (s["label"] == "Geburtsdatum" and s.get("source") == "gliner"
                and not GEBURT_HINWEIS.search(text[satz_a:satz_b])):
            s.setdefault("also_detected_as", []).append(
                {"label": "Geburtsdatum", "source": "gliner", "confidence": s["confidence"], "_abgestuft": True})
            s["label"], s["reason"], s["default_selected"] = "Datum", "KI-Modell, kein Hinweis auf Geburt", False
        if s["label"] == "Datum" and not _andere_vorschlagbare(s):
            if not any(pa < satz_b and pb > satz_a for pa, pb in personen):
                continue  # allgemeines Datum
            s["reason"] = "Datum im selben Satz wie eine Person – Personenbezug möglich, bitte prüfen"
            s["default_selected"] = False
        out.append(s)
    return out


def nach_der_ki(suggestions: list[dict]) -> list[dict]:
    """Kategorien „nur mit Personenbezug“ ohne Bezug und „nicht vorschlagen“ entfallen – außer eine andere
    Erkennung derselben Wörter soll vorgeschlagen werden; dann übernimmt sie die Kategorie."""
    out = []
    for s in suggestions:
        r = regel(s["label"])
        keep = r == VORSCHLAGEN
        if not keep:
            andere = _andere_vorschlagbare(s)
            if andere:
                best = max(andere, key=lambda x: x["confidence"])
                rest = [x for x in s["also_detected_as"] if x is not best]
                rest.append({"label": s["label"], "source": s.get("source", ""), "confidence": s["confidence"]})
                s["also_detected_as"] = rest
                s["label"], s["confidence"] = best["label"], best["confidence"]
                s["source"] = best.get("source") or s.get("source")
                s["default_selected"] = True
                s["reason"] = f"auch erkannt als {rest[-1]['label']}"
                keep = True
            elif s["label"] == "Datum" and r == NUR_MIT_PERSONENBEZUG:
                keep = True  # vor_der_ki hat allgemeine Daten schon entfernt
            elif s["label"] == "Organisation" and r == NUR_MIT_PERSONENBEZUG and (
                    s.get("default_selected") or organisation_mit_personenbezug(s.get("text", ""))):
                # Urteil des Sprachmodells (Privatperson) oder Einzelunternehmen/Praxis mit vollem Namen
                s["default_selected"] = True
                s["confidence"] = min(s["confidence"], 0.8)  # „zu prüfen“, nicht in der Sammelbestätigung
                if not s.get("llm_reviewed"):
                    s["reason"] = "Organisation mit Personenbezug (Einzelunternehmen oder voller Name) – bitte prüfen"
                keep = True
        if keep:
            for x in s.get("also_detected_as", []):
                x.pop("_abgestuft", None)
            out.append(s)
    return out
