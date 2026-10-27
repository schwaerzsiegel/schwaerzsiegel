import { useMemo, useState } from "react";
import type { Mark, MarkStatus } from "../api/client";
import {
  CATEGORIES,
  ORIGIN_HINT,
  ORIGIN_LABEL,
  STATUS_HINT,
  STATUS_LABEL,
  SURE_CONFIDENCE,
  isMinor,
  isUnsure,
  minorSummary,
  sameValueMarks,
} from "../lib/markLabels";
import { Icon } from "./Icon";

type Filter = "all" | "suggested" | "unsure" | "confirmed" | "rejected";

const FILTERS: { key: Filter; label: string; hint?: string }[] = [
  { key: "all", label: "Alle" },
  { key: "suggested", label: "Offen" },
  { key: "unsure", label: "Unsicher", hint: "Offene Vorschläge mit geringer Erkennungssicherheit – gezielt prüfen" },
  { key: "confirmed", label: "Bestätigt" },
  { key: "rejected", label: "Verworfen" },
];

interface Props {
  marks: Mark[];
  currentPage: number;
  selectedId: string | null;
  hoveredId: string | null;
  disabled?: boolean;
  onFocus: (mark: Mark) => void;
  onHover: (id: string | null) => void;
  onStatus: (marks: Mark[], status: Exclude<MarkStatus, "redacted">) => void;
  onLabel: (mark: Mark, label: string) => void;
  onDelete: (mark: Mark) => void;
}

function matches(m: Mark, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "unsure") return isUnsure(m);
  if (filter === "confirmed") return m.status === "confirmed" || m.status === "redacted";
  return m.status === filter;
}

function reasonText(m: Mark): string | null {
  if (m.origin === "manual") return "Von Ihnen markiert";
  const parts = [m.reason, `${Math.round(m.confidence * 100)} %`].filter(Boolean);
  return parts.join(" · ");
}

// Phase 1: eine gemeinsame Liste für manuelle und automatisch erkannte
// Markierungen aller Seiten, gruppiert nach Seite. Phase 4: Gestaltung
// laut Designvorschlag (rechte Spalte, "Fundstellen").
export function MarkList({ marks, currentPage, selectedId, hoveredId, disabled, onFocus, onHover, onStatus, onLabel, onDelete }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [onlyThisPage, setOnlyThisPage] = useState(false);
  // Eigene Kategorie ("Sonstiges …") direkt in der Liste eingeben
  const [customLabel, setCustomLabel] = useState<{ id: string; value: string } | null>(null);
  // Phase 16: nicht zur Schwärzung vorgesehene Vorschläge (Organisationen, Daten …) zunächst eingeklappt
  const [showMinor, setShowMinor] = useState(false);

  const minorApplies = filter === "all" || filter === "suggested" || filter === "unsure";
  const inScope = marks.filter((m) => matches(m, filter) && (!onlyThisPage || m.page === currentPage));
  const minor = minorApplies ? inScope.filter(isMinor) : [];
  const visible = showMinor || !minorApplies ? inScope : inScope.filter((m) => !isMinor(m));
  const minorParts = minorSummary(minor);
  const byPage = useMemo(() => {
    const groups = new Map<number, Mark[]>();
    for (const m of visible) {
      if (!groups.has(m.page)) groups.set(m.page, []);
      groups.get(m.page)!.push(m);
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0]);
  }, [visible]);

  // Phase 2: Sammelbestätigung nur für sichere Vorschläge (Kontext,
  // Prüfziffer, sehr sicheres Modell). Unsichere bleiben zur Einzelprüfung.
  const preselected = marks.filter((m) => m.status === "suggested" && m.default_selected && m.confidence >= SURE_CONFIDENCE);
  const unsureCount = marks.filter(isUnsure).length;
  const count = (f: Filter) => marks.filter((m) => matches(m, f)).length;

  // Gleicher Text im ganzen Dokument: eine Entscheidung für alle gleichen Werte (Phase 16, vorher je Seite)
  const sameText = (m: Mark) => sameValueMarks(marks, m);

  return (
    <>
      <div className="panel-head">
        <div className="panel-title-row">
          <h2>Fundstellen</h2>
          <label className="page-filter">
            <input type="checkbox" checked={onlyThisPage} onChange={(e) => setOnlyThisPage(e.target.checked)} />
            nur diese Seite
          </label>
        </div>
        <div className="filter-chips" role="group" aria-label="Filter">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              aria-pressed={filter === f.key}
              className={`filter-chip ${filter === f.key ? "is-active" : ""}`}
              title={f.hint}
              onClick={() => setFilter(f.key)}
            >
              {f.label} <span className="filter-count">{count(f.key)}</span>
            </button>
          ))}
        </div>
        {preselected.length > 0 && (
          <button
            className="btn btn-soft btn-block"
            disabled={disabled}
            onClick={() => onStatus(preselected, "confirmed")}
            title="Bestätigt alle offenen Vorschläge mit hoher Sicherheit (ab 85 %), die zur Schwärzung vorgesehen sind. Unsichere Vorschläge sowie Datum, Betrag und Organisationen bleiben zur Einzelprüfung."
          >
            {preselected.length} sichere Vorschläge bestätigen
          </button>
        )}
        {unsureCount > 0 && filter !== "unsure" && (
          <button className="text-link" onClick={() => setFilter("unsure")}>
            {unsureCount} unsichere(r) Vorschlag/Vorschläge – gezielt prüfen
          </button>
        )}
      </div>

      <div className="mark-scroll">
        {marks.length === 0 && (
          <p className="mark-empty">
            Noch keine Fundstellen. Mit dem Werkzeug <strong>Rechteck</strong> Bereiche aufziehen oder mit <strong>Text</strong>{" "}
            Wörter auswählen.
          </p>
        )}
        {marks.length > 0 && visible.length === 0 && minor.length === 0 && (
          <p className="mark-empty">Keine Fundstellen für diesen Filter.</p>
        )}
        {minor.length > 0 && (
          <div className="minor-box" data-testid="minor-box">
            <p className="minor-text">
              <strong>{minor.length} weitere Fundstelle{minor.length === 1 ? "" : "n"}</strong>, nicht zur Schwärzung
              vorgesehen: {minorParts.map((p) => `${p.label} ${p.count}`).join(", ")}
            </p>
            <div className="minor-actions">
              <button className="btn btn-small" onClick={() => setShowMinor(!showMinor)} aria-expanded={showMinor}>
                {showMinor ? "Ausblenden" : "Einblenden"}
              </button>
              <button
                className="btn btn-small"
                disabled={disabled}
                onClick={() => onStatus(minor, "rejected")}
                title="Alle diese Fundstellen als „Verworfen“ markieren. Sie bleiben im Filter „Verworfen“ sichtbar und lassen sich wieder bestätigen."
              >
                Alle {minor.length} verwerfen
              </button>
            </div>
          </div>
        )}

        {byPage.map(([page, items]) => (
          <section key={page} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h3 className={`mark-group-title ${page === currentPage ? "is-current" : ""}`}>Seite {page + 1}</h3>
            {items.map((m) => {
              const same = sameText(m);
              const known = CATEGORIES.includes(m.label);
              const editingLabel = customLabel?.id === m.id;
              const unsure = isUnsure(m);
              return (
                <article
                  key={m.id}
                  className={`mark-card status-${m.status} ${unsure ? "is-unsure" : ""} ${m.id === selectedId ? "is-selected" : ""} ${m.id === hoveredId ? "is-hovered" : ""}`}
                  onMouseEnter={() => onHover(m.id)}
                  onMouseLeave={() => onHover(null)}
                >
                  <button className="mark-card-main" onClick={() => onFocus(m)} title="Auf der Seite zeigen">
                    <span className="mark-card-head">
                      <span className={`pill status-${m.status}`} title={STATUS_HINT[m.status]}>
                        {STATUS_LABEL[m.status]}
                      </span>
                      <span className="mark-label">{m.label}</span>
                      <span className="origin-badge" title={ORIGIN_HINT[m.origin]}>
                        {ORIGIN_LABEL[m.origin]}
                      </span>
                    </span>
                    <span className="mark-value">{m.text || "Bereich"}</span>
                    <span className="mark-reason">
                      {unsure ? "Unsicher – bitte prüfen · " : ""}
                      {m.llm_reason || reasonText(m)}
                    </span>
                    {m.also_detected_as && m.also_detected_as.length > 0 && (
                      <span className="mark-reason">
                        auch erkannt als: {m.also_detected_as.map((a) => a.label).join(", ")}
                      </span>
                    )}
                  </button>

                  <div className="mark-row">
                    {editingLabel ? (
                      <form
                        className="mark-label-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const value = customLabel!.value.trim();
                          if (value) onLabel(m, value);
                          setCustomLabel(null);
                        }}
                      >
                        <input
                          autoFocus
                          className="mark-label-input"
                          value={customLabel!.value}
                          maxLength={80}
                          aria-label="Eigene Kategorie"
                          onChange={(e) => setCustomLabel({ id: m.id, value: e.target.value })}
                          onKeyDown={(e) => e.key === "Escape" && setCustomLabel(null)}
                        />
                        <button className="btn btn-small" type="submit">
                          OK
                        </button>
                      </form>
                    ) : (
                      <select
                        className="mark-select"
                        value={known ? m.label : "__custom"}
                        disabled={disabled}
                        aria-label="Kategorie"
                        onChange={(e) => {
                          if (e.target.value === "__other") setCustomLabel({ id: m.id, value: known ? "" : m.label });
                          else if (e.target.value !== "__custom") onLabel(m, e.target.value);
                        }}
                      >
                        {!known && <option value="__custom">{m.label}</option>}
                        {CATEGORIES.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                        <option value="__other">Sonstiges …</option>
                      </select>
                    )}
                    <div className="mark-actions">
                      {m.status !== "confirmed" && m.status !== "redacted" && (
                        <button className="btn btn-small" disabled={disabled} onClick={() => onStatus([m], "confirmed")}>
                          Bestätigen
                        </button>
                      )}
                      {m.status !== "rejected" && (
                        <button className="btn btn-small" disabled={disabled} onClick={() => onStatus([m], "rejected")}>
                          Verwerfen
                        </button>
                      )}
                      <button
                        className="icon-btn is-plain"
                        style={{ width: 32, height: 32 }}
                        disabled={disabled}
                        onClick={() => onDelete(m)}
                        title="Löschen (Entf)"
                        aria-label="Fundstelle löschen"
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  </div>

                  {same.length > 1 && m.status === "suggested" && (
                    <button
                      className="text-link"
                      disabled={disabled}
                      onClick={() => onStatus(same.filter((x) => x.status === "suggested"), "confirmed")}
                    >
                      Alle {same.length} gleichen Werte im Dokument bestätigen
                    </button>
                  )}
                </article>
              );
            })}
          </section>
        ))}
      </div>
    </>
  );
}
