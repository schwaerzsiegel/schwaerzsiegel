// Phase 4: Kunden-Branding und Farbableitung. Rein rechnerisch (ohne DOM),
// damit es mit `npm run test:frontend` geprüft werden kann.

export interface Branding {
  active: boolean;
  productName: string;
  customerName: string | null;
  accent: string;
  showPoweredBy: boolean;
  support: { name?: string; email?: string; phone?: string; url?: string; text?: string } | null;
  logo: string | null;
  logoDark: string | null;
  warnings: string[];
}

export const DEFAULT_ACCENT = "#9B2A24"; // Siegellack-Rot (Phase 7)

export const DEFAULT_BRANDING: Branding = {
  active: false,
  productName: "Schwärzsiegel",
  customerName: null,
  accent: DEFAULT_ACCENT,
  showPoweredBy: true,
  support: null,
  logo: null,
  logoDark: null,
  warnings: [],
};

type Rgb = [number, number, number];

export function parseHex(hex: string): Rgb | null {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function toHex([r, g, b]: Rgb): string {
  return "#" + [r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("").toUpperCase();
}

export function mix(a: string, b: string, weightOfB: number): string {
  const x = parseHex(a)!;
  const y = parseHex(b)!;
  return toHex([0, 1, 2].map((i) => x[i] + (y[i] - x[i]) * weightOfB) as Rgb);
}

function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex)!.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

/** Wie im Sidecar: Akzentfarbe muss weiße Schrift mit 4,5:1 tragen. */
export function usableAccent(hex: string | null | undefined): string {
  return hex && parseHex(hex) && contrast(hex, "#FFFFFF") >= 4.5 ? hex.toUpperCase() : DEFAULT_ACCENT;
}

const DARK_SURFACE = "#181C24";

/** Aufhellen, bis die Farbe auf dunklem Grund mindestens `min`:1 erreicht. */
function lightenForDark(hex: string, min: number): string {
  for (let w = 0.3; w <= 0.95; w += 0.05) {
    const candidate = mix(hex, "#FFFFFF", w);
    if (contrast(candidate, DARK_SURFACE) >= min) return candidate;
  }
  return mix(hex, "#FFFFFF", 0.95);
}

/** Alle Akzent-Varianten für Hell- und Dunkelmodus als CSS-Variablen. */
export function accentVariables(hex: string): Record<string, string> {
  const accent = usableAccent(hex);
  const darkAccent = lightenForDark(accent, 4.5);
  return {
    "--accent-l": accent,
    "--accent-ink-l": mix(accent, "#000000", 0.2),
    "--accent-soft-l": mix(accent, "#FFFFFF", 0.88),
    "--on-accent-l": "#FFFFFF",
    "--accent-d": darkAccent,
    "--accent-ink-d": lightenForDark(accent, 7),
    "--accent-soft-d": mix(accent, DARK_SURFACE, 0.78),
    "--on-accent-d": "#0E1420",
  };
}
