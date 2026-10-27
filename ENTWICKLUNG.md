# Schwärzsiegel – Entwicklung

Technische Notizen für Entwicklerinnen und Entwickler (Aufbau, Schnittstelle, Versionsgeschichte).
Überblick und Lizenz: [README.md](README.md).

## Projektskizze (ursprünglich „Lokale PDF-Redaction-App“)

Rein lokale Desktop-Anwendung zur DSGVO-konformen, layout-erhaltenden Schwärzung
personenbezogener Daten in PDF-Dokumenten. Kein Cloud-Zugriff, keine externen
API-Calls — alle Verarbeitung findet auf dem Gerät des Nutzers statt.

## Architektur

```
┌─────────────────────────────────────────────┐
│  Lokales Gerät (kein Netzwerkzugriff)        │
│                                               │
│  ┌───────────────┐   localhost   ┌─────────┐ │
│  │ Tauri-Frontend │ ─────────────▶ │ Python  │ │
│  │ (React)        │ ◀───────────── │ Sidecar │ │
│  └───────────────┘   JSON/HTTP    └─────────┘ │
│                                       │        │
│                                  ┌────▼────┐   │
│                                  │ SQLite  │   │
│                                  │ Audit-  │   │
│                                  │ Log     │   │
│                                  └─────────┘   │
└─────────────────────────────────────────────┘
```

Das Frontend (Tauri + React) startet beim App-Launch den Python-Sidecar als
Kindprozess und spricht ihn ausschließlich über `127.0.0.1` auf einem beim
Start zufällig gewählten Port an. Der Sidecar wird für die Auslieferung mit
PyInstaller/Nuitka zu einer eigenständigen ausführbaren Datei gebündelt —
Endnutzer brauchen keinen eigenen Python-Interpreter.

## Ordnerstruktur

```
redaction-app/
├── src-tauri/          Rust-Shell: Fenster, Sidecar-Prozessverwaltung
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   └── src/main.rs
├── src/                Frontend (React)
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/client.ts   Typisierter Client für die Sidecar-API
│   └── components/ReviewPanel.tsx
├── sidecar/             Python-Backend
│   ├── main.py          FastAPI-Einstiegspunkt
│   ├── requirements.txt
│   └── core/
│       ├── pdf_processor.py   Textextraktion + echte Redaction (PyMuPDF)
│       ├── ocr.py             OCR-Fallback für Scan-PDFs (Tesseract)
│       ├── ner.py             GLiNER + Regex-Kaskade
│       ├── llm.py             Lokales LLM für Kontext-Disambiguierung
│       └── audit.py           Audit-Log / Compliance-Report
└── package.json
```

## Sidecar-API (lokal, JSON über HTTP)

| Endpoint | Zweck |
|---|---|
| `POST /documents` | PDF hochladen, Verarbeitung anstoßen |
| `GET /documents/{id}/pages/{n}/suggestions` | Redaction-Vorschläge inkl. Confidence |
| `POST /documents/{id}/pages/{n}/decisions` | Human-Review-Entscheidung übermitteln |
| `POST /documents/{id}/finalize` | Echte Redaction + Sicherheitscheck ausführen |
| `GET /documents/{id}/audit-report` | Compliance-Report abrufen |

## Setup (Entwicklung)

```bash
pip install -r sidecar/requirements.txt
npm install
npm run dev          # startet Vite UND den Sidecar (python main.py), Browser: http://localhost:1420
npm run dev:exe      # wie oben, aber mit der gebauten Sidecar-.exe
build-sidecar.cmd    # baut sidecar/dist/redaction-sidecar/ (PyInstaller, Windows)
npm run tauri dev    # Desktop-Fenster, main.rs startet python main.py
npm run tauri:build  # Installer, bündelt sidecar/dist/redaction-sidecar/ als Ressource
```

## Status nach Integration des bestehenden MVP (v0.1)

Übernommen aus dem bestehenden PySide6-MVP, weil dort bereits gut gelöst:
- Wort-ID-basierte bbox-Auflösung (`p{page}_w{idx}`) statt Zeichen-Offsets
- Echte Nachprüfung durch Re-Extraktion im geschwärzten Zielbereich
- Review-Signatur-Mechanismus: eine Freigabe wird ungültig, sobald sich
  die Auswahl danach ändert - verhindert versehentliches Finalisieren
  einer veralteten Auswahl

Bewusst **nicht** übernommen: die Cloud-Anbindung an `app.anymize.ai`
("Waterfall"-Modell). Für v1 ist die Erkennung vollständig lokal
(Regex mit Prüfsummen + GLiNER + lokales LLM) - kein Text verlässt das
Gerät. Eine optionale Cloud-Erweiterung ist bewusst zurückgestellt.

Ebenfalls behoben: OCR war im MVP als Abhängigkeit vorhanden, aber nie
tatsächlich aufgerufen (Scan-PDFs hätten 0 Wörter geliefert) - das ist
jetzt in `pdf_processor.load()` verdrahtet.

## Nächste Schritte

- Modellgewichte (GLiNER, LLM, YOLO) als separate, signierte Download-Pakete
  statt im App-Bundle, um Installationsgröße zu begrenzen

## v31: Geschwindigkeit

- KI-Modelle (GLiNER, Qwen) werden beim Sidecar-Start im Hintergrund geladen
  und bleiben im Speicher (`sidecar/core/models.py`, Status unter `GET /status`).
- LLM bekommt nur ~200 Zeichen Kontext um den Kandidaten statt der ersten
  500 Zeichen des Dokuments, stoppt nach dem JSON (`stop="}"`) und prüft
  gleiche Kandidaten pro Dokument nur einmal (Cache).
- GPU wird automatisch genutzt, wenn vorhanden (GLiNER: PyTorch mit CUDA;
  Qwen: llama-cpp-python mit CUDA), sonst CPU.
- `POST /documents` antwortet sofort, die Analyse läuft im Hintergrund;
  Fortschritt über `GET /documents/{id}/progress` (Seite x von y + Schritt).
- Zeitmessung pro Seite und Schritt im Sidecar-Terminal und in `sidecar.log`.

## v32: Sidecar-Autostart und .exe

- Der Sidecar meldet seinen Port als Zeile `SIDECAR_PORT=<port>` auf stdout.
  `npm run dev` (Vite-Plugin in `vite.config.ts`) und die Tauri-App
  (`src-tauri/src/main.rs`) starten ihn selbst, lesen den Port und beenden
  ihn wieder (`--exit-with-parent`: Sidecar endet, wenn seine stdin schließt).
- Pfade zentral in `sidecar/core/paths.py`: Modelle und Tesseract relativ zum
  Programmordner, Log/Audit-DB/Port-Datei im Datenordner
  (gebündelt: `%LOCALAPPDATA%\Redaction-App`, von Tauri vorgegeben).
- `build-sidecar.cmd` + `sidecar/redaction-sidecar.spec`: PyInstaller-Ordner-Build,
  legt `models\*.gguf` (harter Link) und Tesseract (nur deu/eng/osd) daneben.
- Tauri: `externalBin` ersetzt durch Ressourcen-Ordner (`tauri.release.conf.json`),
  weil der PyInstaller-Build ein Ordner ist, keine einzelne Datei.

## v33: GLiNER offline

- `download_models.cmd` (einmal mit Internet) lädt GLiNER über das normale
  `from_pretrained` in einen app-eigenen Zwischenspeicher `sidecar/models/hf-cache`
  und prüft danach in einem neuen Prozess ohne Internet, ob das Modell lädt.
- Gibt es `models/hf-cache`, setzt `core/paths.py` `HF_HUB_CACHE` darauf und
  schaltet `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` ein: keine Netzwerkzugriffe mehr.
  Die Statusanzeige zeigt „offline“ bzw. „aus dem Internet“.
- `build-sidecar.cmd` kopiert `hf-cache` (ohne `blobs`) neben die .exe.

## v34: Installer

- `build-installer.cmd` baut alles in einem Rutsch: GLiNER-Download (falls nötig),
  Sidecar-.exe, `npm run tauri:build` (NSIS, currentUser, Deutsch) und das Beta-Paket
  `release/Redaction-App-Beta` = setup.exe + Ordner `models`.
- Modelle sind nicht im Installer (NSIS-Grenze 2 GB). `src-tauri/installer-hooks.nsh`
  kopiert `models` neben der setup.exe nach `$INSTDIR\sidecar\models` und entfernt sie
  bei der Deinstallation. `build-sidecar.cmd` legt deshalb keine Modelle mehr neben
  die .exe; `npm run dev:exe` setzt dafür `REDACTION_MODELS_DIR=sidecar/models`.

## v35: Pfadlänge

- `sidecar/trim_dist.py` (von `build-installer.cmd` aufgerufen) entfernt tief verschachtelte
  Unterordner in `*.dist-info/licenses` (PyTorch, > 260 Zeichen, makensis bricht sonst ab)
  und prüft alle Pfade so, wie makensis sie sieht.
- `build-installer.cmd` überspringt den Sidecar-Build, wenn die .exe schon existiert
  (`build-installer.cmd neu` erzwingt ihn).

## v36: Download in der Desktop-App

- `<a download>`-Links zum Sidecar (anderer Ursprung) tun in WebView2 nichts. Die Buttons laden die Datei jetzt
  per `fetch` (`saveDownload` in `src/api/client.ts`); in Tauri gehen die Bytes an den Befehl
  `save_to_downloads` (`src-tauri/src/main.rs`), der sie im Downloads-Ordner ablegt und den Explorer öffnet.
  Im Browser-Dev-Modus wird weiterhin normal über einen Blob-Link heruntergeladen.

## v37: Download über den Sidecar

- Der v36-Weg (Bytes per Tauri-IPC an `save_to_downloads`) hat unter Windows nicht funktioniert; der Befehl ist
  wieder entfernt. Neu: `POST /documents/{id}/result/save?kind=pdf|mapping` kopiert das Ergebnis in den
  Downloads-Ordner (`SHGetKnownFolderPath`, sonst `~/Downloads`), ohne zu überschreiben, und öffnet
  `explorer /select`. Antwort: `{"path": ...}`.
- Speicher-Status und Fehler stehen direkt unter dem Download-Button (vorher nur oben im allgemeinen Fehlerbanner).

## v38: Analyse-Modus

- `POST /documents` nimmt das Formularfeld `mode` = `fast` (Standard) | `ai`. Im Modus `fast` läuft
  `ner.detect(..., use_llm=False)`: Regex + GLiNER, unsichere Treffer bleiben ohne LLM-Prüfung stehen.
- Das LLM wird nicht mehr beim Start geladen (`models.STATUS["llm"]["state"] == "idle"`), sondern bei Bedarf:
  `POST /models/llm/load` (Frontend, sobald „Gründlich mit KI" gewählt wird) oder beim ersten `ai`-Dokument.
  `/status` meldet `ready`, sobald GLiNER geladen ist.
- Frontend: `AnalysisModeSwitch` auf dem Startbildschirm und kompakt neben „Anderes Dokument wählen".
- IBAN-Regex korrigiert: `DE\d{2}(?:\s?\d{4}){4}\s?\d{2}` (vorher nur Vierergruppen, fand keine deutsche IBAN).

## v39 Phase 1: Sichere Schwärzung und manuelle Markierungen

- Eine Routine für alle Markierungen (`sidecar/core/redaction.py`): Zeichen werden nach demselben Kriterium
  entfernt, das die Prüfung verwendet (Mittelpunkt im Bereich oder ≥ 25 % der Fläche) – behebt
  „Schwärzung nicht verifiziert“ und das unbemerkte Mitlöschen von Nachbarzeilen.
- Unabhängige Prüfung der gespeicherten Datei (Text, Pixel, Bilddaten, Vektorgrafik, Annotationen, Links,
  Anhänge/JavaScript, Metadaten, Lesezeichen); Sperre mit Begründung im Prüfbericht.
- Markierungsmodell (`sidecar/core/marks.py`): Herkunft manuell/Regel/KI, Status vorgeschlagen/bestätigt/
  verworfen/geschwärzt; Koordinaten in der API immer als Anzeige-Koordinaten (Seitenrotation eingerechnet).
- Neue Endpunkte: `GET /documents/{id}/pages/{n}`, `GET|POST /documents/{id}/marks`,
  `PATCH|DELETE /documents/{id}/marks/{mark}`, `POST /documents/{id}/marks/{mark}/restore`,
  `POST /documents/{id}/result/verify`. `POST /documents` kennt zusätzlich `mode=manual|rules`.
- Oberfläche: Werkzeuge Auswählen/Rechteck/Text, Verschieben, Größe ändern, Strg+Z/Strg+Y, Zoom,
  gemeinsame Markierungsliste, Prüfbericht, Modus „Nur manuell“.
- Tests: `cd sidecar && python -m unittest test_phase1_redaction` sowie `npm run test:frontend`.

## v39 Phase 2: Erkennungsqualität

- Hybride Erkennung mit Rangfolge Kontext > Struktur/Prüfziffer > GLiNER > reines Format
  (`sidecar/core/rules.py`, `validators.py`, `ner.py`): „Personalnummer: 104827“ wird Personalnummer,
  unbeschriftete Zahlenfolgen werden keiner Kategorie zugeordnet, Feldbezeichnungen nie als Wert.
- Neue Kategorien/Regeln: Personalnummer, Geburtsort, Krankenversichertennummer, Ausweisnummer (Prüfziffer),
  SV-Nummer (Prüfziffer), Steuernummer, Kontonummer, Anschrift (Straße, PLZ + Ort), Gesundheitsdaten mit Kontext,
  Firmenanschrift (standardmäßig nicht vorausgewählt).
- Oberfläche: Filter „Unsicher“, Sammelbestätigung nur ab 85 %, Begründung je Treffer.
- Messung: `cd sidecar && python eval/evaluate.py --mode fast --seed 4711 [--variants]`, Tabellen `python eval/compare.py`;
  Tests `python -m unittest test_phase2_detection`.

## v39 Phase 3: Lokaler KI-Modus

- Betriebsarten: A „Ohne KI“ (`rules`), B „Schnell-KI“ (`fast`, GLiNER), C „Gründliche KI“ (`ai`, zusätzlich
  Sprachmodell für unsichere Treffer) sowie „Analyse überspringen“ (`manual`). `/status` meldet je Modus, ob er
  verfügbar ist, und warum nicht (Modell fehlt, zu wenig RAM).
- Sprachmodell: `sidecar/models/Qwen3-1.7B-Q4_K_M.gguf` (Quelle: Hugging Face `unsloth/Qwen3-1.7B-GGUF`,
  Apache 2.0, SHA-256 `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897`). Liegt
  `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` daneben, wird dieses bevorzugt. Antwortformat per Grammatik erzwungen.
- Netzwerksperre im Sidecar (`core/offline.py`): nur 127.0.0.1 erreichbar, auch keine Namensauflösung.
- GLiNER prüft lange Seiten in Abschnitten (vorher wurde Text nach ~370 Wörtern stillschweigend ignoriert).
- Abbruch: `POST /documents/{id}/cancel`; Sprachmodell entladen: `POST /models/llm/unload`.
- Messung: `python eval/evaluate.py --mode ai --ambiguous --seed 4711 --per-template 6`, Leistung `python eval/perf.py`.
- Hinweis: `npm run dev` startet `python main.py` – vorher `.venv\Scripts\activate`, sonst greift Windows auf den
  Store-Platzhalter `python` zurück.

## v39 Phase 4: Neues Frontend und Branding

- Arbeitsbereich mit Werkzeugleiste, Arbeitsschritten, Seitenminiaturen, Fundstellen, Sicherheitsstatus,
  Dialog „Schwärzung anwenden“, Ergebnisansicht mit Prüfbericht; Hell/Dunkel; keine Schriften aus dem Internet.
- Branding: Ordner `sidecar/branding/` (Vorlage `sidecar/branding-beispiel/`, oder `REDACTION_BRANDING_DIR`) mit
  `branding.json` (productName, customerName, accent, showPoweredBy, logo, logoDark, support) und Logos (SVG/PNG).
- `POST /documents/{id}/reanalyze` – erneut analysieren, manuelle und entschiedene Markierungen bleiben.
- Browsertest: `npm run dev` starten, dann `node tests/ui-smoke.mjs <test.pdf> <ausgabeordner>`
  (Edge headless, temporäres Profil; `UI_BRANDING=beispiel`, wenn mit Beispiel-Branding gestartet).

## v39 Phase 5: Integration und Installer

- Bauen: `build-installer.cmd` (bzw. `build-installer.cmd neu`), nutzt das Python der Projekt-.venv.
  Ergebnis: `release\Redaction-App-Beta` (setup.exe + `models`, optional `branding`).
- Texterkennung: Fehlt Deutsch in der Tesseract-Installation, wird `sidecar\models\tessdata\deu.traineddata`
  genutzt und in den Sidecar gepackt (tessdata_fast, Apache-2.0).
- Installierte App testen: App mit `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9334` starten,
  dann `CDP_PORT=9334 node tests/ui-smoke.mjs <test.pdf> <ausgabeordner>`.

## v39 Phase 6: Testversion mit Lizenzdatei (entfernt in Phase 11)

Die Lizenzprüfung mit signierter Lizenzdatei wurde mit dem Wechsel zu freier Software (AGPL) in
Phase 11 vollständig entfernt. `tools/lizenzhinweise.py` erzeugt weiterhin die Lizenzhinweise der
Bausteine (beim Build automatisch).

## v39 Phase 11: Freie Software (AGPL-3.0-or-later)

- Lizenz: `LICENSE`, Kennzeichnung nach REUSE in `REUSE.toml` und `LICENSES/`.
- Der Installer enthält `LICENSE.txt`, `QUELLCODE.txt`, die Drittanbieter-Lizenzen und die Bausteinliste.
- Info-Dialog: Lizenzhinweis, Gewährleistungsausschluss und Quellcode-Adresse
  (`SOURCE_URL` in `src/components/BrandBar.tsx`).
- Tests: `sidecar/test_phase11_agpl.py`. Der Oberflächentest `tests/ui-smoke.mjs` ist auf den
  Test-Brief aus `sidecar/test_phase1_redaction.py` abgestimmt (feste Klickpositionen).
  Für `npm run dev` ggf. `PYTHON=.venv\Scripts\python.exe` setzen.

## v39 Phase 7: Name und Branding „Schwärzsiegel“

- Produktname **Schwärzsiegel** (Fenster, Kopfzeile, Info-Dialog, Installer, Startmenü, Lizenzwerkzeug).
  Technischer Name `schwaerzsiegel`, App-Kennung `de.schwaerzsiegel.desktop`, Beta-Paket
  `release\Schwaerzsiegel-Beta`. Der Sidecar heißt intern weiter `redaction-sidecar`.
- Bildmarke (Logo-Entwurf A): `src/components/LogoMark.tsx`; Programmsymbol und Installer-Grafiken
  erzeugt `tools/branding/erzeugen.py` lokal aus `tools/branding/logo.svg`.
- Standard-Akzentfarbe Siegellack-Rot `#9B2A24` (Kunden-Branding kann sie weiterhin ersetzen).
- Alle Texte der Oberfläche in der Anrede „Sie“.

## v39 Phase 10: Aufräumen und Nachweise

- Ein neues Dokument oder `DELETE /documents/{id}` entfernt das bisherige vollständig; temporäre Dateien in
  `%TEMP%\Schwaerzsiegel\sitzung-*` (`sidecar/core/workdir.py`), gelöscht beim Beenden, Reste beim nächsten Start.
- Netzwerk- und Dateimessung: `tools/messung/netzwerk_messung.py`; Bausteinliste (CycloneDX) aus `tools/lizenzhinweise.py`.
- Tests: `python -m unittest test_phase10_cleanup`.

## v39 Phase 13: Corporate Branding erstellen

- `tools/branding/paket_erstellen.py` erzeugt aus Organisationsname, Hausfarbe (#RRGGBB) und Logo (SVG/PNG,
  optional zweites Logo für dunkle Darstellung) einen fertigen Ordner `branding/` mit `branding.json`,
  Logos und `LIESMICH.txt` für die IT, auf Wunsch zusätzlich als ZIP (`--zip`). Standardausgabe: `ausgabe/`.
- Prüft Kontrast der Hausfarbe zu weißer Schrift (4,5:1; mit `--abdunkeln` automatisch angepasst), SVGs auf
  Skripte und externe Verweise, PNG-Größe, und lädt das Ergebnis zur Gegenprüfung wie die App (`core/branding.py`).
- Produktname bleibt „Schwärzsiegel“, `showPoweredBy` ist immer an (Entscheidung 24.10.2026).
- Tests: `python -m unittest test_phase13_branding`.
