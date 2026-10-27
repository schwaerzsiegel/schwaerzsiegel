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
