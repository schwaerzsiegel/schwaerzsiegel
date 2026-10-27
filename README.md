# Schwärzsiegel

Schwärzsiegel schwärzt personenbezogene Daten in PDF-Dokumenten, **vollständig lokal** auf dem
Windows-Arbeitsplatz. Dokumente, erkannte Angaben und Ergebnisse verlassen den Rechner nicht. Es gibt
keinen Cloud-Dienst, kein Benutzerkonto und keine Netzwerkzugriffe.

Die Erkennung schlägt Fundstellen vor, die Entscheidung trifft der Mensch. Geschwärzt wird erst nach
ausdrücklicher Freigabe. Danach prüft Schwärzsiegel die gespeicherte Datei unabhängig, und nur ein
Ergebnis ohne Befund lässt sich exportieren.

## Funktionen

- **Erkennung in drei Stufen:** Regeln mit Prüfziffern (ohne KI), lokales Erkennungsmodell (GLiNER)
  oder zusätzlich ein lokales Sprachmodell (Qwen3) für unsichere Treffer. Oder ganz ohne Analyse,
  nur manuell.
- **Erkannte Angaben (Auswahl):** Namen, Anschriften, Telefon, E-Mail, Geburtsdaten, IBAN, Steuer-ID,
  Sozialversicherungs- und Krankenversichertennummer, Ausweis- und Personalnummern, Aktenzeichen,
  Kfz-Kennzeichen, Gesundheitsdaten und weitere besondere Kategorien nach Art. 9 DSGVO.
- **Eigene Markierungen** als Rechteck oder Textauswahl, mit Rückgängig und Wiederholen.
- **Echtes Schwärzen:** Text, Bildpunkte und Vektorgrafik werden aus der Datei entfernt, nicht nur
  überdeckt. Metadaten, JavaScript, Anhänge und Vorschaubilder werden bereinigt.
- **Pseudonymisieren** mit getrennt gespeicherter Zuordnungstabelle.
- **Prüfbericht** und Prüfung der fertigen Datei vor dem Export.
- **Texterkennung** für gescannte Seiten (Tesseract, lokal).

## Freie Software und fertige Installationspakete

Schwärzsiegel ist **freie Software** unter der GNU Affero General Public License. Der vollständige
Quellcode liegt hier; Sie können ihn prüfen, selbst bauen (siehe unten), verändern und weitergeben.

Fertige **Installationspakete** mit den getesteten KI-Modellen als Offline-Paket gibt es für Behörden,
Unternehmen und Kanzleien unter https://schwaerzsiegel.de: kostenlos im Rahmen eines dreimonatigen Pilots
und für den Regelbetrieb als **Schwärzsiegel-Paket** mit Unterlagen für Freigabe und Datenschutz,
Vorlagen, vorausgefülltem EVB-IT-Vertrag, Corporate Branding und Unterstützung. Das Programm ist
dasselbe wie in diesem Quellcode. Sicherheitsupdates gibt es für jede Hauptversion fünf Jahre ab deren
Erscheinen.

Sicherheitsupdates erscheinen immer auch hier im Quellcode.

## Selbst bauen

Voraussetzungen: Windows 11 (64 Bit), Python 3.11, Node.js 24, Rust (stable), Microsoft Edge WebView2,
für die Texterkennung Tesseract-OCR in `C:\Program Files\Tesseract-OCR` (wird mit eingepackt).

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r sidecar\requirements-lock.txt
download_models.cmd
build-installer.cmd
```

Das Ergebnis liegt in `release\Schwaerzsiegel`. Derselbe Build läuft automatisch auf GitHub
Actions ([.github/workflows/build.yml](.github/workflows/build.yml)); dort steht auch, wie
`llama-cpp-python` für alle x64-Prozessoren mit AVX2 übersetzt wird (Umgebungsvariable `CMAKE_ARGS`). Das optionale Sprachmodell für die „Gründliche
KI“ (Qwen3, `.gguf`) legen Sie vorher in `sidecar\models`. Einzelheiten zum Aufbau, zu den Tests und
zur Entwicklung stehen in [ENTWICKLUNG.md](ENTWICKLUNG.md).

**Hinweis zu Windows 11:** Ist die *intelligente App-Steuerung* (Smart App Control) eingeschaltet,
blockiert Windows beim Bauen unsignierte Hilfsdateien (Fehler „Eine Anwendungssteuerungsrichtlinie hat
diese Datei blockiert“, os error 4551). Bauen Sie dann auf einem Rechner ohne diese Einstellung.

## Lizenz

© 2026 Cem Karatas

Schwärzsiegel ist freie Software: Sie können es unter den Bedingungen der GNU Affero General Public
License, wie von der Free Software Foundation veröffentlicht, weitergeben und/oder verändern, entweder
gemäß Version 3 der Lizenz oder (nach Ihrer Wahl) jeder späteren Version. Die Veröffentlichung erfolgt
in der Hoffnung, dass es nützlich ist, aber **ohne jede Gewährleistung**. Den Lizenztext finden Sie in
[LICENSE](LICENSE).

Verwendete Bausteine und ihre Lizenzen: `src-tauri/lizenzhinweise/` und im Programm unter „Info“.
Lizenzkennzeichnung der Dateien nach [REUSE](https://reuse.software) in `REUSE.toml`.

Name und Logo „Schwärzsiegel“ sind von der Lizenz nicht erfasst, siehe [TRADEMARKS.md](TRADEMARKS.md).

## Kontakt

- Sicherheitslücken: [SECURITY.md](SECURITY.md)
- Mitwirken: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code-Signatur: [CODE_SIGNING.md](CODE_SIGNING.md)
- Hersteller: Cem Karatas · info@schwaerzsiegel.de
