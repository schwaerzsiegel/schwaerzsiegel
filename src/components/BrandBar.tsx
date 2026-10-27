import type { Branding } from "../lib/branding";
import { THEME_LABEL, nextTheme, type ThemeChoice } from "../lib/theme";
import { Icon } from "./Icon";
import { LogoMark } from "./LogoMark";

export const APP_VERSION = "1.0.0";
// Phase 11: Lizenzhinweis im Info-Dialog.
export const COPYRIGHT = "© 2026 Cem Karatas";
// Phase 11: Der Quellcode liegt dem Installationspaket als ZIP-Datei bei (build-installer.cmd).
export const SOURCE_FILE = "Schwaerzsiegel-Quellcode-<Version>.zip";
export const SOURCE_CONTACT = "info@schwaerzsiegel.de";
export const SOURCE_URL = "https://github.com/schwaerzsiegel/schwaerzsiegel";

export interface BrandAssets {
  branding: Branding;
  logoUrl: string | null;
  logoDarkUrl: string | null;
}

/** Logo bzw. Standardzeichen plus Kunden- und Produktname (Kopfzeile). */
export function BrandMark({ assets, dark, compact }: { assets: BrandAssets; dark: boolean; compact?: boolean }) {
  const { branding } = assets;
  const logo = dark ? assets.logoDarkUrl ?? assets.logoUrl : assets.logoUrl;
  const customer = branding.customerName;
  return (
    <div className={compact ? "brand is-compact" : "brand"}>
      {logo || customer ? (
        <div className="brand-customer">
          {logo && <img className="brand-logo" src={logo} alt={customer ? `Logo ${customer}` : "Logo"} />}
          {customer && !compact && <span className="brand-customer-name">{customer}</span>}
        </div>
      ) : (
        <span className="brand-mark">
          <LogoMark size={compact ? 13 : 18} />
        </span>
      )}
      {!compact && <span className="brand-product">{branding.productName}</span>}
    </div>
  );
}

export function PoweredBy({ assets }: { assets: BrandAssets }) {
  if (!assets.branding.active) return <span className="powered-by mono">{APP_VERSION}</span>;
  if (!assets.branding.showPoweredBy) return null;
  return (
    <span className="powered-by">
      bereitgestellt mit Schwärzsiegel <span className="mono">{APP_VERSION}</span>
    </span>
  );
}

export function ThemeButton({ theme, onChange }: { theme: ThemeChoice; onChange: (t: ThemeChoice) => void }) {
  const icon = theme === "dark" ? "moon" : theme === "light" ? "sun" : "monitor";
  return (
    <button
      className="icon-btn"
      onClick={() => onChange(nextTheme(theme))}
      aria-label={`${THEME_LABEL[theme]} – umschalten`}
      title={`${THEME_LABEL[theme]} (klicken zum Umschalten)`}
    >
      <Icon name={icon} />
    </button>
  );
}

export function InfoButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="icon-btn" onClick={onClick} aria-label="Info und Kontakt" title="Info und Kontakt">
      <Icon name="info" />
    </button>
  );
}
