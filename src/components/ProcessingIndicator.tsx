import type { AnalysisMode, Progress } from "../api/client";
import { modeName } from "./ModeOptions";

// v31: Zeigt den echten Fortschritt aus dem Sidecar (Seite x von y und den
// gerade laufenden Schritt). Phase 3: Abbrechen. Phase 4: neues Erscheinungsbild.
export function ProcessingIndicator({
  progress,
  analysisMode = "fast",
  onCancel,
  title,
}: {
  progress: Progress | null;
  analysisMode?: AnalysisMode;
  // Analyse abbrechen (wirkt zwischen zwei Seiten bzw. KI-Anfragen)
  onCancel?: () => void;
  title?: string;
}) {
  const pageCount = progress?.page_count ?? 0;
  const page = progress?.page ?? 0;
  // Die laufende Seite zählt erst als erledigt, wenn sie fertig ist.
  const percent = pageCount > 0 ? Math.round((Math.max(page - 1, 0) / pageCount) * 100) : 0;

  return (
    <div className="processing-card" role="status" aria-live="polite">
      <p className="processing-title">
        {title ?? (analysisMode === "manual" ? "Dokument wird geöffnet" : "Dokument wird analysiert")}
      </p>
      <p className="processing-subtitle">
        {analysisMode === "manual" ? "Ohne automatische Analyse" : modeName(analysisMode)} · läuft vollständig lokal, keine
        Daten verlassen dieses Gerät.
      </p>
      {analysisMode === "ai" && (
        <p className="processing-subtitle">
          Gründliche KI: Das dauert länger, weil das Sprachmodell jeden unsicheren Treffer auf diesem Rechner prüft.
        </p>
      )}

      {pageCount > 0 && page > 0 && (
        <p className="processing-page">
          Seite {page} von {pageCount}
        </p>
      )}

      <div className="processing-bar" aria-hidden="true">
        <div className="processing-bar-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="processing-step">
        <span className="processing-spinner" aria-hidden="true" />
        <span>{progress?.step ?? "Dokument wird hochgeladen"}</span>
      </div>

      {onCancel && (
        <button className="btn btn-small" style={{ marginTop: 12 }} onClick={onCancel}>
          Analyse abbrechen
        </button>
      )}
    </div>
  );
}
