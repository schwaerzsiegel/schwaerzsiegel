import { useState } from "react";
import type { AnalysisMode, ModeAvailability } from "../api/client";
import { Modal } from "./Modal";
import { ModeOptions, type MainMode } from "./ModeOptions";

export type FinalMode = "redact" | "pseudonymize";

// Phase 4: "Schwärzung anwenden" - der Klick auf "Jetzt schwärzen" ist die
// ausdrückliche Freigabe der aktuellen Auswahl (vorher ein eigener Schritt).
export function ApplyDialog({
  toRedact,
  open,
  rejected,
  busy,
  onCancel,
  onConfirm,
}: {
  toRedact: number;
  open: number;
  rejected: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (mode: FinalMode) => void;
}) {
  const [mode, setMode] = useState<FinalMode>("redact");
  return (
    <Modal
      title="Auswahl freigeben und schwärzen"
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>
            Abbrechen
          </button>
          <button className="btn btn-primary" disabled={busy || toRedact === 0} onClick={() => onConfirm(mode)}>
            {mode === "redact" ? "Jetzt schwärzen und prüfen" : "Jetzt pseudonymisieren und prüfen"}
          </button>
        </>
      }
    >
      <ul className="summary-list">
        <li>
          <span>Werden {mode === "redact" ? "geschwärzt" : "ersetzt"}</span>
          <strong>{toRedact}</strong>
        </li>
        <li>
          <span>Offene Vorschläge (werden nicht geschwärzt)</span>
          <strong style={{ color: open ? "var(--warn)" : undefined }}>{open}</strong>
        </li>
        <li>
          <span>Verworfen (bleiben sichtbar)</span>
          <strong>{rejected}</strong>
        </li>
      </ul>
      {open > 0 && (
        <div className="hint-box">
          {open} Vorschlag/Vorschläge sind noch nicht geprüft und bleiben im Ergebnis sichtbar. Falls das nicht gewollt ist:
          abbrechen und in der Liste „Offen“ bestätigen.
        </div>
      )}
      <div role="radiogroup" aria-label="Art der Verarbeitung" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label className={`radio-option ${mode === "redact" ? "is-selected" : ""}`}>
          <input type="radio" name="final-mode" checked={mode === "redact"} onChange={() => setMode("redact")} />
          <span>
            <strong>Irreversibel schwärzen</strong>
            <small>Inhalte werden dauerhaft aus dem PDF entfernt.</small>
          </span>
        </label>
        <label className={`radio-option ${mode === "pseudonymize" ? "is-selected" : ""}`}>
          <input type="radio" name="final-mode" checked={mode === "pseudonymize"} onChange={() => setMode("pseudonymize")} />
          <span>
            <strong>Pseudonymisieren</strong>
            <small>Angaben werden durch Platzhalter ersetzt; die Zuordnungstabelle getrennt vom PDF aufbewahren.</small>
          </span>
        </label>
      </div>
      <p className="side-hint">Danach wird die gespeicherte Datei automatisch geprüft. Erst dann ist der Export möglich.</p>
    </Modal>
  );
}

// Phase 10: Beim Öffnen eines anderen Dokuments wird das bisherige samt
// Ergebnisdateien gelöscht - vorher warnen, wenn die Zuordnungstabelle einer
// Pseudonymisierung noch nicht gespeichert wurde.
export function UnsavedMappingDialog({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  return (
    <Modal
      title="Zuordnungstabelle nicht gespeichert"
      onClose={onCancel}
      footer={
        <>
          <button className="btn btn-primary" onClick={onCancel}>
            Zurück und speichern
          </button>
          <button className="btn" onClick={onConfirm}>
            Trotzdem anderes Dokument öffnen
          </button>
        </>
      }
    >
      <p>
        Sie haben die Zuordnungstabelle dieser Pseudonymisierung noch nicht gespeichert. Beim Öffnen eines anderen Dokuments
        wird sie gelöscht. Danach lässt sich die Pseudonymisierung nicht mehr zurückführen.
      </p>
    </Modal>
  );
}

export function ReanalyzeDialog({
  current,
  availability,
  onCancel,
  onStart,
}: {
  current: string;
  availability: Partial<Record<AnalysisMode, ModeAvailability>>;
  onCancel: () => void;
  onStart: (mode: MainMode) => void;
}) {
  const [mode, setMode] = useState<MainMode>(current === "rules" || current === "ai" ? current : "fast");
  return (
    <Modal
      title="Erneut analysieren"
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>
            Abbrechen
          </button>
          <button className="btn btn-primary" disabled={availability[mode] && !availability[mode]!.available} onClick={() => onStart(mode)}>
            Analyse starten
          </button>
        </>
      }
    >
      <p>
        Manuelle Markierungen und alle bereits bestätigten oder verworfenen Fundstellen bleiben erhalten. Nur offene Vorschläge
        werden durch das neue Ergebnis ersetzt.
      </p>
      <ModeOptions value={mode} onChange={setMode} availability={availability} layout="list" />
    </Modal>
  );
}
