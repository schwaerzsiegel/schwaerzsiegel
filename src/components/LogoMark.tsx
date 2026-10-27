// Phase 7: Bildmarke von Schwärzsiegel (Logo-Entwurf A) – schwarzer
// Schwärzungsbalken, an seinem Ende versiegelt. Gleiche Form wie das
// Programmsymbol (tools/branding/logo.svg).
// Ausschnitt eng um die sichtbare Form (2:1), damit die Kopfzeile schmal bleibt.
export function LogoMark({ size = 18 }: { size?: number }) {
  return (
    <svg className="logo-mark" viewBox="1 9 92 46" width={size * 2} height={size} aria-hidden="true">
      <rect className="logo-bar" x="2" y="22" width="62" height="20" rx="2" />
      <circle className="logo-seal" cx="70" cy="32" r="22" />
      <circle className="logo-on-seal" cx="70" cy="32" r="16.5" strokeWidth="1.6" strokeDasharray="2.4 2.4" />
      <path
        className="logo-on-seal"
        d="M61.5 32.5l6 6 11-12"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
