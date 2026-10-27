import { invoke } from "@tauri-apps/api/core";
import type { Rect } from "../lib/geometry";
import type { Branding } from "../lib/branding";

// Phase 1: einheitliches Markierungsmodell (siehe sidecar/core/marks.py).
// Koordinaten in Anzeige-Koordinaten (PDF-Punkte, Seitenrotation eingerechnet).
export type MarkStatus = "suggested" | "confirmed" | "rejected" | "redacted";
export type MarkOrigin = "manual" | "rule" | "ai";

export interface Mark {
  id: string;
  page: number;
  label: string;
  text: string;
  kind: "text" | "area";
  origin: MarkOrigin;
  status: MarkStatus;
  rects: Rect[];
  bbox: Rect;
  confidence: number;
  source: string;
  default_selected: boolean;
  edited: boolean;
  reason?: string | null;
  llm_reviewed?: boolean | null;
  llm_reason?: string | null;
  also_detected_as?: { label: string; source: string; confidence: number }[];
}

export interface PageWord {
  id: string;
  text: string;
  bbox: Rect;
  line: string | null;
}

export interface PageInfo {
  index: number;
  width: number;
  height: number;
  rotation: number;
  words: PageWord[];
  marks: Mark[];
}

export interface MarkListing {
  marks: Mark[];
  counts: Record<MarkStatus, number>;
  notices: string[];
  review_confirmed: boolean;
  result_current: boolean;
  analysis_mode: string;
}

export interface VerificationCheck {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface Verification {
  passed: boolean;
  problems: string[];
  warnings: string[];
  checks: VerificationCheck[];
  metadata_clean: boolean;
}

export interface FinalizeResult {
  output_path: string;
  mode: "redact" | "pseudonymize";
  mapping_table_path?: string;
  verification: Verification;
  actions: string[];
  warning?: string;
}

/** Fehler des Sidecars; bei gesperrter Schwärzung mit Prüfbericht. */
export class ApiError extends Error {
  status: number;
  verification: Verification | null;
  constructor(message: string, status: number, verification: Verification | null = null) {
    super(message);
    this.status = status;
    this.verification = verification;
  }
}

async function failure(res: Response, fallback: string): Promise<ApiError> {
  const data = await res.json().catch(() => ({}));
  const detail = data?.detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return new ApiError(detail.message, res.status, detail.verification ?? null);
  }
  return new ApiError(typeof detail === "string" ? detail : `${fallback}: ${res.status}`, res.status);
}

// v32: Der Port wird automatisch ermittelt - in der Tauri-App von
// main.rs (get_sidecar_port), im Browser-Dev-Modus (npm run dev) vom
// Vite-Dev-Server, der den Sidecar selbst startet (/__sidecar/status,
// siehe vite.config.ts). Die manuelle Port-Eingabe bleibt nur als Notlösung,
// falls der Sidecar von Hand gestartet wurde.
let cachedPort: number | null = null;
let sessionToken: string | null = null;

export function setManualPort(port: number) {
  cachedPort = port;
}

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export interface SidecarLookup {
  port: number | null;
  error: string | null;
  // false, wenn niemand den Sidecar automatisch startet (z.B. reiner
  // Vite-Build ohne Dev-Server) - dann bleibt nur die manuelle Eingabe.
  autostart: boolean;
}

export async function lookupSidecarPort(): Promise<SidecarLookup> {
  if (isTauri()) {
    const port = await invoke<number | null>("get_sidecar_port");
    sessionToken = await invoke<string>("get_sidecar_token");
    const error = port === null ? await invoke<string | null>("get_sidecar_error") : null;
    return { port, error, autostart: true };
  }
  try {
    const res = await fetch("/__sidecar/status", { cache: "no-store" });
    if (!res.ok || !(res.headers.get("content-type") || "").includes("application/json")) {
      return { port: null, error: null, autostart: false };
    }
    const data = (await res.json()) as { port: number | null; error: string | null; token: string };
    sessionToken = data.token;
    return { port: data.port, error: data.error, autostart: true };
  } catch {
    return { port: null, error: null, autostart: false };
  }
}

// Fragt so lange nach, bis der Sidecar seinen Port gemeldet hat (dauert
// beim Start einige Sekunden, weil Python erst seine Pakete lädt).
export async function waitForSidecar(
  onUpdate: (lookup: SidecarLookup) => void,
  isCancelled: () => boolean,
): Promise<number | null> {
  for (;;) {
    if (isCancelled()) return null;
    const lookup = await lookupSidecarPort();
    onUpdate(lookup);
    if (lookup.port !== null) {
      cachedPort = lookup.port;
      return lookup.port;
    }
    if (!lookup.autostart || lookup.error) return null;
    await new Promise((r) => setTimeout(r, 500));
  }
}

function authorized(init: RequestInit = {}): RequestInit {
  if (!sessionToken) throw new Error("Keine sichere Sidecar-Sitzung verfügbar.");
  return { ...init, headers: { ...init.headers, "X-Redaction-Session": sessionToken } };
}

async function securedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  return fetch(url, authorized(init));
}

async function securedBlobUrl(url: string): Promise<string> {
  const response = await securedFetch(url);
  if (!response.ok) throw new Error(`PDF-Vorschau fehlgeschlagen: ${response.status}`);
  return URL.createObjectURL(await response.blob());
}

async function baseUrl(): Promise<string> {
  if (cachedPort === null) {
    throw new Error("Keine Verbindung zum KI-Dienst (Sidecar).");
  }
  return `http://127.0.0.1:${cachedPort}`;
}

// Phase 3: drei Betriebsarten plus "manual" (Analyse überspringen):
// "rules" = A, ohne KI; "fast" = B, Schnell-KI (GLiNER, Standard);
// "ai" = C, Gründliche KI (zusätzlich lokales Sprachmodell).
export type AnalysisMode = "manual" | "rules" | "fast" | "ai";

export async function uploadDocument(file: File, analysisMode: AnalysisMode = "fast") {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", analysisMode);
  const res = await securedFetch(`${await baseUrl()}/documents`, { method: "POST", body: form });
  if (!res.ok) throw await failure(res, "Upload fehlgeschlagen");
  return res.json() as Promise<{ document_id: string; page_count: number }>;
}

export interface Progress {
  kind?: "reanalyze";
  state: "queued" | "processing" | "done" | "error" | "cancelled";
  page: number;
  page_count: number;
  step: string;
  error: string | null;
}

// Neu in v31: Die Analyse läuft im Sidecar im Hintergrund, der Upload
// kehrt sofort zurück. Der Fortschritt (Seite x von y + aktueller Schritt)
// wird hierüber abgefragt.
export async function getProgress(documentId: string) {
  const res = await securedFetch(`${await baseUrl()}/documents/${documentId}/progress`);
  if (!res.ok) throw new Error(`Fortschritt nicht abrufbar: ${res.status}`);
  return res.json() as Promise<Progress>;
}

export interface ModelStatus {
  state: "idle" | "pending" | "loading" | "ready" | "error";
  device: string | null;
  seconds: number | null;
  error: string | null;
}

export interface ModeAvailability {
  available: boolean;
  problem: string | null;
  loading?: boolean;
  model?: { name: string | null; size_gb: number | null; ram_needed_gb: number | null; ram_available_gb: number | null };
}

export interface SidecarStatus {
  ready: boolean;
  models: Record<string, ModelStatus>;
  modes: Record<AnalysisMode, ModeAvailability>;
  offline_guard: boolean;
  ocr: boolean;
}

export async function getSidecarStatus() {
  const res = await securedFetch(`${await baseUrl()}/status`);
  if (!res.ok) throw new Error(`Status nicht abrufbar: ${res.status}`);
  return res.json() as Promise<SidecarStatus>;
}

// v38: KI-Prüfmodell vorab laden, sobald "Gründliche KI" gewählt wird -
// bis die Datei ausgewählt ist, ist es dann oft schon fertig.
export async function preloadLlm() {
  await securedFetch(`${await baseUrl()}/models/llm/load`, { method: "POST" }).catch(() => undefined);
}

// Phase 3: Arbeitsspeicher des Sprachmodells wieder freigeben.
export async function unloadLlm() {
  await securedFetch(`${await baseUrl()}/models/llm/unload`, { method: "POST" }).catch(() => undefined);
}

// Phase 10: Dokument schließen - der Sidecar entfernt Arbeitsstand und
// Ergebnisdateien. Fehler (z. B. schon entfernt) sind unkritisch.
export async function closeDocument(documentId: string) {
  await securedFetch(`${await baseUrl()}/documents/${documentId}`, { method: "DELETE" }).catch(() => undefined);
}

// Phase 3: laufende Analyse abbrechen.
export async function cancelAnalysis(documentId: string) {
  await securedFetch(`${await baseUrl()}/documents/${documentId}/cancel`, { method: "POST" });
}

// Phase 4: erneut analysieren (manuelle und entschiedene Markierungen bleiben).
export async function reanalyzeDocument(documentId: string, mode: Exclude<AnalysisMode, "manual">): Promise<Progress> {
  const res = await sendJson(`/documents/${documentId}/reanalyze`, "POST", { mode });
  if (!res.ok) throw await failure(res, "Erneute Analyse nicht möglich");
  return res.json();
}

// Phase 4: Kunden-Branding
export async function getBranding(): Promise<Branding> {
  const res = await securedFetch(`${await baseUrl()}/branding`);
  if (!res.ok) throw await failure(res, "Branding nicht abrufbar");
  return res.json();
}

/** Logo als Blob-URL (nur als <img> verwenden) oder null, wenn keins hinterlegt ist. */
export async function getBrandingLogoUrl(variant: "light" | "dark"): Promise<string | null> {
  const res = await securedFetch(`${await baseUrl()}/branding/logo?variant=${variant}`);
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
}

export const PAGE_IMAGE_ZOOM = 1.5;

// Blob-URLs der Seitenbilder werden vom Aufrufer mit URL.revokeObjectURL
// freigegeben, sobald das Bild nicht mehr angezeigt wird.
export async function getPageImageUrl(documentId: string, page: number, zoom = PAGE_IMAGE_ZOOM) {
  return securedBlobUrl(`${await baseUrl()}/documents/${documentId}/pages/${page}/image?zoom=${zoom}`);
}

export async function getResultPageImageUrl(documentId: string, page: number, zoom = PAGE_IMAGE_ZOOM) {
  return securedBlobUrl(`${await baseUrl()}/documents/${documentId}/result/pages/${page}/image?zoom=${zoom}`);
}

export async function getPageInfo(documentId: string, page: number): Promise<PageInfo> {
  const res = await securedFetch(`${await baseUrl()}/documents/${documentId}/pages/${page}`);
  if (!res.ok) throw await failure(res, "Seite nicht abrufbar");
  return res.json();
}

export async function listMarks(documentId: string): Promise<MarkListing> {
  const res = await securedFetch(`${await baseUrl()}/documents/${documentId}/marks`);
  if (!res.ok) throw await failure(res, "Markierungen nicht abrufbar");
  return res.json();
}

export interface NewMark {
  page: number;
  kind: "area" | "text";
  rects?: Rect[];
  word_ids?: string[];
  label?: string;
}

async function sendJson(path: string, method: string, body?: unknown): Promise<Response> {
  return securedFetch(`${await baseUrl()}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function createMark(documentId: string, mark: NewMark): Promise<Mark> {
  const res = await sendJson(`/documents/${documentId}/marks`, "POST", mark);
  if (!res.ok) throw await failure(res, "Markierung konnte nicht angelegt werden");
  return res.json();
}

export interface MarkPatch {
  rects?: Rect[];
  label?: string;
  status?: Exclude<MarkStatus, "redacted">;
}

export async function updateMark(documentId: string, markId: string, patch: MarkPatch): Promise<Mark> {
  const res = await sendJson(`/documents/${documentId}/marks/${markId}`, "PATCH", patch);
  if (!res.ok) throw await failure(res, "Markierung konnte nicht geändert werden");
  return res.json();
}

export async function deleteMark(documentId: string, markId: string): Promise<void> {
  const res = await sendJson(`/documents/${documentId}/marks/${markId}`, "DELETE");
  if (!res.ok) throw await failure(res, "Markierung konnte nicht gelöscht werden");
}

export async function restoreMark(documentId: string, markId: string): Promise<Mark> {
  const res = await sendJson(`/documents/${documentId}/marks/${markId}/restore`, "POST");
  if (!res.ok) throw await failure(res, "Markierung konnte nicht wiederhergestellt werden");
  return res.json();
}

export async function verifyResult(documentId: string): Promise<Verification> {
  const res = await sendJson(`/documents/${documentId}/result/verify`, "POST");
  if (!res.ok) throw await failure(res, "Prüfung fehlgeschlagen");
  return (await res.json()).verification;
}

export async function getResultDownloadUrl(documentId: string) {
  return `${await baseUrl()}/documents/${documentId}/result/download`;
}

export async function getMappingDownloadUrl(documentId: string) {
  return `${await baseUrl()}/documents/${documentId}/result/mapping-download`;
}

// v37: Herunterladen. In der Desktop-App speichert der Sidecar die Datei
// selbst im Downloads-Ordner (POST .../result/save) und gibt den Pfad
// zurück - Download-Links und die Übergabe an das App-Fenster (v35/v36)
// haben unter Windows nicht funktioniert. Im Browser-Dev-Modus wird
// normal über einen Blob-Link heruntergeladen (Rückgabe null).
export async function saveDownload(
  documentId: string,
  kind: "pdf" | "mapping",
  url: string,
  filename: string,
): Promise<string | null> {
  if (isTauri()) {
    const res = await securedFetch(`${await baseUrl()}/documents/${documentId}/result/save?kind=${kind}`, {
      method: "POST",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Speichern fehlgeschlagen: ${res.status}`);
    return data.path as string;
  }
  const res = await securedFetch(url);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Download fehlgeschlagen: ${res.status}`);
  }
  const objectUrl = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
  return null;
}

export async function confirmReview(documentId: string) {
  const res = await securedFetch(`${await baseUrl()}/documents/${documentId}/review/confirm`, {
    method: "POST",
  });
  if (!res.ok) throw await failure(res, "Freigabe fehlgeschlagen");
}

/** Schwärzt (oder pseudonymisiert) und prüft das Ergebnis. Schlägt die
 *  Prüfung fehl, kommt ein ApiError mit Prüfbericht - das Ergebnis ist
 *  dann gesperrt. */
export async function finalizeDocument(documentId: string, mode: "redact" | "pseudonymize"): Promise<FinalizeResult> {
  const res = await sendJson(`/documents/${documentId}/finalize`, "POST", { mode });
  if (!res.ok) throw await failure(res, "Schwärzen fehlgeschlagen");
  return res.json();
}
