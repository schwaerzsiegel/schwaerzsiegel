import { useEffect, useRef, useState } from "react";
import { getPageImageUrl, type Mark } from "../api/client";
import { modeName, MODES } from "./ModeOptions";

// Phase 4: linke Spalte - Seitenminiaturen (werden erst geladen, wenn sie
// sichtbar werden) und Analyse-Einstellung.

function Thumbnail({ documentId, page }: { documentId: string; page: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let created: string | null = null;
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        observer.disconnect();
        getPageImageUrl(documentId, page, 0.25)
          .then((u) => {
            if (cancelled) URL.revokeObjectURL(u);
            else {
              created = u;
              setUrl(u);
            }
          })
          .catch(() => undefined);
      }
    });
    observer.observe(el);
    return () => {
      cancelled = true;
      observer.disconnect();
      if (created) URL.revokeObjectURL(created);
    };
  }, [documentId, page]);

  return (
    <span ref={ref}>
      {url ? <img className="thumb-img" src={url} alt="" /> : <span className="thumb-placeholder" style={{ display: "block" }} />}
    </span>
  );
}

export function PageSidebar({
  documentId,
  pageCount,
  page,
  marks,
  analysisMode,
  disabled,
  onPage,
  onReanalyze,
}: {
  documentId: string;
  pageCount: number;
  page: number;
  marks: Mark[];
  analysisMode: string;
  disabled?: boolean;
  onPage: (page: number) => void;
  onReanalyze: () => void;
}) {
  const pages = Array.from({ length: pageCount }, (_, i) => i);
  const counts = (i: number) => {
    const onPage = marks.filter((m) => m.page === i);
    return { total: onPage.length, open: onPage.filter((m) => m.status === "suggested").length };
  };

  return (
    <aside className="ws-left" aria-label="Seiten und Analyse">
      <section className="side-section">
        <h2 className="side-title">Seiten</h2>
        {pages.map((i) => {
          const c = counts(i);
          return (
            <button
              key={i}
              className={`thumb ${i === page ? "is-current" : ""}`}
              aria-current={i === page ? "page" : undefined}
              onClick={() => onPage(i)}
            >
              <Thumbnail documentId={documentId} page={i} />
              <span className="thumb-meta">
                <strong>Seite {i + 1}</strong>
                <span>{c.total === 1 ? "1 Fundstelle" : `${c.total} Fundstellen`}</span>
                {c.open > 0 && <span className="thumb-open">{c.open} offen</span>}
              </span>
            </button>
          );
        })}
      </section>

      <section className="side-section">
        <h2 className="side-title">Analyse</h2>
        <div className="mode-list">
          {MODES.map((m) => (
            <span key={m.key} className={`mode-list-item ${analysisMode === m.key ? "is-active" : ""}`} title={`${m.speed}, ${m.ram} Arbeitsspeicher`}>
              <span>
                {m.letter} · {m.name}
              </span>
              {analysisMode === m.key && <span>aktiv</span>}
            </span>
          ))}
          {analysisMode === "manual" && <span className="mode-list-item is-active">{modeName("manual")}</span>}
        </div>
        <button className="btn btn-small" disabled={disabled} onClick={onReanalyze}>
          Erneut analysieren …
        </button>
        <p className="side-hint">Manuelle Markierungen bleiben beim erneuten Analysieren erhalten.</p>
      </section>
    </aside>
  );
}
