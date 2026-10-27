"""
Kunden-Branding (Phase 4).

Ein Ordner `branding/` neben dem Sidecar (Entwicklung: sidecar/branding,
installiert: <Programmordner>/sidecar/branding; überschreibbar mit
REDACTION_BRANDING_DIR) mit:

    branding.json   {"productName": "Datenschutz-Schwärzung",
                     "customerName": "Stadtwerke Musterhausen",
                     "accent": "#0F6B6B",
                     "showPoweredBy": true,
                     "logo": "logo.svg", "logoDark": "logo-dark.svg",
                     "support": {"name": "...", "email": "...", "phone": "...",
                                 "url": "...", "text": "Impressum ..."}}
    logo.svg/.png   (optional, je eine Fassung für hell und dunkel)

Bewusst NICHT anpassbar: Farben der Sicherheitszustände, Warn- und
Prüftexte. Branding gelangt nie in das geschwärzte PDF.

Robust: Fehlt der Ordner oder ist etwas ungültig, gilt das Standarddesign;
die Gründe stehen in "warnings" (und im Log), die App startet immer.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from core.paths import APP_DIR

BRANDING_DIR = Path(os.environ.get("REDACTION_BRANDING_DIR") or APP_DIR / "branding")
DEFAULT_ACCENT = "#9B2A24"  # Siegellack-Rot (Phase 7)
MAX_JSON_BYTES = 64 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}
MIN_CONTRAST = 4.5  # weiße Schrift auf Akzentfarbe (WCAG AA)

_TEXT_LIMITS = {"productName": 60, "customerName": 80}
_SUPPORT_FIELDS = {"name": 120, "email": 120, "phone": 60, "url": 200, "text": 600}


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_with_white(hex_color: str) -> float:
    return 1.05 / (_luminance(hex_color) + 0.05)


def _clean_text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return text[:limit] or None


def _logo_file(name, warnings: list[str], key: str) -> Path | None:
    if name is None:
        return None
    if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() not in LOGO_TYPES:
        warnings.append(f"{key}: nur ein Dateiname mit .svg oder .png im Branding-Ordner ist erlaubt")
        return None
    path = BRANDING_DIR / name
    if not path.is_file():
        warnings.append(f"{key}: Datei {name} nicht gefunden")
        return None
    if path.stat().st_size > MAX_LOGO_BYTES:
        warnings.append(f"{key}: Datei {name} ist größer als 2 MB")
        return None
    return path


def load() -> dict:
    """Branding laden und prüfen. Gibt immer ein vollständiges Ergebnis zurück."""
    result = {"active": False, "productName": "Schwärzsiegel", "customerName": None, "accent": DEFAULT_ACCENT,
              "showPoweredBy": True, "support": None, "logo": None, "logoDark": None, "warnings": []}
    config_path = BRANDING_DIR / "branding.json"
    if not config_path.is_file():
        return result
    warnings = result["warnings"]
    try:
        if config_path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError("branding.json ist größer als 64 KB")
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("branding.json muss ein JSON-Objekt sein")
    except Exception as e:
        warnings.append(f"branding.json konnte nicht gelesen werden ({e}) – Standarddesign")
        return result

    result["active"] = True
    for key, limit in _TEXT_LIMITS.items():
        if key in data:
            value = _clean_text(data[key], limit)
            if value:
                result[key] = value
            else:
                warnings.append(f"{key}: ungültiger Text")

    accent = data.get("accent")
    if accent is not None:
        if not isinstance(accent, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
            warnings.append("accent: Farbe im Format #RRGGBB angeben – Standardfarbe verwendet")
        elif contrast_with_white(accent) < MIN_CONTRAST:
            warnings.append(f"accent: {accent} ist zu hell für weiße Schrift (Kontrast "
                            f"{contrast_with_white(accent):.1f}:1, nötig {MIN_CONTRAST}:1) – Standardfarbe verwendet")
        else:
            result["accent"] = accent.upper()

    if isinstance(data.get("showPoweredBy"), bool):
        result["showPoweredBy"] = data["showPoweredBy"]

    support = data.get("support")
    if isinstance(support, dict):
        cleaned = {k: _clean_text(support.get(k), limit) for k, limit in _SUPPORT_FIELDS.items()}
        if cleaned.get("url") and not re.match(r"https?://", cleaned["url"]):
            warnings.append("support.url: nur http(s)-Adressen")
            cleaned["url"] = None
        cleaned = {k: v for k, v in cleaned.items() if v}
        result["support"] = cleaned or None

    light = _logo_file(data.get("logo"), warnings, "logo")
    dark = _logo_file(data.get("logoDark"), warnings, "logoDark")
    result["logo"] = light.name if light else None
    result["logoDark"] = dark.name if dark else (light.name if light else None)
    return result


def logo(variant: str) -> tuple[bytes, str] | None:
    """Logodatei (Inhalt, Medientyp) für "light" oder "dark"."""
    info = load()
    name = info["logoDark"] if variant == "dark" else info["logo"]
    if not name:
        return None
    path = BRANDING_DIR / name
    return path.read_bytes(), LOGO_TYPES[path.suffix.lower()]
