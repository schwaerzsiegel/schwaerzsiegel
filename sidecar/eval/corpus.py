"""
Synthetischer Testkorpus für die Erkennungsqualität (Phase 2).

Alle Personen, Nummern und Adressen sind erfunden. Nummern mit Prüfziffer
(IBAN, Steuer-ID, Sozialversicherungs-, Krankenversicherten-,
Ausweisnummer) werden mit gültiger Prüfziffer erzeugt. Die Prüfziffern
werden hier bewusst unabhängig von core/validators.py berechnet - so
prüfen sich Generator und Erkennung gegenseitig.

Auszeichnung in den Vorlagen: [[kategorie|Text]] markiert eine erwartete
Fundstelle, [[-|Text]] einen Bereich, der weder als Treffer noch als
Fehlalarm zählt.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

CATEGORIES = [
    "person", "adresse", "telefon", "email", "geburtsdatum", "geburtsort", "personalnummer",
    "iban", "bankverbindung", "ausweis", "sv_nummer", "steuer_id", "kv_nummer", "gesundheit",
]

FIRST = ["Erika", "Max", "Lena", "Jonas", "Sophie", "Paul", "Mia", "Felix", "Hannah", "Lukas", "Aylin",
         "Mehmet", "Olga", "Piotr", "Chiara", "Leonie", "Ben", "Clara", "Noah", "Marie", "Elias", "Greta"]
LAST = ["Mustermann", "Beispiel", "Musterfrau", "Becker", "Hoffmann", "Schulz", "Wagner", "Krüger", "Brandt",
        "Yilmaz", "Nowak", "Kowalski", "Schäfer", "Vogel", "Lorenz", "Dietrich", "Haas", "Franke", "Seidel",
        "Busch", "Albrecht", "Petersen-Lohmann"]
CITIES = ["Beispielstadt", "Musterhausen", "Berlin", "Hamburg", "München", "Köln", "Leipzig", "Bremen",
          "Dresden", "Hannover", "Nürnberg", "Kiel", "Bad Musterberg", "Frankfurt am Main"]
STREETS = ["Musterweg", "Hauptstraße", "Lindenallee", "Gartenstraße", "Bahnhofstr.", "Schillerplatz",
           "Rosenweg", "Birkenring", "Am Mühlbach", "Kirchgasse", "Uferdamm"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]
DIAGNOSES = [("Diabetes mellitus Typ 2", "E11.9"), ("Depressive Episode", "F32.1"), ("Asthma bronchiale", "J45.9"),
             ("Essentielle Hypertonie", "I10"), ("Lumbago", "M54.5"), ("Migräne", "G43.9")]
MEDICATION = ["Metformin 500 mg", "Sertralin 50 mg", "Salbutamol-Spray", "Ramipril 5 mg", "Ibuprofen 400 mg"]


# ---------------------------------------------------------------------------
# Nummern mit gültiger Prüfziffer (unabhängige Implementierung)
# ---------------------------------------------------------------------------

def _digit_sum(n: int) -> int:
    return sum(int(c) for c in str(n))


def make_iban(rng: random.Random) -> str:
    bban = "".join(str(rng.randint(0, 9)) for _ in range(18))
    check = 98 - int(bban + "131400") % 97
    raw = f"DE{check:02d}{bban}"
    return " ".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def make_steuer_id(rng: random.Random) -> str:
    while True:
        digits = list(range(10))
        rng.shuffle(digits)
        body = digits[:9]
        body.insert(rng.randint(1, 9), rng.choice(body))
        if body[0] == 0:
            continue
        product = 10
        for d in body:
            s = (d + product) % 10 or 10
            product = (s * 2) % 11
        check = (11 - product) % 10
        raw = "".join(map(str, body)) + str(check)
        return rng.choice([raw, f"{raw[:2]} {raw[2:5]} {raw[5:8]} {raw[8:]}"])


def make_sv_nummer(rng: random.Random, birth: tuple[int, int, int], last: str) -> str:
    day, month, year = birth
    area = rng.choice(["12", "13", "15", "65", "53", "40"])
    letter = last[0].upper().replace("Ä", "A").replace("Ö", "O").replace("Ü", "U")
    serial = f"{rng.randint(0, 99):02d}"
    digits = area + f"{day:02d}{month:02d}{year % 100:02d}" + f"{ord(letter) - 64:02d}" + serial
    weights = [2, 1, 2, 5, 7, 1, 2, 1, 2, 1, 2, 1]
    check = sum(_digit_sum(int(d) * w) for d, w in zip(digits, weights)) % 10
    return f"{area} {day:02d}{month:02d}{year % 100:02d} {letter} {serial}{check}"


def make_kvnr(rng: random.Random) -> str:
    letter = chr(rng.randint(65, 90))
    body = "".join(str(rng.randint(0, 9)) for _ in range(8))
    digits = f"{ord(letter) - 64:02d}" + body
    check = sum(_digit_sum(int(d) * (1 if i % 2 == 0 else 2)) for i, d in enumerate(digits)) % 10
    return f"{letter}{body}{check}"


def make_ausweis(rng: random.Random) -> str:
    alphabet = "CFGHJKLMNPRTVWXYZ0123456789"
    serial = rng.choice("LMNPRTVWXY") + "".join(rng.choice(alphabet) for _ in range(8))
    value = lambda c: int(c) if c.isdigit() else ord(c) - 55  # noqa: E731
    check = sum(value(c) * (7, 3, 1)[i % 3] for i, c in enumerate(serial)) % 10
    return f"{serial}{check}"


def make_phone(rng: random.Random, mobile: bool = False) -> str:
    if mobile:
        prefix = rng.choice(["0151", "0160", "0171", "0176"])
        number = "".join(str(rng.randint(0, 9)) for _ in range(7))
        return rng.choice([f"{prefix} {number}", f"{prefix}/{number}", f"+49 {prefix[1:]} {number}"])
    area = rng.choice(["030", "040", "089", "0221", "0341", "0431"])
    number = "".join(str(rng.randint(1, 9)) for _ in range(rng.randint(6, 8)))
    return rng.choice([f"{area} {number}", f"{area}-{number}", f"({area}) {number[:3]} {number[3:]}",
                       f"+49 {area[1:]} {number}", f"+49 (0){area[1:]} {number}"])


def _ascii(name: str) -> str:
    return (name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
            .replace(" ", ""))


@dataclass
class Person:
    first: str
    last: str
    female: bool
    birth: tuple[int, int, int]
    birthplace: str
    street: str
    plz: str
    city: str
    phone: str
    mobile: str
    email: str
    iban: str
    kontonummer: str
    steuer_id: str
    sv: str
    kvnr: str
    ausweis: str
    personalnr: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    def birth_text(self, rng: random.Random) -> str:
        d, m, y = self.birth
        return rng.choice([f"{d:02d}.{m:02d}.{y}", f"{d}. {MONTHS[m - 1]} {y}"])


FEMALE = {"Erika", "Lena", "Sophie", "Mia", "Hannah", "Aylin", "Olga", "Chiara", "Leonie", "Clara", "Marie", "Greta"}


def make_person(rng: random.Random) -> Person:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    birth = (rng.randint(1, 28), rng.randint(1, 12), rng.randint(1950, 2004))
    return Person(
        first=first, last=last, female=first in FEMALE, birth=birth, birthplace=rng.choice(CITIES),
        street=f"{rng.choice(STREETS)} {rng.randint(1, 120)}{rng.choice(['', '', 'a', 'b'])}",
        plz=f"{rng.randint(10000, 99999)}", city=rng.choice(CITIES),
        phone=make_phone(rng), mobile=make_phone(rng, mobile=True),
        email=rng.choice([f"{_ascii(first)}.{_ascii(last)}@example.org", f"{_ascii(first)[0]}{_ascii(last)}@mail.example.com"]),
        iban=make_iban(rng), kontonummer=str(rng.randint(10 ** 7, 10 ** 10)), steuer_id=make_steuer_id(rng),
        sv=make_sv_nummer(rng, birth, last), kvnr=make_kvnr(rng), ausweis=make_ausweis(rng),
        personalnr=rng.choice([str(rng.randint(10000, 999999)), f"P-{rng.randint(1000, 99999)}",
                               f"MA-{rng.randint(10, 99)}-{rng.randint(100, 999)}", f"0{rng.randint(10000, 99999)}"]),
    )


# ---------------------------------------------------------------------------
# Vorlagen
# ---------------------------------------------------------------------------

def _personalnr_label(rng):
    return rng.choice(["Personalnummer:", "Pers.-Nr.:", "Pers.-Nr.", "MA-Nr.:", "Mitarbeiter-ID:", "Personal-Nr.:",
                       "Personalnr.", "Mitarbeiternummer:"])


def personalakte(rng, p: Person) -> list[str]:
    return [
        "Personalakte – vertraulich",
        f"Name: [[person|{p.name}]]",
        f"{_personalnr_label(rng)} [[personalnummer|{p.personalnr}]]",
        f"Anschrift: [[adresse|{p.street}]], [[adresse|{p.plz} {p.city}]]",
        f"Telefon: [[telefon|{p.phone}]]",
        f"Mobil: [[telefon|{p.mobile}]]",
        f"E-Mail: [[email|{p.email}]]",
        f"Geburtsdatum: [[geburtsdatum|{p.birth_text(rng)}]]",
        f"Geburtsort: [[geburtsort|{p.birthplace}]]",
        f"Steuer-ID: [[steuer_id|{p.steuer_id}]]",
        f"Sozialversicherungsnummer: [[sv_nummer|{p.sv}]]",
        f"Bankverbindung: IBAN [[iban|{p.iban}]]",
        f"Eintritt: [[-|01.04.2019]]   Abteilung: Einkauf",
    ]


def arztbrief(rng, p: Person) -> list[str]:
    diagnosis, icd = rng.choice(DIAGNOSES)
    title = "Patientin" if p.female else "Patient"
    return [
        "Arztbrief",
        f"{title}: [[person|{p.name}]], geb. [[geburtsdatum|{p.birth_text(rng)}]]",
        f"Versichertennummer: [[kv_nummer|{p.kvnr}]]",
        f"Wohnhaft: [[adresse|{p.street}]], [[adresse|{p.plz} {p.city}]]",
        f"Diagnose: [[gesundheit|{diagnosis}]] ([[gesundheit|{icd}]])",
        f"Medikation: [[gesundheit|{rng.choice(MEDICATION)}]]",
        f"{'Frau' if p.female else 'Herr'} [[person|{p.last}]] stellte sich am [[-|12.09.2026]] in unserer Praxis vor.",
        f"Rückfragen bitte unter [[telefon|{p.phone}]].",
    ]


def bankformular(rng, p: Person) -> list[str]:
    return [
        "SEPA-Lastschriftmandat",
        f"Kontoinhaber: [[person|{p.name}]]",
        f"Straße: [[adresse|{p.street}]]",
        f"PLZ/Ort: [[adresse|{p.plz} {p.city}]]",
        f"IBAN: [[iban|{p.iban}]]",
        "BIC: MUSTDEFFXXX",
        f"Kontonummer (alt): [[bankverbindung|{p.kontonummer}]]",
        f"Steuerliche Identifikationsnummer: [[steuer_id|{p.steuer_id}]]",
        f"Mandatsreferenz: [[-|MR-{rng.randint(100000, 999999)}]]",
    ]


def lebenslauf(rng, p: Person) -> list[str]:
    return [
        "Lebenslauf",
        f"[[person|{p.name}]]",
        f"[[adresse|{p.street}]]",
        f"[[adresse|{p.plz} {p.city}]]",
        f"Tel. [[telefon|{p.mobile}]]  ·  [[email|{p.email}]]",
        f"geboren am [[geburtsdatum|{p.birth_text(rng)}]] in [[geburtsort|{p.birthplace}]]",
        "Berufserfahrung: 2018 – 2024 Sachbearbeitung bei der Muster GmbH",
        f"Ich freue mich auf Ihre Rückmeldung unter [[telefon|{p.phone}]].",
    ]


def behoerde(rng, p: Person) -> list[str]:
    salutation = "Sehr geehrte Frau" if p.female else "Sehr geehrter Herr"
    return [
        "Bürgeramt Beispielstadt",
        f"Aktenzeichen: [[-|BA-{rng.randint(100, 999)}/2026]]",
        f"{salutation} [[person|{p.last}]],",
        f"Ihre Personalausweisnummer [[ausweis|{p.ausweis}]] ist im Register vermerkt.",
        f"Rentenversicherungsnummer: [[sv_nummer|{p.sv}]]",
        f"Steuer-ID [[steuer_id|{p.steuer_id}]]",
        f"Bitte melden Sie sich unter Angabe Ihrer Mitarbeiter-ID [[personalnummer|{p.personalnr}]].",
        "Mit freundlichen Grüßen",
    ]


def rechnung(rng, p: Person) -> list[str]:
    return [
        "Muster GmbH · Industriestraße 5 · 99999 Firmenstadt",
        f"Rechnungsnummer: [[-|2026-{rng.randint(1000, 9999)}]]",
        f"Bestellnummer: 0815 {rng.randint(1000, 9999)} {rng.randint(10, 99)}",
        f"Lieferschein 0301 {rng.randint(1000, 9999)} {rng.randint(1, 9)}",
        f"Kunde: [[person|{p.name}]]",
        f"Lieferadresse: [[adresse|{p.street}]], [[adresse|{p.plz} {p.city}]]",
        "Seite 1 von 2 · Version 2.0.1 · Raum 0.12",
        f"Gesamtbetrag: [[-|{rng.randint(10, 999)},{rng.randint(10, 99)} €]]",
        f"Ihre Personalnummer für die Abrechnung: [[personalnummer|{p.personalnr}]]",
    ]


TEMPLATES = [personalakte, arztbrief, bankformular, lebenslauf, behoerde, rechnung]


# ---------------------------------------------------------------------------
# Variantenkorpus: andere Formulierungen, Fließtext, Abkürzungen. Geschrieben,
# bevor Ergebnisse darauf gemessen wurden - prüft, ob die Regeln über die
# Vorlagen oben hinaus tragen.
# ---------------------------------------------------------------------------

def v_aktennotiz(rng, p: Person) -> list[str]:
    pron = "Sie" if p.female else "Er"
    return [
        "Aktennotiz Personalabteilung",
        f"{'Frau' if p.female else 'Herr'} [[person|{p.name}]] (Pers.-Nr. [[personalnummer|{p.personalnr}]]) hat sich gemeldet.",
        f"{pron} wohnt jetzt in der [[adresse|{p.street}]] in [[adresse|{p.plz} {p.city}]].",
        f"{pron} ist unter [[telefon|{p.mobile}]] erreichbar, privat auch per Mail an [[email|{p.email}]].",
        f"Personalnummer [[personalnummer|{p.personalnr}]] bitte im Betreff angeben.",
        "Hotline der Muster AG: [[-|0800 123 4567]]",
    ]


def v_krankmeldung(rng, p: Person) -> list[str]:
    diagnosis, icd = rng.choice(DIAGNOSES)
    return [
        "Arbeitsunfähigkeitsbescheinigung",
        f"Versicherte(r): [[person|{p.last}]], [[person|{p.first}]]",
        f"Geb.: [[geburtsdatum|{p.birth_text(rng)}]] in [[geburtsort|{p.birthplace}]]",
        f"Krankenkasse: AOK Musterland, Versicherten-Nr.: [[kv_nummer|{p.kvnr}]]",
        f"Arbeitsunfähig wegen [[gesundheit|{diagnosis}]], ICD [[gesundheit|{icd}]]",
        f"Arzt: Praxis Dr. Muster, Tel. [[-|{make_phone(rng)}]]",
    ]


def v_vertrag(rng, p: Person) -> list[str]:
    return [
        "Arbeitsvertrag zwischen der Muster GmbH und",
        f"[[person|{p.name}]], wohnhaft [[adresse|{p.street}]], [[adresse|{p.plz} {p.city}]],",
        f"geboren am [[geburtsdatum|{p.birth_text(rng)}]] – im Folgenden „Arbeitnehmer“.",
        f"SV-Nr [[sv_nummer|{p.sv}]] · Steuer-IdNr. [[steuer_id|{p.steuer_id}]]",
        f"Gehalt wird überwiesen auf das Konto [[iban|{p.iban}]] bei der Musterbank.",
        f"Artikel-Nr. 0123 {rng.randint(100000, 999999)} (Dienstwagen)",
    ]


def v_email(rng, p: Person) -> list[str]:
    return [
        f"Von: [[person|{p.name}]] <[[email|{p.email}]]>",
        "Betreff: Adressänderung",
        "Hallo zusammen,",
        f"meine neue Anschrift lautet [[adresse|{p.street}]], [[adresse|{p.plz} {p.city}]].",
        f"Meine Handynummer bleibt [[telefon|{p.mobile}]].",
        f"Viele Grüße, [[person|{p.first}]]",
    ]


def v_ausweiskopie(rng, p: Person) -> list[str]:
    return [
        "Kopie Personalausweis (geschwärzte Ablage)",
        f"Ausweis-Nr.: [[ausweis|{p.ausweis}]]",
        f"Name, Vorname: [[person|{p.last}, {p.first}]]",
        f"Geburtstag: [[geburtsdatum|{p.birth_text(rng)}]]",
        f"Geburtsort: [[geburtsort|{p.birthplace}]]",
        f"Gültig bis: [[-|{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.2031]]",
    ]


def v_lohnabrechnung(rng, p: Person) -> list[str]:
    return [
        "Entgeltabrechnung Oktober 2026",
        f"Mitarbeiter: [[person|{p.name}]]   Pers.Nr. [[personalnummer|{p.personalnr}]]",
        f"Steuer-ID: [[steuer_id|{p.steuer_id}]]   RV-Nummer: [[sv_nummer|{p.sv}]]",
        f"Auszahlung an IBAN [[iban|{p.iban}]]",
        f"Auszahlungsbetrag: [[-|{rng.randint(1000, 4999)},{rng.randint(10, 99)} €]]",
        f"Kostenstelle 0{rng.randint(100, 999)} {rng.randint(1000, 9999)}",
    ]


VARIANT_TEMPLATES = [v_aktennotiz, v_krankmeldung, v_vertrag, v_email, v_ausweiskopie, v_lohnabrechnung]


# ---------------------------------------------------------------------------
# Mehrdeutige Fälle (Phase 3): Angaben ohne Beschriftung, bei denen erst der
# Kontext entscheidet - genau die Fälle, die das Sprachmodell (Modus
# "Gründliche KI") zu sehen bekommt. Festgelegt, bevor Modus C darauf lief.
# Nicht ausgezeichnete Daten/Nummern dürfen NICHT vorausgewählt werden.
# ---------------------------------------------------------------------------

def _date(rng) -> str:
    return f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.choice([2025, 2026, 2027])}"


def m_geburtstag(rng, p: Person) -> list[str]:
    d, m, y = p.birth
    return [
        f"Beispielstadt, {_date(rng)}",
        "Liebe Kolleginnen und Kollegen,",
        f"unser Kollege [[person|{p.name}]] hat am [[geburtsdatum|{d:02d}.{m:02d}.{y}]] das Licht der Welt erblickt.",
        f"Die Feier findet am {_date(rng)} um 15 Uhr in Raum 2.14 statt.",
        "Bitte bis Freitag zusagen.",
    ]


def m_telefonliste(rng, p: Person) -> list[str]:
    return [
        "Wichtige Nummern für die Rufbereitschaft",
        f"Zentrale Stadtwerke Musterhausen: {make_phone(rng)}",
        f"Störungsdienst rund um die Uhr: 0800 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
        f"Bereitschaft diese Woche: [[person|{p.name}]], [[telefon|{p.mobile}]]",
        f"Empfang Rathaus: {make_phone(rng)}",
    ]


def m_abwesenheit(rng, p: Person) -> list[str]:
    diagnosis, _ = rng.choice(DIAGNOSES)
    return [
        "Teamnotiz",
        f"[[person|{p.first}]] fehlt diese Woche, weil [[gesundheit|{diagnosis}]] behandelt wird.",
        "Die Kantine bleibt wegen einer Grippewelle bis Montag geschlossen.",
        "Zur Grippeimpfung informiert der Betriebsarzt am Aushang.",
        f"Vertretung übernimmt [[person|{rng.choice(FIRST)} {rng.choice(LAST)}]].",
    ]


def m_vertragsnotiz(rng, p: Person) -> list[str]:
    d, m, y = p.birth
    return [
        "Notiz zum Mietvertrag",
        f"Vertragsbeginn {_date(rng)}, Kündigungsfrist bis {_date(rng)}.",
        f"Mieterin/Mieter: [[person|{p.name}]], [[geburtsdatum|{d:02d}.{m:02d}.{y}]]",
        f"Hausverwaltung Muster Immobilien GmbH, Service {make_phone(rng)}",
        f"Erreichbar ist [[person|{p.first}]] am besten abends: [[telefon|{p.phone}]]",
    ]


def m_protokoll(rng, p: Person) -> list[str]:
    return [
        f"Protokoll der Teamsitzung vom {_date(rng)}",
        f"Anwesend: [[person|{p.name}]], [[person|{rng.choice(FIRST)} {rng.choice(LAST)}]]",
        f"Nächster Termin: {_date(rng)}, Abgabe der Berichte bis {_date(rng)}.",
        f"[[person|{p.first}]] berichtet von [[gesundheit|{rng.choice(['Rückenschmerzen', 'einer Allergie', 'Schlafstörungen'])}]] seit Wochen.",
        "Allgemeiner Hinweis: Rückenschmerzen lassen sich durch Pausen vorbeugen.",
    ]


AMBIGUOUS_TEMPLATES = [m_geburtstag, m_telefonliste, m_abwesenheit, m_vertragsnotiz, m_protokoll]

# Zusätzliche Zeilen ohne personenbezogene Daten, die Zahlen enthalten,
# die wie Telefonnummern aussehen (Fehlalarm-Fallen).
NEGATIVES = [
    lambda rng: f"Vorgang {rng.choice(['0', '04'])}{rng.randint(100, 999)} {rng.randint(1000, 9999)} {rng.randint(1, 9)}",
    lambda rng: f"Referenz {rng.randint(100000, 999999)}",
    lambda rng: f"Code: 0{rng.randint(1000, 9999)} {rng.randint(1000, 9999)}",
    lambda rng: f"Telefonzentrale der Muster GmbH: siehe Website",
    lambda rng: f"Stand: [[-|{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.2026]]",
]


@dataclass
class Gold:
    line: int
    start: int
    text: str
    category: str  # "-" = neutral


@dataclass
class Document:
    name: str
    lines: list[str]
    gold: list[Gold] = field(default_factory=list)


_MARK = re.compile(r"\[\[([a-z_\-]+)\|(.+?)\]\]")


def _parse(lines: list[str]) -> tuple[list[str], list[Gold]]:
    clean, gold = [], []
    for i, line in enumerate(lines):
        out, pos = "", 0
        for m in _MARK.finditer(line):
            out += line[pos:m.start()]
            gold.append(Gold(i, len(out), m.group(2), m.group(1)))
            out += m.group(2)
            pos = m.end()
        out += line[pos:]
        clean.append(out)
    return clean, gold


def generate(seed: int, per_template: int = 10, variants: bool = False, ambiguous: bool = False) -> list[Document]:
    rng = random.Random(seed)
    docs = []
    templates = AMBIGUOUS_TEMPLATES if ambiguous else VARIANT_TEMPLATES if variants else TEMPLATES
    for t in templates:
        for n in range(per_template):
            person = make_person(rng)
            raw = t(rng, person)
            for _ in range(0 if ambiguous else rng.randint(1, 2)):
                raw.insert(rng.randint(1, len(raw)), rng.choice(NEGATIVES)(rng))
            lines, gold = _parse(raw)
            docs.append(Document(f"{t.__name__}-{n + 1:02d}", lines, gold))
    return docs
