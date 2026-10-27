# Code signing policy / Regeln zur Code-Signatur

**Status:** Die Veröffentlichungen sind derzeit **nicht signiert**. Geplant ist die Signatur mit einem
eigenen Code-Signing-Zertifikat des Herstellers. Diese Regeln gelten ab der ersten signierten Version.
Bis dahin prüfen Sie die Echtheit eines Installationsprogramms über die mitgelieferten SHA-256-Prüfsummen.

## Was signiert wird

Signiert werden ausschließlich Programmdateien, die aus dem Quellcode dieses Repositorys gebaut werden:
das Installationsprogramm, `schwaerzsiegel.exe` und der Erkennungsdienst `redaction-sidecar.exe`.
Gebaut wird automatisch mit GitHub Actions ([.github/workflows/build.yml](.github/workflows/build.yml))
auf einem frischen Windows-Rechner; jede Signatur wird einzeln von Hand freigegeben. Mitgelieferte
Programme Dritter (z. B. Tesseract-OCR, Laufzeitbibliotheken) behalten ihre eigene oder keine Signatur.

## Team roles / Rollen

| Rolle | Person |
|---|---|
| Committers and reviewers (Autoren, Prüfung von Beiträgen Dritter) | [CemKa89](https://github.com/CemKa89) |
| Approvers (Freigabe jeder Signatur) | [CemKa89](https://github.com/CemKa89) |

Alle Beteiligten nutzen die Zwei-Faktor-Anmeldung bei GitHub und beim Signaturdienst.

## Privacy policy / Datenschutz

This program will not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it.

Schwärzsiegel arbeitet ausschließlich lokal. Es überträgt keine Dokumente, keine Nutzungsdaten und
keine sonstigen Informationen an den Hersteller oder an Dritte; eine eingebaute Netzwerksperre
verhindert Verbindungen nach außen.
