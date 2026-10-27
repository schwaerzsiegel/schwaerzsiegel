"""
Struktur- und Prüfziffernvalidierung für Kennungen (Phase 2).

Eine bestandene Prüfziffer ist ein starkes Indiz, aber kein Beweis (bei
einer Ziffer Prüfsumme passt auch jede zehnte Zufallszahl) - deshalb
werden Formate ohne Kontext nur mit Prüfziffer und Strukturregeln
zusammen als Treffer gewertet.
"""

import re


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def compact(text: str) -> str:
    return re.sub(r"[\s\-/.]", "", text).upper()


def iban(candidate: str) -> bool:
    """ISO 13616 / ISO 7064 MOD 97-10. Deutsche IBAN: genau 22 Zeichen."""
    value = compact(candidate)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value):
        return False
    if value.startswith("DE") and len(value) != 22:
        return False
    rearranged = value[4:] + value[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def steuer_id(candidate: str) -> bool:
    """Steuerliche Identifikationsnummer (BZSt): 11 Ziffern, erste nicht 0,
    in den ersten 10 Ziffern genau eine Ziffer doppelt oder dreifach,
    Prüfziffer nach ISO 7064 MOD 11,10."""
    value = digits(candidate)
    if len(value) != 11 or value[0] == "0" or len(digits(candidate)) != len(re.sub(r"[\s]", "", candidate)):
        return False
    body = value[:10]
    counts = sorted((body.count(d) for d in set(body)), reverse=True)
    if counts[0] not in (2, 3) or (len(counts) > 1 and counts[1] > 1):
        return False
    product = 10
    for d in body:
        s = (int(d) + product) % 10 or 10
        product = (s * 2) % 11
    return (11 - product) % 10 == int(value[10])


def _digit_sum(n: int) -> int:
    return n // 10 + n % 10


def sv_nummer(candidate: str) -> bool:
    """Versicherungsnummer der Rentenversicherung: 2 Ziffern Bereich,
    6 Ziffern Geburtsdatum (TTMMJJ), Anfangsbuchstabe des Geburtsnamens,
    2 Ziffern Seriennummer, 1 Prüfziffer (Gewichte 2,1,2,5,7,1,2,1,2,1,2,1)."""
    value = compact(candidate)
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})(\d{2})([A-Z])(\d{2})(\d)", value)
    if not m:
        return False
    day, month = int(m.group(2)), int(m.group(3))
    # Tag kann bei Vergabeengpässen um 30 oder 60 erhöht sein
    if not 1 <= day <= 91 or not 1 <= month <= 12:
        return False
    number = m.group(1) + m.group(2) + m.group(3) + m.group(4) + f"{ord(m.group(5)) - 64:02d}" + m.group(6)
    weights = (2, 1, 2, 5, 7, 1, 2, 1, 2, 1, 2, 1)
    return sum(_digit_sum(int(d) * w) for d, w in zip(number, weights)) % 10 == int(m.group(7))


def kv_nummer(candidate: str) -> bool:
    """Krankenversichertennummer (eGK): Buchstabe + 8 Ziffern + Prüfziffer;
    Buchstabe als zweistellige Zahl, Gewichte abwechselnd 1 und 2."""
    value = compact(candidate)
    if not re.fullmatch(r"[A-Z]\d{9}", value):
        return False
    number = f"{ord(value[0]) - 64:02d}" + value[1:9]
    total = sum(_digit_sum(int(d) * (1 if i % 2 == 0 else 2)) for i, d in enumerate(number))
    return total % 10 == int(value[9])


_AUSWEIS_ALPHABET = set("CFGHJKLMNPRTVWXYZ0123456789")


def ausweis(candidate: str) -> bool:
    """Seriennummer Personalausweis/Reisepass (seit 2010): 9 Zeichen aus
    einem eingeschränkten Alphabet + Prüfziffer (Gewichte 7, 3, 1)."""
    value = compact(candidate)
    if not re.fullmatch(r"[A-Z0-9]{9}\d", value) or not set(value[:9]) <= _AUSWEIS_ALPHABET:
        return False
    char_value = lambda c: int(c) if c.isdigit() else ord(c) - 55  # noqa: E731
    total = sum(char_value(c) * (7, 3, 1)[i % 3] for i, c in enumerate(value[:9]))
    return total % 10 == int(value[9])


def phone(candidate: str) -> bool:
    """Plausibilität: 6-15 Ziffern, kein Datum."""
    if re.fullmatch(r"\s*\d{1,2}\.\d{1,2}\.\d{2,4}\s*", candidate):
        return False
    return 6 <= len(digits(candidate)) <= 15


def email(candidate: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", candidate.strip()))
