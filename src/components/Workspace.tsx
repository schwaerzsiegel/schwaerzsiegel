import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  cancelAnalysis,
  confirmReview,
  createMark,
  deleteMark,
  finalizeDocument,
  getMappingDownloadUrl,
  getPageImageUrl,
  getPageInfo,
  getProgress,
  getResultDownloadUrl,
  getResultPageImageUrl,
  listMarks,
  reanalyzeDocument,
  restoreMark,
  saveDownload,
  updateMark,
  verifyResult,
  type AnalysisMode,
  type FinalizeResult,
  type Mark,
  type MarkListing,
  type MarkStatus,
  type ModeAvailability,
  type PageInfo,
  type Progress,
  type Verification,
} from "../api/client";
import { nextZoom, renderZoomFor, type Rect } from "../lib/geometry";
import { History, combine, type Command, type HistoryState } from "../lib/history";
import { deriveSteps } from "../lib/workflow";
import type { ThemeChoice } from "../lib/theme";
import { BrandMark, InfoButton, ThemeButton, type BrandAssets } from "./BrandBar";
import { ApplyDialog, ReanalyzeDialog, UnsavedMappingDialog, type FinalMode } from "./Dialogs";
import { Icon } from "./Icon";
import { MarkList } from "./MarkList";
import { modeName, type MainMode } from "./ModeOptions";
import { PageCanvas, type Tool } from "./PageCanvas";
import { PageSidebar } from "./PageSidebar";
import { ProcessingIndicator } from "./ProcessingIndicator";
import { ResultCompare } from "./ResultCompare";
import { VerificationReport } from "./VerificationReport";

interface Props {
  documentId: string;
  pageCount: number;
  fileName: string;
  analysisMode: AnalysisMode;
  assets: BrandAssets;
  dark: boolean;
  theme: ThemeChoice;
  onTheme: (t: ThemeChoice) => void;
  onInfo: () => void;
  onOpenFile: (file: File) => void;
  availability: Partial<Record<AnalysisMode, ModeAvailability>>;
}

const TOOLS: { key: Tool; label: string; shortcut: string; hint: string; icon: "select" | "rect" | "text" }[] = [
  { key: "select", label: "Auswählen", shortcut: "V", hint: "Markierungen auswählen, verschieben und in der Größe ändern", icon: "select" },
  { key: "rect", label: "Rechteck", shortcut: "R", hint: "Beliebigen Bereich aufziehen – auch Bilder, Unterschriften, Handschrift", icon: "rect" },
  { key: "text", label: "Text", shortcut: "T", hint: "Wörter auswählen und zur Schwärzung hinzufügen (Enter)", icon: "text" },
];

// Bild-URLs freigeben, sobald sie nicht mehr gebraucht werden (Speicher).
function useBlobUrl(): [string | null, (url: string | null) => void] {
  const [url, setUrl] = useState<string | null>(null);
  const current = useRef<string | null>(null);
  const update = useCallback((next: string | null) => {
    if (current.current && current.current !== next) URL.revokeObjectURL(current.current);
    current.current = next;
    setUrl(next);
  }, []);
  useEffect(() => () => {
    if (current.current) URL.revokeObjectURL(current.current);
  }, []);
  return [url, update];
}

// Phase 1: Arbeitsbereich für ein geladenes Dokument. Phase 4: Aufbau laut
// Designvorschlag - Werkzeugleiste, Arbeitsschritte, links Seiten und
// Analyse, Mitte Seite, rechts Fundstellen und Sicherheitsstatus.
export function Workspace(props: Props) {
  const { documentId, pageCount, assets } = props;
  const [page, setPage] = useState(0);
  const [scale, setScale] = useState(1.25);
  const [tool, setTool] = useState<Tool>("select");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [listing, setListing] = useState<MarkListing | null>(null);
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null);
  const [imageUrl, setImageUrl] = useBlobUrl();
  const [resultImageUrl, setResultImageUrl] = useBlobUrl();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [result, setResult] = useState<FinalizeResult | null>(null);
  const [view, setView] = useState<"edit" | "result">("edit");
  const [locked, setLocked] = useState<Verification | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  // Phase 10: Zuordnungstabelle des aktuellen Ergebnisses gespeichert?
  const [mappingSaved, setMappingSaved] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [applyOpen, setApplyOpen] = useState(false);
  const [reanalyzeOpen, setReanalyzeOpen] = useState(false);
  const [reanalysis, setReanalysis] = useState<{ mode: MainMode; progress: Progress | null } | null>(null);
  const [historyState, setHistoryState] = useState<HistoryState>({ canUndo: false, canRedo: false, undoLabel: null, redoLabel: null });
  const historyRef = useRef<History>();
  if (!historyRef.current) historyRef.current = new History(200, () => setHistoryState(historyRef.current!.state()));
  const history = historyRef.current;
  const scrollRef = useRef<HTMLDivElement>(null);
  const fitted = useRef(false);

  // Seite in die verfügbare Breite einpassen (beim Öffnen und auf Wunsch)
  const fitWidth = useCallback(() => {
    const box = scrollRef.current;
    if (!box || !pageInfo) return;
    const available = box.clientWidth - 48;
    setScale(Math.min(3, Math.max(0.5, Math.floor((available / pageInfo.width) * 20) / 20)));
  }, [pageInfo]);

  useEffect(() => {
    if (pageInfo && !fitted.current) {
      fitted.current = true;
      fitWidth();
    }
  }, [pageInfo, fitWidth]);

  const refresh = useCallback(async () => {
    const data = await listMarks(documentId);
    setListing(data);
    if (!data.result_current) setResult(null);
    return data;
  }, [documentId]);

  // Fehler sichtbar machen, Arbeitsstand danach immer neu laden.
  const run = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      try {
        await action();
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        await refresh().catch(() => undefined);
        setBusy(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    history.clear();
    refresh().catch((e) => setError(e.message));
  }, [documentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seite (Größe, Wörter) laden
  useEffect(() => {
    let cancelled = false;
    getPageInfo(documentId, page)
      .then((p) => !cancelled && setPageInfo(p))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [documentId, page]);

  // Seitenbild in passender Auflösung für Zoom und Bildschirm
  const renderZoom = renderZoomFor(scale, window.devicePixelRatio || 1);
  useEffect(() => {
    let cancelled = false;
    getPageImageUrl(documentId, page, renderZoom)
      .then((url) => (cancelled ? URL.revokeObjectURL(url) : setImageUrl(url)))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [documentId, page, renderZoom, setImageUrl]);

  const resultCurrent = !!listing?.result_current && !!result;
  const showingResult = view === "result" && (resultCurrent || !!locked);
  useEffect(() => {
    if (!resultCurrent || view !== "result") {
      setResultImageUrl(null);
      return;
    }
    let cancelled = false;
    getResultPageImageUrl(documentId, page, renderZoom)
      .then((url) => (cancelled ? URL.revokeObjectURL(url) : setResultImageUrl(url)))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [documentId, page, renderZoom, resultCurrent, view, setResultImageUrl]);

  const marks = listing?.marks ?? [];
  const pageMarks = useMemo(() => marks.filter((m) => m.page === page), [marks, page]);
  const markById = (id: string) => marks.find((m) => m.id === id);

  // --- Änderungen (jede mit Rückgängig-Schritt) -----------------------------

  function afterChange() {
    setLocked(null);
    setSavedNote(null);
    setView("edit");
  }

  const addArea = (rect: Rect) =>
    run(async () => {
      const m = await createMark(documentId, { page, kind: "area", rects: [rect] });
      history.push({ label: "Bereich markiert", undo: () => deleteMark(documentId, m.id), redo: async () => void (await restoreMark(documentId, m.id)) });
      setSelectedId(m.id);
      afterChange();
    });

  const addText = (wordIds: string[]) =>
    run(async () => {
      const m = await createMark(documentId, { page, kind: "text", word_ids: wordIds });
      history.push({ label: "Text markiert", undo: () => deleteMark(documentId, m.id), redo: async () => void (await restoreMark(documentId, m.id)) });
      setSelectedId(m.id);
      afterChange();
    });

  const changeRects = (id: string, rects: Rect[], previous: Rect[]) =>
    run(async () => {
      await updateMark(documentId, id, { rects });
      history.push({
        label: "Markierung verändert",
        undo: async () => void (await updateMark(documentId, id, { rects: previous })),
        redo: async () => void (await updateMark(documentId, id, { rects })),
      });
      afterChange();
    });

  const setStatus = (targets: Mark[], status: Exclude<MarkStatus, "redacted">) =>
    run(async () => {
      const commands: Command[] = [];
      for (const m of targets) {
        const before = m.status === "redacted" ? "confirmed" : m.status;
        if (before === status) continue;
        await updateMark(documentId, m.id, { status });
        commands.push({
          label: "Status",
          undo: async () => void (await updateMark(documentId, m.id, { status: before })),
          redo: async () => void (await updateMark(documentId, m.id, { status })),
        });
      }
      if (commands.length) {
        const verb = status === "confirmed" ? "bestätigt" : status === "rejected" ? "verworfen" : "zurückgesetzt";
        history.push(combine(commands.length > 1 ? `${commands.length} Fundstellen ${verb}` : `Fundstelle ${verb}`, commands));
        afterChange();
      }
    });

  const setLabel = (m: Mark, label: string) =>
    run(async () => {
      const before = m.label;
      await updateMark(documentId, m.id, { label });
      history.push({
        label: "Kategorie geändert",
        undo: async () => void (await updateMark(documentId, m.id, { label: before })),
        redo: async () => void (await updateMark(documentId, m.id, { label })),
      });
      afterChange();
    });

  const remove = (m: Mark) =>
    run(async () => {
      await deleteMark(documentId, m.id);
      history.push({ label: "Markierung gelöscht", undo: async () => void (await restoreMark(documentId, m.id)), redo: () => deleteMark(documentId, m.id) });
      if (selectedId === m.id) setSelectedId(null);
      afterChange();
    });

  const undo = () => run(async () => void ((await history.undo()) && afterChange()));
  const redo = () => run(async () => void ((await history.redo()) && afterChange()));

  function goToPage(next: number) {
    if (next < 0 || next >= pageCount) return;
    setPage(next);
    setSelectedId(null);
  }

  function focusMark(m: Mark) {
    if (m.page !== page) setPage(m.page);
    setSelectedId(m.id);
    setTool("select");
    setView("edit");
  }

  // --- Schwärzen, Prüfen, Export -------------------------------------------

  const applyRedaction = (mode: FinalMode) =>
    run(async () => {
      setLocked(null);
      setSavedNote(null);
      try {
        await confirmReview(documentId); // ausdrückliche Freigabe der aktuellen Auswahl
        const data = await finalizeDocument(documentId, mode);
        setMappingSaved(false);
        setResult(data);
        setView("result");
      } catch (e) {
        if (e instanceof ApiError && e.verification) {
          setLocked(e.verification);
          setView("result");
        }
        throw e;
      } finally {
        setApplyOpen(false);
      }
    });

  const reverify = () =>
    run(async () => {
      try {
        const verification = await verifyResult(documentId);
        setResult((r) => (r ? { ...r, verification } : r));
      } catch (e) {
        if (e instanceof ApiError && e.verification) setLocked(e.verification);
        throw e;
      }
    });

  async function download(kind: "pdf" | "mapping") {
    setError(null);
    setSavedNote(null);
    setBusy(true);
    try {
      const url = kind === "pdf" ? await getResultDownloadUrl(documentId) : await getMappingDownloadUrl(documentId);
      const name = kind === "mapping" ? "pseudonym-schluessel.json" : result?.mode === "pseudonymize" ? "pseudonymisiert.pdf" : "geschwaerzt.pdf";
      const path = await saveDownload(documentId, kind, url, name);
      if (kind === "mapping") setMappingSaved(true);
      setSavedNote(path ? `Gespeichert unter ${path}` : "Download gestartet");
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  // --- Erneut analysieren ---------------------------------------------------

  async function startReanalysis(mode: MainMode) {
    setReanalyzeOpen(false);
    setError(null);
    setInfo(null);
    try {
      let progress = await reanalyzeDocument(documentId, mode);
      setReanalysis({ mode, progress });
      while (progress.state === "queued" || progress.state === "processing") {
        await new Promise((r) => setTimeout(r, 400));
        progress = await getProgress(documentId);
        setReanalysis({ mode, progress });
      }
      if (progress.step !== "Fertig") setInfo(progress.step);
      else history.clear(); // Vorschläge wurden ersetzt: alte Schritte passen nicht mehr
      afterChange();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setReanalysis(null);
      await refresh().catch(() => undefined);
    }
  }

  // --- Tastatur -------------------------------------------------------------

  const counts = listing?.counts ?? { suggested: 0, confirmed: 0, rejected: 0, redacted: 0 };
  const toRedact = counts.confirmed + counts.redacted;
  const editingLocked = busy || showingResult || !!reanalysis;

  const keyHandler = useRef<(e: KeyboardEvent) => void>(() => {});
  keyHandler.current = (e: KeyboardEvent) => {
    if (applyOpen || reanalyzeOpen || pendingFile) return;
    const target = e.target as HTMLElement | null;
    if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (ctrl && key === "z" && !e.shiftKey) {
      e.preventDefault();
      if (!editingLocked && historyState.canUndo) undo();
    } else if (ctrl && (key === "y" || (key === "z" && e.shiftKey))) {
      e.preventDefault();
      if (!editingLocked && historyState.canRedo) redo();
    } else if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
      const m = markById(selectedId);
      if (m && !editingLocked) {
        e.preventDefault();
        remove(m);
      }
    } else if (e.key === "Escape") {
      setSelectedId(null);
    } else if (!ctrl && !e.altKey) {
      const t = TOOLS.find((x) => x.shortcut.toLowerCase() === key);
      if (t && !showingResult) setTool(t.key);
      else if (e.key === "PageDown") goToPage(page + 1);
      else if (e.key === "PageUp") goToPage(page - 1);
    }
  };
  useEffect(() => {
    const listener = (e: KeyboardEvent) => keyHandler.current(e);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  // --- Ansicht --------------------------------------------------------------

  const steps = deriveSteps({
    analysisDone: !!listing,
    openSuggestions: counts.suggested,
    toRedact,
    resultCurrent,
    verificationFailed: !!locked && !resultCurrent,
    exported: !!savedNote,
  });
  const exportReady = resultCurrent && !!result?.verification.passed;
  const mappingAtRisk = exportReady && result?.mode === "pseudonymize" && !mappingSaved;

  function openOtherFile(file: File) {
    if (mappingAtRisk) setPendingFile(file);
    else props.onOpenFile(file);
  }

  return (
    <div className="ws">
      <header className="app-header">
        <BrandMark assets={assets} dark={props.dark} compact />
        <div className="ws-docinfo">
          <span className="ws-docname" title={props.fileName}>
            {props.fileName}
          </span>
          <span className="ws-docmeta">
            {pageCount} {pageCount === 1 ? "Seite" : "Seiten"} · {modeName(listing?.analysis_mode ?? props.analysisMode)} · nur lokal
          </span>
        </div>

        <div className="tool-group" role="radiogroup" aria-label="Werkzeug">
          {TOOLS.map((t) => (
            <button
              key={t.key}
              role="radio"
              aria-checked={tool === t.key}
              className={`tool-btn ${tool === t.key ? "is-active" : ""}`}
              title={`${t.hint} (${t.shortcut})`}
              disabled={showingResult}
              onClick={() => setTool(t.key)}
            >
              <Icon name={t.icon} size={16} />
              {t.label}
              <kbd>{t.shortcut}</kbd>
            </button>
          ))}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <button
            className="icon-btn"
            disabled={editingLocked || !historyState.canUndo}
            onClick={undo}
            aria-label={historyState.undoLabel ? `Rückgängig: ${historyState.undoLabel} (Strg+Z)` : "Rückgängig (Strg+Z)"}
            title={historyState.undoLabel ? `Rückgängig: ${historyState.undoLabel} (Strg+Z)` : "Rückgängig (Strg+Z)"}
          >
            <Icon name="undo" />
          </button>
          <button
            className="icon-btn"
            disabled={editingLocked || !historyState.canRedo}
            onClick={redo}
            aria-label={historyState.redoLabel ? `Wiederholen: ${historyState.redoLabel} (Strg+Y)` : "Wiederholen (Strg+Y)"}
            title={historyState.redoLabel ? `Wiederholen: ${historyState.redoLabel} (Strg+Y)` : "Wiederholen (Strg+Y)"}
          >
            <Icon name="redo" />
          </button>
          <div className="zoom-group" style={{ marginLeft: 8 }}>
            <button onClick={() => setScale((s) => nextZoom(s, -1))} disabled={scale <= 0.5} aria-label="Verkleinern">
              −
            </button>
            <button className="zoom-value" onClick={fitWidth} title="Auf Breite einpassen" aria-label={`Zoom ${Math.round(scale * 100)} % – auf Breite einpassen`} style={{ width: "auto" }}>
              {Math.round(scale * 100)} %
            </button>
            <button onClick={() => setScale((s) => nextZoom(s, 1))} disabled={scale >= 3} aria-label="Vergrößern">
              +
            </button>
          </div>
        </div>

        <div className="header-end">
          <label className="btn" title="Anderes Dokument öffnen">
            Dokument öffnen
            <input
              className="visually-hidden"
              type="file"
              accept="application/pdf"
              disabled={busy || !!reanalysis}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) openOtherFile(file);
              }}
            />
          </label>
          {showingResult ? (
            <button className="btn" disabled={busy} onClick={() => setView("edit")}>
              Zurück zur Bearbeitung
            </button>
          ) : (
            <button className="btn btn-primary" disabled={busy || !!reanalysis || toRedact === 0} onClick={() => setApplyOpen(true)}>
              Schwärzung anwenden …
            </button>
          )}
          {exportReady ? (
            <button className="btn btn-primary" disabled={busy} onClick={() => (view === "result" ? download("pdf") : setView("result"))}>
              <Icon name="save" size={16} />
              Exportieren
            </button>
          ) : (
            <button className="btn btn-locked" disabled aria-label="Exportieren – gesperrt, bis ein geprüftes Ergebnis vorliegt" title="Gesperrt, bis ein geprüftes Ergebnis vorliegt">
              <Icon name="lock" size={16} />
              Exportieren
            </button>
          )}
          <ThemeButton theme={props.theme} onChange={props.onTheme} />
          <InfoButton onClick={props.onInfo} />
        </div>
      </header>

      <ol className="steps" aria-label="Arbeitsschritte">
        {steps.map((s, i) => (
          <li key={s.key} className={`step ${s.state}`} aria-current={s.state === "active" ? "step" : undefined}>
            <span className="step-num">{s.state === "done" ? "✓" : s.state === "failed" ? "✕" : i + 1}</span>
            {s.label}
            {s.detail && <span className="step-detail">· {s.detail}</span>}
          </li>
        ))}
      </ol>

      {(error || info || (listing && listing.notices.length > 0)) && (
        <div className="ws-messages">
          {error && <div className="error-banner" role="alert">{error}</div>}
          {info && <div className="notice-banner">{info}</div>}
          {listing && listing.notices.length > 0 && (
            <div className="notice-banner">
              {listing.notices.map((n, i) => (
                <p key={i}>{n}</p>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="ws-body">
        <PageSidebar
          documentId={documentId}
          pageCount={pageCount}
          page={page}
          marks={marks}
          analysisMode={listing?.analysis_mode ?? props.analysisMode}
          disabled={editingLocked}
          onPage={goToPage}
          onReanalyze={() => setReanalyzeOpen(true)}
        />

        <main className="ws-center" aria-label="Dokumentseite">
          {reanalysis ? (
            <div className="result-center">
              <ProcessingIndicator
                progress={reanalysis.progress}
                analysisMode={reanalysis.mode}
                title="Dokument wird erneut analysiert"
                onCancel={() => cancelAnalysis(documentId).catch(() => undefined)}
              />
            </div>
          ) : showingResult && resultCurrent && imageUrl && resultImageUrl && pageInfo ? (
            <div className="result-center">
              <ResultCompare
                beforeUrl={imageUrl}
                afterUrl={resultImageUrl}
                afterLabel={result?.mode === "pseudonymize" ? "Pseudonymisiert" : "Geschwärzt"}
                width={pageInfo.width * scale}
              />
              <p className="compare-hint">Schieberegler ziehen, um Original und Ergebnis zu vergleichen</p>
            </div>
          ) : (
            <>
              <div className="canvas-scroll" ref={scrollRef}>
                {pageInfo && (
                  <PageCanvas
                    imageUrl={imageUrl}
                    page={pageInfo}
                    marks={pageMarks}
                    scale={scale}
                    tool={tool}
                    selectedId={selectedId}
                    hoveredId={hoveredId}
                    readOnly={editingLocked}
                    onSelect={setSelectedId}
                    onHover={setHoveredId}
                    onCreateArea={addArea}
                    onCreateText={addText}
                    onChangeRects={changeRects}
                    onStatus={(m, s) => setStatus([m], s)}
                    onLabel={setLabel}
                    onDelete={remove}
                  />
                )}
              </div>
              <div className="canvas-legend" aria-label="Legende">
                <span>
                  <span className="legend-swatch confirmed" />
                  wird geschwärzt
                </span>
                <span>
                  <span className="legend-swatch suggested" />
                  Vorschlag, offen
                </span>
                <span>
                  <span className="legend-swatch rejected" />
                  verworfen, bleibt sichtbar
                </span>
              </div>
            </>
          )}
          {!reanalysis && (
            <div className="canvas-pager">
              <button disabled={page <= 0} onClick={() => goToPage(page - 1)} aria-label="Vorherige Seite">
                <Icon name="chevronLeft" />
              </button>
              <span className="mono" style={{ padding: "0 6px" }}>
                {page + 1} / {pageCount}
              </span>
              <button disabled={page >= pageCount - 1} onClick={() => goToPage(page + 1)} aria-label="Nächste Seite">
                <Icon name="chevronRight" />
              </button>
            </div>
          )}
        </main>

        <aside className="ws-right" aria-label={showingResult ? "Prüfbericht" : "Fundstellen und Sicherheitsstatus"}>
          {showingResult ? (
            <div className="report">
              {locked && !resultCurrent ? (
                <VerificationReport verification={locked} onJumpToPage={(p) => { setView("edit"); goToPage(p); }} />
              ) : (
                result && <VerificationReport verification={result.verification} actions={result.actions} />
              )}
              {result?.warning && <div className="hint-box">{result.warning}</div>}
              <div className="report-actions">
                {exportReady && (
                  <>
                    <button className="btn btn-primary btn-block" disabled={busy} onClick={() => download("pdf")}>
                      <Icon name="save" size={16} />
                      {result?.mode === "pseudonymize" ? "Pseudonymisiertes PDF speichern …" : "Geschwärztes PDF speichern …"}
                    </button>
                    {result?.mode === "pseudonymize" && (
                      <button className="btn btn-block" disabled={busy} onClick={() => download("mapping")} title="Enthält Klartext-Originaldaten – getrennt vom PDF aufbewahren">
                        Zuordnungstabelle speichern …
                      </button>
                    )}
                  </>
                )}
                {savedNote && <span className="saved-note">{savedNote}</span>}
                {resultCurrent && (
                  <button className="btn btn-block" disabled={busy} onClick={reverify}>
                    <Icon name="refresh" size={16} />
                    Verifizierung erneut starten
                  </button>
                )}
                <button className="btn btn-block" onClick={() => setView("edit")}>
                  Zurück zur Bearbeitung
                </button>
              </div>
            </div>
          ) : (
            <>
              <MarkList
                marks={marks}
                currentPage={page}
                selectedId={selectedId}
                hoveredId={hoveredId}
                disabled={editingLocked}
                onFocus={focusMark}
                onHover={setHoveredId}
                onStatus={setStatus}
                onLabel={setLabel}
                onDelete={remove}
              />
              <section className="safety" aria-label="Sicherheitsstatus">
                {resultCurrent ? (
                  <>
                    <div className="safety-head">
                      <span className="safety-dot ok" />
                      <strong>Geschwärzt und geprüft</strong>
                    </div>
                    <p>Das aktuelle Ergebnis ist geprüft. Jede Änderung an den Markierungen macht es ungültig.</p>
                    <button className="btn btn-primary" onClick={() => setView("result")}>
                      Ergebnis und Prüfbericht ansehen
                    </button>
                  </>
                ) : locked ? (
                  <>
                    <div className="safety-head">
                      <span className="safety-dot danger" />
                      <strong>Prüfung nicht bestanden</strong>
                    </div>
                    <p>Export gesperrt. Markierungen anpassen und erneut schwärzen.</p>
                    <button className="btn" onClick={() => setView("result")}>
                      Gründe ansehen
                    </button>
                  </>
                ) : (
                  <>
                    <div className="safety-head">
                      <span className="safety-dot warn" />
                      <strong>Noch nicht geschwärzt</strong>
                    </div>
                    <p>
                      {toRedact === 1 ? "1 Stelle wird" : `${toRedact} Stellen werden`} geschwärzt.
                      {counts.suggested > 0 && (
                        <>
                          {" "}
                          <strong>
                            {counts.suggested} offene{counts.suggested === 1 ? "r Vorschlag wird" : " Vorschläge werden"}
                          </strong>{" "}
                          nicht geschwärzt, solange Sie {counts.suggested === 1 ? "ihn" : "sie"} nicht bestätigen.
                        </>
                      )}
                    </p>
                    <button className="btn btn-primary" disabled={busy || !!reanalysis || toRedact === 0} onClick={() => setApplyOpen(true)}>
                      Auswahl freigeben und schwärzen
                    </button>
                  </>
                )}
              </section>
            </>
          )}
        </aside>
      </div>

      {applyOpen && (
        <ApplyDialog
          toRedact={toRedact}
          open={counts.suggested}
          rejected={counts.rejected}
          busy={busy}
          onCancel={() => setApplyOpen(false)}
          onConfirm={applyRedaction}
        />
      )}
      {pendingFile && (
        <UnsavedMappingDialog
          onCancel={() => setPendingFile(null)}
          onConfirm={() => {
            const file = pendingFile;
            setPendingFile(null);
            props.onOpenFile(file);
          }}
        />
      )}
      {reanalyzeOpen && (
        <ReanalyzeDialog
          current={listing?.analysis_mode ?? props.analysisMode}
          availability={props.availability}
          onCancel={() => setReanalyzeOpen(false)}
          onStart={startReanalysis}
        />
      )}
    </div>
  );
}
