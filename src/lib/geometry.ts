// Phase 1: Geometrie der Seitenansicht. Alle Rechtecke sind in
// Anzeige-Koordinaten in PDF-Punkten (so wie der Sidecar sie liefert,
// Seitenrotation schon eingerechnet). Bildschirm-Pixel entstehen erst
// durch Multiplikation mit dem Anzeigemaßstab `scale` - dadurch bleiben
// Markierungen bei jedem Zoom und jeder Scrollposition an derselben Stelle.

export type Rect = [number, number, number, number];
export type Point = [number, number];
export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** Mindestgröße einer Markierung in Punkten (der Sidecar verlangt 1 pt). */
export const MIN_SIZE = 2;

/** Mausposition -> Seitenkoordinaten. `origin` ist die linke obere Ecke
 *  der Seite auf dem Bildschirm (getBoundingClientRect), damit Scrollen
 *  und Layout automatisch berücksichtigt sind. */
export function clientToPage(clientX: number, clientY: number, origin: { left: number; top: number }, scale: number): Point {
  return [(clientX - origin.left) / scale, (clientY - origin.top) / scale];
}

export function normalizeRect(a: Point, b: Point): Rect {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

export function clampRect(r: Rect, width: number, height: number): Rect {
  return [
    Math.max(0, Math.min(r[0], width)),
    Math.max(0, Math.min(r[1], height)),
    Math.max(0, Math.min(r[2], width)),
    Math.max(0, Math.min(r[3], height)),
  ];
}

export function rectSize(r: Rect): [number, number] {
  return [r[2] - r[0], r[3] - r[1]];
}

export function isUsable(r: Rect): boolean {
  const [w, h] = rectSize(r);
  return w >= MIN_SIZE && h >= MIN_SIZE;
}

export function containsPoint(r: Rect, p: Point, tolerance = 0): boolean {
  return p[0] >= r[0] - tolerance && p[0] <= r[2] + tolerance && p[1] >= r[1] - tolerance && p[1] <= r[3] + tolerance;
}

/** Verschiebt ein Rechteck, ohne es über den Seitenrand hinauszuschieben. */
export function moveRect(r: Rect, dx: number, dy: number, width: number, height: number): Rect {
  const [w, h] = rectSize(r);
  const x0 = Math.max(0, Math.min(r[0] + dx, width - w));
  const y0 = Math.max(0, Math.min(r[1] + dy, height - h));
  return [x0, y0, x0 + w, y0 + h];
}

/** Größe über einen Anfasser ändern. Wird eine Kante über die gegenüber-
 *  liegende gezogen, bleibt das Rechteck mindestens MIN_SIZE groß. */
export function resizeRect(r: Rect, handle: Handle, dx: number, dy: number, width: number, height: number): Rect {
  let [x0, y0, x1, y1] = r;
  if (handle.includes("w")) x0 = Math.min(x0 + dx, x1 - MIN_SIZE);
  if (handle.includes("e")) x1 = Math.max(x1 + dx, x0 + MIN_SIZE);
  if (handle.includes("n")) y0 = Math.min(y0 + dy, y1 - MIN_SIZE);
  if (handle.includes("s")) y1 = Math.max(y1 + dy, y0 + MIN_SIZE);
  return clampRect([x0, y0, x1, y1], width, height);
}

/** Position eines Anfassers (Mittelpunkt) in Seitenkoordinaten. */
export function handlePoint(r: Rect, handle: Handle): Point {
  const cx = (r[0] + r[2]) / 2;
  const cy = (r[1] + r[3]) / 2;
  const x = handle.includes("w") ? r[0] : handle.includes("e") ? r[2] : cx;
  const y = handle.includes("n") ? r[1] : handle.includes("s") ? r[3] : cy;
  return [x, y];
}

/** Anfasser unter dem Mauszeiger (Trefferradius in Bildschirmpixeln). */
export function handleAt(r: Rect, p: Point, scale: number, radiusPx = 6): Handle | null {
  const radius = radiusPx / scale;
  for (const h of HANDLES) {
    const [hx, hy] = handlePoint(r, h);
    if (Math.abs(hx - p[0]) <= radius && Math.abs(hy - p[1]) <= radius) return h;
  }
  return null;
}

export interface WordBox {
  id: string;
  bbox: Rect;
}

/** Wort unter oder nahe der Position (für die Textauswahl). */
export function wordIndexAt(words: WordBox[], p: Point, tolerance = 3): number {
  let best = -1;
  let bestDistance = Infinity;
  words.forEach((w, i) => {
    const [x0, y0, x1, y1] = w.bbox;
    const dx = p[0] < x0 ? x0 - p[0] : p[0] > x1 ? p[0] - x1 : 0;
    const dy = p[1] < y0 ? y0 - p[1] : p[1] > y1 ? p[1] - y1 : 0;
    const distance = Math.hypot(dx, dy);
    if (distance <= tolerance && distance < bestDistance) {
      best = i;
      bestDistance = distance;
    }
  });
  return best;
}

/** Wörter zwischen Anker und aktueller Position in Lesereihenfolge des
 *  PDFs - wie eine Textauswahl in einem PDF-Betrachter. */
export function wordsBetween<T>(words: T[], anchor: number, focus: number): T[] {
  if (anchor < 0 || focus < 0) return [];
  const [a, b] = anchor <= focus ? [anchor, focus] : [focus, anchor];
  return words.slice(a, b + 1);
}

/** Auflösung, mit der die Seite gerendert wird: passend zu Zoom und
 *  Bildschirmdichte, in Stufen (Cache im Sidecar), höchstens 3. */
export function renderZoomFor(scale: number, devicePixelRatio = 1): number {
  const wanted = scale * Math.max(1, devicePixelRatio);
  return Math.min(3, Math.max(0.5, Math.ceil(wanted * 2) / 2));
}

export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

export function nextZoom(current: number, direction: 1 | -1): number {
  if (direction > 0) return ZOOM_STEPS.find((z) => z > current + 1e-6) ?? ZOOM_STEPS[ZOOM_STEPS.length - 1];
  return [...ZOOM_STEPS].reverse().find((z) => z < current - 1e-6) ?? ZOOM_STEPS[0];
}

export function sameRects(a: Rect[], b: Rect[]): boolean {
  return a.length === b.length && a.every((r, i) => r.every((v, j) => Math.abs(v - b[i][j]) < 0.01));
}
