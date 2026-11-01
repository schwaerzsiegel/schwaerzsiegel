// Phase 20: Personen im Dokument – alle Stellen einer Person auf einmal schwärzen oder auslassen.
//   node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

import type { Mark } from "../src/api/client.ts";
import { nameWords, personGroups, samePersonMarks } from "../src/lib/markLabels.ts";

let n = 0;
function person(text: string, over: Partial<Mark> = {}): Mark {
  return {
    id: `m${++n}`,
    page: 0,
    label: "Person",
    text,
    kind: "text",
    rects: [[0, 0, 10, 10]],
    confidence: 0.9,
    origin: "rule",
    status: "suggested",
    default_selected: true,
    ...over,
  } as Mark;
}

test("Namenswörter ohne Anrede, Titel und Satzzeichen", () => {
  assert.deepEqual(nameWords("Herrn Dr. Jonas Beispiel)"), ["Jonas", "Beispiel"]);
  assert.deepEqual(nameWords("gez. Hansen,"), ["Hansen"]);
  assert.deepEqual(nameWords("Becker-Sommer."), ["Becker-Sommer"]);
});

test("Schreibweisen derselben Person bilden eine Gruppe", () => {
  const marks = [
    person("Wiebke Hansen"),
    person("Hansen,"),
    person("Frau Hansen", { page: 3 }),
    person("Dr. Jana Beispiel"),
    person("Beispiel, Dr. Jana"),
    person("Jana"),
    person("Muster"),
  ];
  const groups = personGroups(marks);
  const byKey = Object.fromEntries(groups.map((g) => [g.key, g]));
  assert.equal(byKey.Hansen.marks.length, 3);
  assert.equal(byKey.Hansen.name, "Wiebke Hansen");
  assert.equal(byKey.Beispiel.marks.length, 3, "„Beispiel, Dr. Jana“ und der Vorname allein gehören dazu");
  assert.equal(byKey.Beispiel.name, "Dr. Jana Beispiel");
  assert.equal(byKey.Muster.marks.length, 1);
  assert.equal(groups[0].marks.length, 3, "Personen mit den meisten Stellen zuerst");
});

test("Anzeigename ohne Satzzeichen am Ende, Titel bleibt", () => {
  const groups = personGroups([person("Thomsen."), person("Frau Thomsen,"), person("(Dr. Möller,")]);
  assert.deepEqual(groups.map((g) => g.name).sort(), ["Dr. Möller", "Thomsen"]);
});

test("Ein Vorname, der zu zwei Personen passt, wird keiner zugeordnet", () => {
  const groups = personGroups([person("Jana Beispiel"), person("Jana Muster"), person("Jana")]);
  assert.deepEqual(groups.map((g) => g.key).sort(), ["Beispiel", "Jana", "Muster"]);
});

test("Zählung nach Status", () => {
  const g = personGroups([
    person("Hansen", { status: "confirmed" }),
    person("Frau Hansen", { status: "rejected" }),
    person("Wiebke Hansen"),
  ])[0];
  assert.deepEqual([g.open, g.confirmed, g.rejected], [1, 1, 1]);
});

test("Nur Personen-Treffer, andere Kategorien bleiben außen vor", () => {
  const tel = person("0431 123456", { label: "Telefonnummer" });
  assert.equal(samePersonMarks([tel], tel), null);
  assert.deepEqual(personGroups([tel]), []);
});
