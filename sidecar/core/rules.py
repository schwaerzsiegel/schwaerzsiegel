"""
Regelbasierte Erkennung mit Kontext (Phase 2).

Drei Arten von Regeln, mit fester Rangfolge bei Konflikten (siehe
ner.resolve_conflicts):

  PRIO_CONTEXT   (4) Beschriftung + Wert, z. B. "Personalnummer: 104827".
                     Der Kontext entscheidet über die Kategorie, nicht das
                     Zahlenformat.
  PRIO_STRUCTURE (3) Eindeutige Struktur, meist mit Prüfziffer: IBAN,
                     Steuer-ID, Sozialversicherungsnummer, E-Mail, Anschrift.
  PRIO_MODEL     (2) KI-Modell (GLiNER), siehe ner.py.
  PRIO_FORMAT    (1) Reines Format ohne Kontext (Telefonnummer, Datum,
                     Betrag) - wird verworfen, wenn die Zeile eine andere
                     Kennung beschreibt ("Bestellnummer 0815 4711 23").

Der Text kommt aus ner._build_text_and_index: Wörter mit einem Leerzeichen,
Zeilen mit "\\n" getrennt. Werte enden deshalb immer am Zeilenende.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from core import validators as v

PRIO_CONTEXT, PRIO_STRUCTURE, PRIO_MODEL, PRIO_FORMAT = 4, 3, 2, 1

# Kategorien, die standardmäßig sichtbar bleiben (Dokument bleibt verständlich)
DEFAULT_OFF_LABELS = {"Organisation", "Datum", "Betrag", "Firmenanschrift", "Service-Rufnummer"}

_UPPER = "A-ZÄÖÜ"
_LOWER = "a-zäöüß"
NAME_WORD = rf"[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)?"
NAME = rf"{NAME_WORD}(?: (?:von |van |de |zu |vom |von der )?{NAME_WORD}){{0,3}}"
MONTHS = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
DATE = rf"\d{{1,2}}\. ?\d{{1,2}}\. ?(?:19|20)?\d{{2}}(?!\d)|\d{{1,2}}\. ?(?:{MONTHS}) (?:19|20)\d{{2}}"
CITY = rf"[{_UPPER}][{_LOWER}]+(?:(?: | ?- ?)(?:am |an der |im |bei |ob der |in der )?[{_UPPER}][{_LOWER}]+)*"
STREET = (rf"(?:[{_UPPER}][\w\-]*?(?:straße|strasse|str\.|weg|gasse|allee|platz|ring|damm|ufer|chaussee|steig|pfad)"
          rf"|(?:Am|An der|An den|Im|In der|Auf dem|Auf der|Zum|Zur|Unter den) [{_UPPER}][{_LOWER}]+)"
          r" \d{1,4} ?[a-zA-Z]?(?![\w])")
PLZ_CITY = rf"(?<![\d\w])\d{{5}} {CITY}"
PHONE_VALUE = r"(?:\+|\(?0)[\d ()/.\-]{4,}\d"
UNLABELED_PHONE = (r"(?<![\w.+/\-])(?:\+49 ?(?:\(0\) ?)?\d{2,5}|\(0\d{2,5}\)|0\d{2,5})"
                   r"(?: ?[/\-] ?| )(?:\d{5,}|\d{3} \d{3,5})(?![\w.])")
EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
IBAN_VALUE = r"[A-Z]{2}\d{2}(?: ?[A-Z0-9]{1,4}){3,8}"
REST_OF_LINE = rf"[^\n]+?(?=(?: [{_UPPER}][\w.\-]*:)|\n|$)"

# Wörter, die Feldbezeichnungen sind und selbst nie ein Wert sein können
FIELD_WORDS = {
    "telefon", "tel", "telefonnummer", "rufnummer", "mobil", "mobilnummer", "handy", "fax", "telefax",
    "telefonzentrale", "e-mail", "email", "mail", "name", "vorname", "nachname", "familienname", "anschrift",
    "adresse", "wohnort", "wohnhaft", "straße", "strasse", "plz", "ort", "plz/ort", "iban", "bic", "konto",
    "kontonummer", "kontoinhaber", "kontoinhaberin", "bankverbindung", "kunde", "kundin", "patient", "patientin",
    "geburtsdatum", "geburtsort", "geboren", "geb", "personalnummer", "personalakte", "personal-nr",
    "pers.-nr", "mitarbeiter-id", "mitarbeiternummer", "ma-nr", "steuer-id", "steuerliche",
    "identifikationsnummer", "steuernummer", "versichertennummer", "rentenversicherungsnummer",
    "sozialversicherungsnummer", "krankenversichertennummer", "ausweisnummer", "personalausweisnummer",
    "reisepassnummer", "diagnose", "medikation", "befund", "aktenzeichen", "rechnungsnummer", "bestellnummer",
    "lieferadresse", "lebenslauf", "arztbrief", "vertraulich", "herr", "frau", "unterschrift",
}

_VETO_FOR_FORMAT = re.compile(
    r"(?i)(nummer|nr\.|\bnr\b|\bid\b|referenz|code|vorgang|aktenzeichen|\baz\b|bestell|rechnung|kunden|liefer"
    r"|iban|konto|steuer|versicherung|personal|ausweis|mandat|version|seite|raum|artikel|auftrag|vertrag"
    r"|kostenstelle|projekt|charge|los\b|serien)")
# Wortanfang prüfen: "tel" steckt auch in "Kostenstelle"
_PHONE_HINT = re.compile(r"(?i)\b(tel|telefon|fon|mobil|handy|fax|telefax|ruf|erreichbar|anruf|whatsapp)")
_ORGANISATION = re.compile(
    r"\b(GmbH|AG|KG|OHG|UG|e\. ?V\.|mbH|Amt|Behörde|Bürgeramt|Rathaus|Klinik|Klinikum|Praxis|Bank|Sparkasse"
    r"|Verwaltung|Ministerium|Gericht)\b")


def is_field_word(word: str) -> bool:
    """Feldbezeichnung: bekanntes Wort oder ein Wort mit Doppelpunkt am Ende
    ("Arzt:", "Versicherte(r):") - ein Wert endet nie mit Doppelpunkt."""
    return word.endswith(":") or word.strip(".:,;()[]·").casefold() in FIELD_WORDS


@dataclass
class ContextRule:
    label: str          # Kategorie der Fundstelle
    keywords: str       # Beschriftung (ohne Groß-/Kleinschreibung)
    value: str          # Wert (mit Groß-/Kleinschreibung)
    validator: Callable[[str], bool] | None = None
    confidence: float = 0.95
    # Wert ohne gültige Prüfziffer: trotzdem Treffer (Kontext ist eindeutig),
    # aber mit geringerer Sicherheit -> "zu prüfen"
    unvalidated_confidence: float | None = None
    colon: bool = False  # Doppelpunkt nach der Beschriftung zwingend
    filler: bool = True  # bis zu 4 Wörter zwischen Beschriftung und ":" erlaubt
    note: str = ""       # Zusatz zur Begründung (z.B. "Format unbekannt")


def _has_digits(n: int) -> Callable[[str], bool]:
    return lambda s: len(v.digits(s)) >= n


def _ausweis_like(s: str) -> bool:
    return any(c.isdigit() for c in s) and len(v.compact(s)) >= 8


CONTEXT_RULES = [
    ContextRule("Personalnummer",
                r"Personal-?(?:nummer|nr\.?|kennziffer|-?ID)|Pers\.? ?-? ?Nr\.?|P-?Nr\.|Mitarbeiter-?(?:ID|nummer|nr\.?|kennung)"
                r"|MA-?(?:Nr\.?|Nummer|ID)|Dienstnummer|Personalkennung",
                r"[A-Z]{0,3}-?\d[\dA-Z\-/]{2,14}(?: \d{2,6}(?![\d.,]))?", _has_digits(3)),
    # Bis zu drei kleingeschriebene Füllwörter: "Handynummer bleibt 0171 …",
    # "Tel. privat 030 …". Der Wert muss mit + oder 0 beginnen.
    ContextRule("Telefonnummer",
                r"(?:Tel(?:efon)?(?:nummer)?\.?|Rufnummer|Mobil(?:telefon|nummer)?|Handy(?:nummer)?|Telefax|Fax|Phone"
                r"|Fon|Durchwahl|(?:Rückfragen|Rückmeldung|Rückruf|erreichbar|anrufen)(?: [a-zäöüß]+){0,3} unter)"
                r"(?: [a-zäöüß]+){0,3}",
                PHONE_VALUE, v.phone, filler=False),
    ContextRule("E-Mail", r"E-?Mail(?:-?Adresse)?|Mail", EMAIL, v.email, 0.98, filler=False),
    ContextRule("Geburtsdatum", r"Geburtsdatum|Geb\.? ?-? ?Datum|geb\.|geboren(?: am)?|Geburtstag", DATE,
                filler=False),
    ContextRule("Geburtsort", r"Geburtsort|Geb\.? ?-? ?Ort|geboren in", CITY, confidence=0.9, filler=False),
    ContextRule("Steuer-ID",
                r"Steuer-?ID(?:-?Nr\.?)?|Steuer-?IdNr\.?|IdNr\.?|(?:Steuerliche )?Identifikationsnummer"
                r"|Steuer-?identifikationsnummer|TIN",
                r"\d{2} ?\d{3} ?\d{3} ?\d{3}(?!\d)", v.steuer_id, 0.98, unvalidated_confidence=0.8),
    ContextRule("Steuernummer", r"Steuernummer|St\.? ?-? ?Nr\.?|StNr\.?", r"\d{2,3}/\d{3,4}/\d{4,5}|\d{10,13}"),
    ContextRule("Sozialversicherungsnummer",
                r"Sozialversicherungs-?(?:nummer|nr\.?)|SV-?(?:Nummer|Nr\.?)|SVNR|Rentenversicherungs-?(?:nummer|nr\.?)"
                r"|RV-?(?:Nummer|Nr\.?)|RVNR",
                r"\d{2} ?\d{6} ?[A-Z] ?\d{3}", v.sv_nummer, 0.98, unvalidated_confidence=0.8),
    ContextRule("Krankenversichertennummer",
                r"Krankenversicherten-?(?:nummer|nr\.?)|Versicherten-?(?:nummer|nr\.?)|KVNR|Vers\.? ?-? ?Nr\.?"
                r"|Krankenversicherungs-?(?:nummer|nr\.?)",
                r"[A-Z] ?\d{9}(?!\d)", v.kv_nummer, 0.98, unvalidated_confidence=0.8),
    ContextRule("Ausweisnummer",
                r"(?:Personal)?ausweis-?(?:nummer|nr\.?)?|Reisepass-?(?:nummer|nr\.?)?|Pass-?(?:nummer|nr\.?)"
                r"|Dokumenten-?nummer",
                r"[A-Z0-9]{8,10}(?![\w])", _ausweis_like),
    # Phase 5: beschriftete IBAN auch ohne gültige Prüfziffer (Tippfehler,
    # Texterkennung, Testdaten) - dann nur "zu prüfen", nicht "sicher"
    ContextRule("IBAN", r"IBAN|Bankverbindung", IBAN_VALUE, v.iban, 0.98, unvalidated_confidence=0.8),
    ContextRule("Bankverbindung", r"Konto-?(?:nummer|nr\.?)|Kto\.? ?-? ?Nr\.?|Kontonr\.?",
                r"\d{5,12}(?!\d)", confidence=0.9),
    ContextRule("Person",
                r"Vor- und Zuname|Vor- und Nachname|Name|Vorname|Nachname|Familienname|Geburtsname|Kontoinhaber(?:in)?"
                r"|Patient(?:in)?|Kunde|Kundin|Versicherte[rn]?|Antragsteller(?:in)?|Mitarbeiter(?:in)?"
                r"|Arbeitnehmer(?:in)?|Ansprechpartner(?:in)?|Empfänger(?:in)?|Inhaber(?:in)?|Bewerber(?:in)?"
                r"|Von|An|Absender(?:in)?",
                NAME, colon=True, filler=False),
    ContextRule("Person", r"Herrn?|Frau|Hr\.|Fr\.", rf"(?:(?:Dr\.|Prof\.) )*{NAME}", confidence=0.9, filler=False),
    ContextRule("Person", r"geb\.|geborene[rn]?", NAME, confidence=0.85, filler=False),  # Geburtsname
    ContextRule("Adresse",
                r"Anschrift|Adresse|Wohnanschrift|Postanschrift|Wohnort|wohnhaft(?: in)?|Lieferadresse"
                r"|Rechnungsadresse|Straße|PLZ ?/ ?Ort",
                # Phase 5: mit PLZ + Ort endet die Anschrift dort - sonst lief
                # der Wert bis zum Zeilenende ("…, 12345 Ort. Bitte melden …")
                rf"[^\n]*?\d{{5}} {CITY}|{REST_OF_LINE}", confidence=0.9, filler=False),
    ContextRule("Gesundheitsdaten",
                r"Diagnosen?|Befunde?|Erkrankung(?:en)?|Krankheit(?:en)?|Vorerkrankung(?:en)?|Medikation|Medikamente?"
                r"|Therapie|Behinderung|GdB|Allergien?|Beschwerden|Anamnese|ICD(?:-10)?"
                r"|(?:arbeitsunfähig|erkrankt|behandelt)(?:keit)? (?:wegen|an|aufgrund)",
                REST_OF_LINE, confidence=0.85, filler=False),
]


# Phase 5: Kennnummern-Felder, deren Wert kein bekanntes Format hat
# ("Sozialversicherungsnr. TEST-SV-123456") - als "unsicher" vorschlagen.
_ID_FIELDS = {"IBAN", "Steuer-ID", "Sozialversicherungsnummer", "Krankenversichertennummer", "Ausweisnummer"}
_CHECKSUM_LABELS = _ID_FIELDS - {"Ausweisnummer"}
CONTEXT_RULES += [
    ContextRule(rule.label, rule.keywords, r"[A-Za-z0-9][A-Za-z0-9\-/.]{2,30}[A-Za-z0-9]", _has_digits(4), 0.6,
                note="Format unbekannt, bitte prüfen")
    for rule in CONTEXT_RULES if rule.label in _ID_FIELDS
]

# Phase 5: Zusatz in Klammern zwischen Beschriftung und Wert
# ("IBAN (Testwert) DE…", "Telefon (privat): 030 …")
_REMARK = r"(?: ?\([^()\n]{1,30}\))?"


def _compile(rule: ContextRule) -> list[re.Pattern]:
    # Beschriftung am Wortanfang und nicht Teil eines längeren Wortes
    # ("Konto" in "Kontoinhaber", "Tel" in "Telefonzentrale").
    label = rf"(?<![\w\-])(?P<label>(?i:{rule.keywords}))(?![{_LOWER}]){_REMARK}"
    sep = r" ?: ?" if rule.colon else r"(?: ?[:#] ?| )?"
    patterns = [re.compile(rf"(?m){label}{sep}(?P<value>{rule.value})")]
    if rule.filler:
        patterns.append(re.compile(rf"(?m){label}(?: [^\s:]+){{1,4}} ?: ?(?P<value>{rule.value})"))
    return patterns


_COMPILED = [(rule, _compile(rule)) for rule in CONTEXT_RULES]
# "geboren am 3. Mai 1979 in Kiel" - der Ort steht hinter dem Datum
_BIRTHPLACE_AFTER_DATE = re.compile(rf"(?i:geb(?:oren|\.)?)(?: am)?:? ?(?:{DATE}) in (?P<value>{CITY})")


@dataclass
class Hit:
    label: str
    start: int
    end: int
    confidence: float
    priority: int
    reason: str


def _line_of(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, len(text) if end < 0 else end


def context_hits(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for rule, patterns in _COMPILED:
        for pattern in patterns:
            for m in pattern.finditer(text):
                value = m.group("value").rstrip(" ,;.") if rule.label != "Ausweisnummer" else m.group("value")
                if rule.label == "IBAN" and not rule.note:
                    value = _trim_iban(value)
                if not value.strip():
                    continue
                confidence = rule.confidence
                valid = rule.validator(value) if rule.validator else None
                if valid is False:
                    if rule.unvalidated_confidence is None:
                        continue
                    confidence = rule.unvalidated_confidence
                start = m.start("value")
                note = rule.note
                if not note and rule.label in _CHECKSUM_LABELS and valid is not None:
                    note = "Prüfziffer gültig" if valid else "Prüfziffer ungültig, bitte prüfen"
                reason = f"Kontext „{m.group('label').strip()}“" + (f", {note}" if note else "")
                hits.append(Hit(rule.label, start, start + len(value), confidence, PRIO_CONTEXT, reason))
    for m in _BIRTHPLACE_AFTER_DATE.finditer(text):
        hits.append(Hit("Geburtsort", m.start("value"), m.end("value"), 0.9, PRIO_CONTEXT, "Kontext „geboren … in“"))
    return hits


def _trim_iban(value: str) -> str:
    """Gültigen Präfix nehmen, sonst angehängte Gruppen ohne Ziffer ("BIC") abschneiden."""
    parts = value.split(" ")
    for n in range(len(parts), 0, -1):
        if v.iban(" ".join(parts[:n])):
            return " ".join(parts[:n])
    while len(parts) > 1 and not any(c.isdigit() for c in parts[-1]):
        parts.pop()
    return " ".join(parts)


def _iban_hits(text: str) -> list[Hit]:
    """IBAN mit Leerzeichen-Gruppen: längsten gültigen Präfix nehmen, damit
    folgende Wörter ("BIC") nicht die Prüfung verderben."""
    hits = []
    for m in re.finditer(r"(?<![\w])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{1,4}){3,8}", text):
        candidate = m.group()
        parts = candidate.split(" ")
        for n in range(len(parts), 0, -1):
            chunk = " ".join(parts[:n])
            if v.iban(chunk):
                hits.append(Hit("IBAN", m.start(), m.start() + len(chunk), 0.98, PRIO_STRUCTURE, "Prüfziffer gültig"))
                break
    return hits


_STRUCTURE = [
    # (Kategorie, Muster, Prüfung, Sicherheit, Begründung)
    ("E-Mail", EMAIL, v.email, 0.97, "Format E-Mail-Adresse"),
    ("Steuer-ID", r"(?<![\d\w])\d{2} ?\d{3} ?\d{3} ?\d{3}(?![\d\w])", v.steuer_id, 0.9, "Prüfziffer gültig"),
    ("Sozialversicherungsnummer", r"(?<![\d\w])\d{2} ?\d{6} ?[A-Z] ?\d{3}(?![\d\w])", v.sv_nummer, 0.95,
     "Prüfziffer gültig"),
    # Ohne Kontext nur "zu prüfen": bei einer Prüfziffer passt jede zehnte Zufallskennung
    ("Krankenversichertennummer", r"(?<![\w])[A-Z]\d{9}(?![\w])", v.kv_nummer, 0.7, "Prüfziffer gültig"),
    ("Adresse", STREET, None, 0.85, "Format Straße + Hausnummer"),
    ("Adresse", PLZ_CITY, None, 0.85, "Format Postleitzahl + Ort"),
    ("Gesundheitsdaten (ICD-10)", r"(?<![\w])[A-TV-Z]\d{2}\.\d{1,2}(?![\w])", None, 0.75, "Format ICD-10-Code"),
]

_FORMAT = [
    ("Telefonnummer", UNLABELED_PHONE, v.phone, 0.7, "Format Telefonnummer, ohne Beschriftung"),
    ("Datum", DATE, None, 0.6, "Format Datum"),
    ("Betrag", r"(?:€ ?|EUR ?)?\d{1,3}(?:\.\d{3})*,\d{2} ?(?:€|EUR)?", None, 0.6, "Format Betrag"),
]


_UNLABELED_PHONE_FULL = re.compile(rf"\s*{UNLABELED_PHONE}\s*")
# Freecall-/Service-/Mehrwertnummern gehören nie einer Privatperson
_SERVICE_NUMBER = re.compile(r"\s*(?:\+49 ?(?:\(0\) ?)?|0)(?:800|180\d?|137|900|700)\b")
_ADDRESS_SHAPE = re.compile(rf"{STREET}|{PLZ_CITY}")


def is_service_number(value: str) -> bool:
    return bool(_SERVICE_NUMBER.match(value))


def plausible_address(value: str) -> bool:
    """KI-Treffer "Adresse": mit Ziffern nur, wenn sie wie eine Anschrift
    aussehen ("Raum 2.14" nicht); reine Ortsnamen sind erlaubt."""
    if not any(c.isdigit() for c in value):
        return any(c.isalpha() for c in value)
    return bool(_ADDRESS_SHAPE.search(value))


def plausible_phone(text: str, start: int, end: int) -> bool:
    """Darf eine Zahlenfolge ohne eindeutigen Kontext Telefonnummer heißen?
    Ja, wenn die Zeile davor auf ein Telefon hinweist; sonst nur bei klarer
    Telefon-Struktur und wenn die Zeile keine andere Kennung beschreibt."""
    line_start, _ = _line_of(text, start)
    prefix = text[line_start:start]
    if _PHONE_HINT.search(prefix):
        return True
    if _VETO_FOR_FORMAT.search(prefix):
        return False
    return bool(_UNLABELED_PHONE_FULL.fullmatch(text[start:end]))


def structure_and_format_hits(text: str) -> list[Hit]:
    hits = _iban_hits(text)
    for priority, rules in ((PRIO_STRUCTURE, _STRUCTURE), (PRIO_FORMAT, _FORMAT)):
        for label, pattern, check, confidence, reason in rules:
            for m in re.finditer(pattern, text):
                value = m.group()
                if check and not check(value):
                    continue
                line_start, line_end = _line_of(text, m.start())
                prefix = text[line_start:m.start()]
                hit = Hit(label, m.start(), m.end(), confidence, priority, reason)
                if label == "Telefonnummer" and _VETO_FOR_FORMAT.search(prefix) and not _PHONE_HINT.search(prefix):
                    continue  # die Zeile beschreibt eine andere Kennung
                if label == "Telefonnummer" and is_service_number(value):
                    hit.label, hit.confidence, hit.reason = "Service-Rufnummer", 0.6, "Service-/Freecall-Vorwahl"
                if label == "Adresse" and _ORGANISATION.search(text[line_start:line_end]):
                    hit.label, hit.confidence, hit.reason = "Firmenanschrift", 0.6, "Anschrift einer Organisation"
                hits.append(hit)
    return hits


def all_hits(text: str) -> list[Hit]:
    return context_hits(text) + structure_and_format_hits(text)
