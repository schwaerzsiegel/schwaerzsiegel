import { useEffect, useState } from "react";
import {
  setManualPort,
  waitForSidecar,
  uploadDocument,
  getProgress,
  getSidecarStatus,
  getBranding,
  getBrandingLogoUrl,
  preloadLlm,
  unloadLlm,
  cancelAnalysis,
  closeDocument,
  type AnalysisMode,
  type ModeAvailability,
  type Progress,
} from "./api/client";
import { DEFAULT_BRANDING, accentVariables } from "./lib/branding";
import { applyTheme, loadTheme, saveTheme, type ThemeChoice } from "./lib/theme";
import { BrandMark, InfoButton, PoweredBy, ThemeButton, type BrandAssets } from "./components/BrandBar";
import { InfoDialog } from "./components/InfoDialog";
import { ProcessingIndicator } from "./components/ProcessingIndicator";
import { StartScreen, type SystemStatus } from "./components/StartScreen";
import { Workspace } from "./components/Workspace";

function useDarkMode(theme: ThemeChoice): boolean {
  const query = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const [systemDark, setSystemDark] = useState(!!query?.matches);
  useEffect(() => {
    if (!query) return;
    const listener = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return theme === "dark" || (theme === "system" && systemDark);
}

export default function App() {
  const [port, setPort] = useState("");
  const [connected, setConnected] = useState(false);
  // v32: Verbindung zum Sidecar wird automatisch hergestellt. Die
  // Port-Eingabe erscheint nur noch, wenn das nicht klappt.
  const [showManualConnect, setShowManualConnect] = useState(false);
  const [sidecarError, setSidecarError] = useState<string | null>(null);

  const [documentId, setDocumentId] = useState<string | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [fileName, setFileName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  // v38: Standard "Schnell-KI"; die Wahl gilt für alle weiteren Dokumente
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("fast");
  const [modeAvailability, setModeAvailability] = useState<Partial<Record<AnalysisMode, ModeAvailability>>>({});
  // Phase 3: Dokument in Analyse (für "Abbrechen")
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Phase 4: Darstellung und Branding
  const [theme, setThemeState] = useState<ThemeChoice>(loadTheme);
  const dark = useDarkMode(theme);
  const [assets, setAssets] = useState<BrandAssets>({ branding: DEFAULT_BRANDING, logoUrl: null, logoDarkUrl: null });
  const [infoOpen, setInfoOpen] = useState(false);

  useEffect(() => applyTheme(theme), [theme]);

  function setTheme(next: ThemeChoice) {
    setThemeState(next);
    saveTheme(next);
  }

  function changeAnalysisMode(next: AnalysisMode) {
    if (analysisMode === "ai" && next !== "ai") unloadLlm(); // Speicher freigeben
    setAnalysisMode(next);
    if (next === "ai") preloadLlm();
  }

  async function cancelProcessing() {
    if (processingId) await cancelAnalysis(processingId).catch(() => undefined);
  }

  useEffect(() => {
    let cancelled = false;
    waitForSidecar(
      (lookup) => setSidecarError(lookup.error),
      () => cancelled,
    ).then((found) => {
      if (cancelled) return;
      if (found !== null) setConnected(true);
      else setShowManualConnect(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase 4: Branding laden (nur lokale Dateien, siehe sidecar/core/branding.py)
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;
    const urls: string[] = [];
    (async () => {
      try {
        const branding = await getBranding();
        const logoUrl = branding.logo ? await getBrandingLogoUrl("light") : null;
        const logoDarkUrl = branding.logoDark && branding.logoDark !== branding.logo ? await getBrandingLogoUrl("dark") : null;
        [logoUrl, logoDarkUrl].forEach((u) => u && urls.push(u));
        if (cancelled) return;
        setAssets({ branding, logoUrl, logoDarkUrl });
      } catch {
        // ohne Branding: Standarddesign
      }
    })();
    return () => {
      cancelled = true;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [connected]);

  useEffect(() => {
    const vars = accentVariables(assets.branding.accent);
    Object.entries(vars).forEach(([k, v]) => document.documentElement.style.setProperty(k, v));
    document.title = assets.branding.customerName
      ? `${assets.branding.productName} – ${assets.branding.customerName}`
      : assets.branding.productName;
  }, [assets]);

  function connect() {
    const parsed = parseInt(port, 10);
    if (!parsed) {
      setError("Bitte eine gültige Portnummer eingeben.");
      return;
    }
    setManualPort(parsed);
    setConnected(true);
    setError(null);
  }

  // v31/Phase 3: Modellstatus und verfügbare Modi laufend abfragen (alle 5 s,
  // beim Laden jede Sekunde).
  useEffect(() => {
    if (!connected) return;
    let stopped = false;
    async function poll() {
      let delay = 5000;
      try {
        const status = await getSidecarStatus();
        setModeAvailability(status.modes ?? {});
        const { gliner, llm } = status.models;
        const llmInfo = status.modes?.ai?.model;
        setSystemStatus({
          offlineGuard: !!status.offline_guard,
          glinerOk: gliner.state === "ready",
          glinerText:
            gliner.state === "ready"
              ? `Erkennung bereit (GLiNER, ${gliner.device})`
              : gliner.state === "error"
                ? `KI-Erkennung nicht verfügbar – Modus A und manuelles Markieren funktionieren`
                : "Erkennung wird geladen (einmalig nach dem Start) …",
          llmOk: !!status.modes?.ai?.available,
          llmText: status.modes?.ai?.available
            ? `Sprachmodell ${llm.state === "ready" ? "geladen" : "vorhanden"} (${llmInfo?.name ?? "lokal"})`
            : `Gründliche KI nicht verfügbar: ${status.modes?.ai?.problem ?? "unbekannt"}`,
          ocrOk: !!status.ocr,
        });
        if (!status.ready || llm.state === "pending" || llm.state === "loading") delay = 1000;
      } catch {
        setSystemStatus(null);
      }
      if (!stopped) setTimeout(poll, delay);
    }
    poll();
    return () => {
      stopped = true;
    };
  }, [connected]);

  async function waitForAnalysis(docId: string) {
    for (;;) {
      const p = await getProgress(docId);
      setProgress(p);
      if (p.state === "done") return true;
      if (p.state === "cancelled") return false;
      if (p.state === "error") throw new Error(p.error || "Verarbeitung fehlgeschlagen");
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  async function handleFile(file: File) {
    if (file.type && file.type !== "application/pdf") {
      setError("Bitte eine PDF-Datei wählen.");
      return;
    }
    // Phase 10: bisheriges Dokument samt Ergebnisdateien entfernen
    if (documentId) await closeDocument(documentId);
    setLoading(true);
    setError(null);
    setProgress(null);
    setDocumentId(null);
    setNotice(null);
    try {
      const data = await uploadDocument(file, analysisMode);
      setProcessingId(data.document_id);
      if (await waitForAnalysis(data.document_id)) {
        setPageCount(data.page_count);
        setFileName(file.name);
        setDocumentId(data.document_id);
      } else {
        setNotice("Analyse abgebrochen. Sie können ein Dokument erneut öffnen – z. B. in einem schnelleren Modus.");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setProcessingId(null);
      setLoading(false);
    }
  }

  const header = (
    <header className="app-header">
      <BrandMark assets={assets} dark={dark} />
      <div className="header-end">
        <PoweredBy assets={assets} />
        <ThemeButton theme={theme} onChange={setTheme} />
        {connected && <InfoButton onClick={() => setInfoOpen(true)} />}
      </div>
    </header>
  );

  if (!connected) {
    return (
      <div className="app-shell">
        {header}
        {!showManualConnect ? (
          <div className="connect-screen" role="status">
            <h2>Erkennungsdienst wird gestartet …</h2>
            <p className="muted">Das dauert beim ersten Öffnen einige Sekunden. Danach geht es automatisch weiter.</p>
          </div>
        ) : (
          <div className="connect-screen">
            <h2>Mit Sidecar verbinden</h2>
            {sidecarError ? (
              <div className="error-banner">{sidecarError}</div>
            ) : (
              <p className="muted">
                Der Dienst wurde nicht automatisch gestartet. Falls Sie ihn von Hand mit <code>python main.py</code> gestartet haben,
                tragen Sie hier den Port aus der Zeile „SIDECAR_PORT=…“ ein.
              </p>
            )}
            <label className="visually-hidden" htmlFor="port">
              Port
            </label>
            <input id="port" value={port} onChange={(e) => setPort(e.target.value)} placeholder="z. B. 51569" />
            <button className="btn btn-primary" onClick={connect}>
              Verbinden
            </button>
            {error && <div className="error-banner">{error}</div>}
          </div>
        )}
      </div>
    );
  }

  if (documentId && !loading) {
    return (
      <>
        <Workspace
          key={documentId}
          documentId={documentId}
          pageCount={pageCount}
          fileName={fileName}
          analysisMode={analysisMode}
          assets={assets}
          dark={dark}
          theme={theme}
          onTheme={setTheme}
          onInfo={() => setInfoOpen(true)}
          onOpenFile={handleFile}
          availability={modeAvailability}
        />
        {infoOpen && (
        <InfoDialog assets={assets} onClose={() => setInfoOpen(false)} />
      )}
      </>
    );
  }

  return (
    <div className="app-shell">
      {header}
      {(error || notice) && (
        <div style={{ maxWidth: 880, width: "100%", margin: "16px auto 0", padding: "0 24px" }}>
          {error && <div className="error-banner" role="alert">{error}</div>}
          {notice && <div className="notice-banner">{notice}</div>}
        </div>
      )}
      {loading ? (
        <ProcessingIndicator progress={progress} analysisMode={analysisMode} onCancel={processingId ? cancelProcessing : undefined} />
      ) : (
        <StartScreen
          mode={analysisMode}
          onModeChange={changeAnalysisMode}
          availability={modeAvailability}
          onFile={handleFile}
          disabled={loading}
          status={systemStatus}
        />
      )}
      {infoOpen && (
        <InfoDialog assets={assets} onClose={() => setInfoOpen(false)} />
      )}
    </div>
  );
}
