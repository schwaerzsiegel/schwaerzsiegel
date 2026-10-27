import type { AnalysisMode, ModeAvailability } from "../api/client";

export type MainMode = Exclude<AnalysisMode, "manual">;

export const MODES: { key: MainMode; letter: string; name: string; text: string; speed: string; ram: string; note?: string }[] = [
  {
    key: "rules",
    letter: "A",
    name: "Ohne KI",
    text: "Regeln mit Kontext und Prüfziffern: Personal-, Steuer- und Versicherungsnummern, IBAN, Telefon, E-Mail, Anschriften, beschriftete Namen.",
    speed: "sofort",
    ram: "0,4 GB",
  },
  {
    key: "fast",
    letter: "B",
    name: "Schnell-KI",
    text: "Zusätzlich findet ein lokales KI-Modell Namen, Adressen und weitere Angaben im Fließtext.",
    speed: "≈ 1–7 s pro Seite",
    ram: "2,6 GB",
    note: "Empfohlen für die meisten Dokumente",
  },
  {
    key: "ai",
    letter: "C",
    name: "Gründliche KI",
    text: "Zusätzlich prüft ein lokales Sprachmodell unsichere Treffer im Kontext – z. B. Geburtsdatum oder Briefdatum.",
    speed: "≈ 6–12 s pro Seite",
    ram: "4,2 GB",
  },
];

export function modeName(mode: string): string {
  const m = MODES.find((x) => x.key === mode);
  return m ? `${m.letter} · ${m.name}` : "Nur manuell";
}

// Phase 4: Auswahl der Betriebsart als Karten (Startbildschirm) oder als
// kompakte Liste (Dialog "Erneut analysieren"). Nicht verfügbare Modi sind
// gesperrt und nennen den Grund.
export function ModeOptions({
  value,
  onChange,
  availability,
  disabled,
  layout = "cards",
}: {
  value: AnalysisMode;
  onChange: (mode: MainMode) => void;
  availability?: Partial<Record<AnalysisMode, ModeAvailability>>;
  disabled?: boolean;
  layout?: "cards" | "list";
}) {
  return (
    <div className={layout === "cards" ? "mode-grid" : "summary-list"} role="radiogroup" aria-label="Analyse-Modus">
      {MODES.map((m) => {
        const info = availability?.[m.key];
        const unavailable = !!info && !info.available;
        const selected = value === m.key;
        const input = (
          <input
            type="radio"
            name={`modus-${layout}`}
            checked={selected}
            disabled={disabled || unavailable}
            onChange={() => onChange(m.key)}
          />
        );
        if (layout === "list") {
          return (
            <label key={m.key} className={`radio-option ${selected ? "is-selected" : ""}`}>
              {input}
              <span>
                <strong>
                  {m.letter} · {m.name}
                </strong>
                <small>
                  {m.speed} · {m.ram} Arbeitsspeicher
                </small>
                {unavailable && <small className="mode-problem">Nicht verfügbar: {info?.problem}</small>}
              </span>
            </label>
          );
        }
        return (
          <label key={m.key} className={`mode-card ${selected ? "is-selected" : ""} ${unavailable ? "is-disabled" : ""}`}>
            <span className="mode-card-head">
              {input}
              <span className="mode-letter">{m.letter}</span>
              <span className="mode-name">{m.name}</span>
            </span>
            <span className="mode-text">{m.text}</span>
            <span className="mode-chips">
              <span className="chip-info">{m.speed}</span>
              <span className="chip-info">{m.ram}</span>
            </span>
            {unavailable ? (
              <span className="mode-problem">Nicht verfügbar: {info?.problem}</span>
            ) : info?.loading ? (
              <span className="mode-note">Modell wird geladen …</span>
            ) : (
              m.note && <span className="mode-note">{m.note}</span>
            )}
          </label>
        );
      })}
    </div>
  );
}
