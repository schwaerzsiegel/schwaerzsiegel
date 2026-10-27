# Mitwirken

Danke für Ihr Interesse an Schwärzsiegel.

## Fehler und Vorschläge

- **Sicherheitslücken** bitte nie öffentlich melden, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.
- Fehler und Vorschläge können Sie als Ticket melden. Bitte nur erfundene Beispieldaten anhängen,
  niemals echte Dokumente.
- Meldungen werden nach Kräften bearbeitet, ohne zugesagte Frist. Kundinnen und Kunden erreichen uns
  außerdem unter support@schwaerzsiegel.de. Über die Umsetzung von Vorschlägen entscheidet der Hersteller.

## Code beitragen

Beiträge stehen unter derselben Lizenz wie das Projekt (**AGPL-3.0-or-later**).

Mit jedem Commit bestätigen Sie das [Developer Certificate of Origin 1.1](https://developercertificate.org/):
Sie dürfen den Beitrag unter dieser Lizenz einreichen. Dazu fügen Sie jedem Commit eine Zeile hinzu,
am einfachsten mit `git commit -s`:

```
Signed-off-by: Vorname Nachname <adresse@example.org>
```

Bitte beachten Sie außerdem:

- Tests laufen lassen (siehe [ENTWICKLUNG.md](ENTWICKLUNG.md)). Neue Funktionen bringen eigene Tests mit.
- Nur synthetische Testdaten verwenden.
- Keine Netzwerkzugriffe einbauen: Schwärzsiegel arbeitet ausschließlich lokal.
- Neue Abhängigkeiten nur mit AGPL-verträglicher Lizenz.
