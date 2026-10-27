// Phase 16: eingeklappte Nebenfundstellen und gleiche Werte im ganzen Dokument.
//   node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

import type { Mark } from "../src/api/client.ts";
import { CATEGORIES, isMinor, minorSummary, sameValueMarks } from "../src/lib/markLabels.ts";

let n = 0;
function mark(over: Partial<Mark>): Mark {
  return {
    id: `m${++n}`,
    page: 0,
    label: "Person",
    text: "Erika Musterfrau",
    kind: "text",
    rects: [[0, 0, 10, 10]],
    confidence: 0.9,
    origin: "ai",
    status: "suggested",
    default_selected: true,
    ...over,
  } as Mark;
}

test("Nicht vorausgewählte offene Vorschläge gelten als Nebenfundstellen", () => {
  assert.equal(isMinor(mark({ label: "Organisation", default_selected: false })), true);
  assert.equal(isMinor(mark({ label: "Datum", default_selected: false, origin: "rule" })), true);
});

test("Vorausgewählte, bestätigte, verworfene und eigene Markierungen sind keine Nebenfundstellen", () => {
  assert.equal(isMinor(mark({})), false);
  assert.equal(isMinor(mark({ label: "Organisation", default_selected: false, status: "confirmed" })), false);
  assert.equal(isMinor(mark({ label: "Organisation", default_selected: false, status: "rejected" })), false);
  assert.equal(isMinor(mark({ origin: "manual", default_selected: false })), false);
});

test("Unterschriften aus der Handschrift-Erkennung sind vorausgewählt und damit sichtbar", () => {
  const sig = mark({ label: "Unterschrift", text: "", kind: "area", origin: "rule", confidence: 0.7 });
  assert.equal(isMinor(sig), false);
  assert.ok(CATEGORIES.includes("Unterschrift") && CATEGORIES.includes("Handschrift oder Stempel"));
});

test("Zusammenfassung zählt je Kategorie, häufigste zuerst", () => {
  const marks = [
    mark({ label: "Organisation", default_selected: false }),
    mark({ label: "Organisation", default_selected: false }),
    mark({ label: "Datum", default_selected: false }),
    mark({ label: "Person" }),
  ];
  assert.deepEqual(minorSummary(marks), [
    { label: "Organisation", count: 2 },
    { label: "Datum", count: 1 },
  ]);
});

test("Gleiche Werte werden im ganzen Dokument gefunden, nicht nur auf derselben Seite", () => {
  const a = mark({ page: 0, text: "Jonas Becker" });
  const b = mark({ page: 2, text: "jonas becker " });
  const c = mark({ page: 1, text: "Sophie Wagner" });
  assert.deepEqual(sameValueMarks([a, b, c], a).map((m) => m.id), [a.id, b.id]);
  const area = mark({ text: "", kind: "area" });
  assert.deepEqual(sameValueMarks([area, a], area), [area]);
});
