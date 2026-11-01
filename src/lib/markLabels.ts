import type { Mark, MarkOrigin, MarkStatus } from "../api/client";

// Gemeinsam von Seitenansicht und Liste genutzt, damit dieselbe Markierung
// überall gleich heißt und gleich aussieht.

export const STATUS_LABEL: Record<MarkStatus, string> = {
  suggested: "Vorgeschlagen",
  confirmed: "Bestätigt",
  rejected: "Verworfen",
  redacted: "Geschwärzt",
};

export const STATUS_HINT: Record<MarkStatus, string> = {
  suggested: "Automatisch gefunden, noch nicht geprüft – wird nur geschwärzt, wenn Sie es bestätigen.",
  confirmed: "Wird beim Schwärzen entfernt.",
  rejected: "Bleibt sichtbar. Kann jederzeit wieder bestätigt werden.",
  redacted: "Im aktuellen, geprüften Ergebnis geschwärzt.",
};

export const ORIGIN_LABEL: Record<MarkOrigin, string> = {
  manual: "Manuell",
  rule: "Regel",
  ai: "KI",
};

export const ORIGIN_HINT: Record<MarkOrigin, string> = {
  manual: "Von Ihnen markiert",
  rule: "Von einer Regel gefunden (Muster, Prüfsumme oder Bild auf der Seite)",
  ai: "Vom lokalen KI-Modell gefunden",
};

/** Auswahl beim Ändern der Kategorie. Eigene Bezeichnungen sind über
 *  "Sonstiges …" möglich. */
export const CATEGORIES = [
  "Person",
  "Adresse",
  "Telefonnummer",
  "E-Mail",
  "Geburtsdatum",
  "Sterbedatum",
  "Geburtsort",
  "Personalnummer",
  "Organisationszeichen",
  "IBAN",
  "Bankverbindung",
  "Steuer-ID",
  "Steuernummer",
  "Sozialversicherungsnummer",
  "Krankenversichertennummer",
  "Ausweisnummer",
  "Gesundheitsdaten",
  "Gesundheitsdaten (ICD-10)",
  "Unterschrift",
  "Handschrift oder Stempel",
  "Bild",
  "Organisation",
  "Firmenanschrift",
  "Service-Rufnummer",
  "Datum",
  "Betrag",
  "Markierter Text",
  "Manueller Bereich",
];

export function markTitle(m: Mark): string {
  const text = m.text && m.kind === "text" ? `: ${m.text}` : "";
  return `${m.label}${text} – ${STATUS_LABEL[m.status]} (${ORIGIN_LABEL[m.origin]})`;
}

export function isOpen(m: Mark): boolean {
  return m.status === "suggested";
}

/** Ab dieser Sicherheit gilt ein Vorschlag als "eindeutig" (Kontext,
 *  Prüfziffer, sehr sicheres Modell); darunter "unsicher – bitte prüfen". */
export const SURE_CONFIDENCE = 0.85;
export const UNSURE_CONFIDENCE = 0.75;

export function isUnsure(m: Mark): boolean {
  return m.status === "suggested" && m.origin !== "manual" && m.confidence < UNSURE_CONFIDENCE;
}

/** Phase 16: Offene Vorschläge, die nicht zur Schwärzung vorgesehen sind (z. B. Organisationen, Daten,
 *  Beträge oder von der KI abgestufte Treffer). Sie stehen in der Liste zunächst eingeklappt, damit
 *  lange Dokumente übersichtlich bleiben – verloren geht nichts. */
export function isMinor(m: Mark): boolean {
  return m.status === "suggested" && m.origin !== "manual" && !m.default_selected;
}

/** Anzahl je Kategorie, häufigste zuerst – für die Zeile „n weitere Fundstellen (…)“. */
export function minorSummary(marks: Mark[]): { label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const m of marks) if (isMinor(m)) counts.set(m.label, (counts.get(m.label) ?? 0) + 1);
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "de"));
}

/** Phase 16: gleiche Werte im ganzen Dokument (vorher nur auf derselben Seite). */
export function sameValueMarks(marks: Mark[], m: Mark): Mark[] {
  const key = m.text.trim().toLowerCase();
  return key ? marks.filter((x) => x.text.trim().toLowerCase() === key) : [m];
}

// Phase 20: Personen im ganzen Dokument – alle Stellen einer Person auf einmal schwärzen oder auslassen.
// „Frau Hansen“, „Wiebke Hansen“, „Hansen,“ und „gez. Hansen“ gehören zu derselben Person.

const NOT_A_NAME = new Set(["herr", "herrn", "frau", "hr", "fr", "dr", "prof", "med", "jur", "rer", "nat", "dipl", "ing", "gez", "ppa"]);
const NAME_WORD = /^[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*$/;

/** Namenswörter eines Personen-Treffers ohne Anrede, Titel und Satzzeichen. */
export function nameWords(text: string): string[] {
  return text
    .split(/\s+/)
    .map((w) => w.replace(/^[^A-Za-zÄÖÜäöüß]+|[^A-Za-zÄÖÜäöüß]+$/g, ""))
    .filter((w) => NAME_WORD.test(w) && !NOT_A_NAME.has(w.toLowerCase()));
}

/** Nachname: das letzte Namenswort – bei „Nachname, Vorname“ das erste. */
function surname(text: string): string | null {
  const words = nameWords(text);
  if (!words.length) return null;
  const core = text.trim().replace(/[,;.]+$/, "");
  const comma = core.indexOf(",");
  const commaFirst = words.length > 1 && comma > 0 && nameWords(core.slice(0, comma)).length > 0;
  return commaFirst ? words[0] : words[words.length - 1];
}

export interface PersonGroup {
  key: string;
  name: string;
  marks: Mark[];
  open: number;
  confirmed: number;
  rejected: number;
}

const TITLE_WORDS = /^(Dr\.|Prof\.)$/;

/** Anzeigename: die ausführlichste Schreibweise (mit Titel, ohne Anrede), z. B. „Dr. Jana Beispiel“. */
function displayName(texts: string[]): string {
  let best = "";
  let bestCount = -1;
  for (const t of texts) {
    const words = t
      .split(/\s+/)
      .map((w) => w.replace(/^[(„"]+/, ""))
      .map((w) => (TITLE_WORDS.test(w.replace(/[,;:)]+$/, "")) ? w.replace(/[,;:)]+$/, "") : w.replace(/[.,;:)“"]+$/, "")));
    const kept = words.filter((w) => TITLE_WORDS.test(w) || nameWords(w).length > 0);
    const count = kept.filter((w) => !TITLE_WORDS.test(w)).length;
    if (count > bestCount) {
      best = kept.join(" ");
      bestCount = count;
    }
  }
  return best;
}

/** Personen-Treffer nach Person gruppiert, Personen mit den meisten Stellen zuerst.
 *  Phase 21: Gibt es zu einem Nachnamen mehrere Vornamen („Wiebke Hansen“, „Jan Hansen“), bekommt jede
 *  Person eine eigene Zeile; Stellen nur mit Nachnamen („Frau Hansen“) stehen dann in einer eigenen Zeile
 *  „Hansen (Vorname nicht genannt)“, weil sie keiner der Personen sicher zuzuordnen sind. */
export function personGroups(marks: Mark[]): PersonGroup[] {
  const persons = marks.filter((m) => m.label === "Person" && m.text);
  const keys = new Set<string>();
  const firstNames = new Map<string, Set<string>>(); // Vorname -> Nachnamen
  const givenNames = new Map<string, Set<string>>(); // Nachname -> Vornamen
  for (const m of persons) {
    const words = nameWords(m.text);
    const key = surname(m.text);
    if (!key || words.length < 2) continue;
    keys.add(key);
    const given = words.filter((w) => w !== key);
    for (const w of given) firstNames.set(w, (firstNames.get(w) ?? new Set()).add(key));
    if (given.length) givenNames.set(key, (givenNames.get(key) ?? new Set()).add(given[0]));
  }
  const shared = (key: string) => (givenNames.get(key)?.size ?? 0) > 1;
  const groups = new Map<string, Mark[]>();
  const unnamed = new Set<string>();
  for (const m of persons) {
    const words = nameWords(m.text);
    let key = surname(m.text);
    if (!key) continue;
    let given = words.filter((w) => w !== key)[0];
    if (words.length === 1 && !keys.has(key) && firstNames.get(key)?.size === 1) {
      given = key; // nur der Vorname („Wiebke“)
      key = [...firstNames.get(key)!][0];
    }
    if (shared(key)) {
      if (given && givenNames.get(key)!.has(given)) key = `${key}|${given}`;
      else unnamed.add((key = `${key}|`));
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(m);
  }
  return [...groups.entries()]
    .map(([key, ms]) => ({
      key,
      name: unnamed.has(key)
        ? `${key.slice(0, -1)} (Vorname nicht genannt)`
        : displayName(ms.map((m) => m.text)) || key,
      marks: ms,
      open: ms.filter((m) => m.status === "suggested").length,
      confirmed: ms.filter((m) => m.status === "confirmed" || m.status === "redacted").length,
      rejected: ms.filter((m) => m.status === "rejected").length,
    }))
    .sort((a, b) => b.marks.length - a.marks.length || a.name.localeCompare(b.name, "de"));
}

/** Alle Stellen derselben Person (nur für Personen-Treffer, sonst nur die Markierung selbst). */
export function samePersonMarks(marks: Mark[], m: Mark): PersonGroup | null {
  if (m.label !== "Person") return null;
  return personGroups(marks).find((g) => g.marks.some((x) => x.id === m.id)) ?? null;
}
