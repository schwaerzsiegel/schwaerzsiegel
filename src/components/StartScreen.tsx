import { useState } from "react";
import type { AnalysisMode, ModeAvailability } from "../api/client";
import { Icon } from "./Icon";
import { ModeOptions, type MainMode } from "./ModeOptions";

export interface SystemStatus {
  offlineGuard: boolean;
  glinerText: string | null;
  glinerOk: boolean;
  llmText: string | null;
  llmOk: boolean;
  ocrOk: boolean;
}

// Phase 4: Startbildschirm laut Designvorschlag (Bildschirm 1).
export function StartScreen({
  mode,
  onModeChange,
  availability,
  onFile,
  disabled,
  status,
}: {
  mode: AnalysisMode;
  onModeChange: (mode: AnalysisMode) => void;
  availability: Partial<Record<AnalysisMode, ModeAvailability>>;
  onFile: (file: File) => void;
  disabled?: boolean;
  status: SystemStatus | null;
}) {
  const [dragging, setDragging] = useState(false);
  const skip = mode === "manual";
  // Beim Überspringen bleibt die zuletzt gewählte Betriebsart sichtbar
  const [lastMain, setLastMain] = useState<MainMode>(mode === "manual" ? "fast" : mode);

  function pick(next: MainMode) {
    setLastMain(next);
    onModeChange(next);
  }

  return (
    <main className="start-main">
      <div className="start-inner">
        <div className="start-intro">
          <h1>PDF sicher schwärzen</h1>
          <p>
            Das Dokument wird ausschließlich auf diesem Rechner verarbeitet.
            <br />
            Die Erkennung schlägt vor – was geschwärzt wird, entscheiden Sie.
          </p>
        </div>

        <label
          className={`dropzone ${dragging ? "is-dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !disabled) onFile(file);
          }}
        >
          <span className="dropzone-icon">
            <Icon name="upload" size={36} strokeWidth={1.5} />
          </span>
          <span className="dropzone-title">PDF hierher ziehen oder auswählen</span>
          <span className="dropzone-hint">bis 100 MB und 1.000 Seiten · passwortgeschützte PDFs werden nicht unterstützt</span>
          <span className="btn btn-primary">Datei auswählen …</span>
          <input
            className="visually-hidden"
            type="file"
            accept="application/pdf"
            aria-label="PDF-Datei auswählen"
            disabled={disabled}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) onFile(file);
            }}
          />
        </label>

        <fieldset className="mode-fieldset">
          <legend>Wie soll analysiert werden?</legend>
          <ModeOptions value={skip ? lastMain : mode} onChange={pick} availability={availability} disabled={disabled || skip} />
          <label className="check-option">
            <input type="checkbox" checked={skip} disabled={disabled} onChange={(e) => onModeChange(e.target.checked ? "manual" : lastMain)} />
            Analyse überspringen und direkt manuell markieren
          </label>
        </fieldset>

        {status && (
          <div className="system-status" aria-label="Systemzustand">
            {status.offlineGuard && (
              <span className="status-item ok">
                <Icon name="check" size={14} strokeWidth={2} />
                Netzwerkzugriffe gesperrt
              </span>
            )}
            {status.glinerText && (
              <span className={`status-item ${status.glinerOk ? "ok" : "warn"}`}>
                <Icon name={status.glinerOk ? "check" : "alert"} size={14} strokeWidth={2} />
                {status.glinerText}
              </span>
            )}
            {status.llmText && (
              <span className={`status-item ${status.llmOk ? "ok" : "warn"}`}>
                <Icon name={status.llmOk ? "check" : "alert"} size={14} strokeWidth={2} />
                {status.llmText}
              </span>
            )}
            <span className={`status-item ${status.ocrOk ? "ok" : "warn"}`}>
              <Icon name={status.ocrOk ? "check" : "alert"} size={14} strokeWidth={2} />
              {status.ocrOk ? "Texterkennung für Scans (OCR) bereit" : "Texterkennung für Scans (OCR) nicht installiert – Scans per Rechteck schwärzen"}
            </span>
          </div>
        )}
      </div>
    </main>
  );
}
