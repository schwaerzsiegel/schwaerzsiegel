// Phase 21: Zwei Personen mit gleichem Nachnamen stehen im Kasten „Personen im Dokument“ getrennt.
//   node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

import type { Mark } from "../src/api/client.ts";
import { personGroups, samePersonMarks } from "../src/lib/markLabels.ts";

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

test("Gleicher Nachname, verschiedene Vornamen: getrennte Zeilen", () => {
  const marks = [
    person("Wiebke Hansen"),
    person("Hansen, Wiebke"),
    person("Wiebke"),
    person("Jan Hansen"),
    person("Herrn Jan Hansen", { page: 2 }),
    person("Frau Hansen"),
    person("Hansen,"),
  ];
  const byName = Object.fromEntries(personGroups(marks).map((g) => [g.name, g.marks.length]));
  assert.deepEqual(byName, { "Wiebke Hansen": 3, "Jan Hansen": 2, "Hansen (Vorname nicht genannt)": 2 });
});

test("Auslassen einer Person betrifft die andere nicht", () => {
  const wiebke = person("Wiebke Hansen");
  const jan = person("Jan Hansen");
  const group = samePersonMarks([wiebke, jan, person("Wiebke Hansen")], wiebke)!;
  assert.equal(group.marks.length, 2);
  assert.ok(!group.marks.includes(jan));
});

test("Nur ein Vorname bekannt: alle Stellen bleiben eine Person", () => {
  const groups = personGroups([person("Wiebke Hansen"), person("Frau Hansen"), person("Hansen,")]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].name, "Wiebke Hansen");
});

test("Titel und Komma-Schreibweise ändern den Vornamen nicht", () => {
  const groups = personGroups([person("Dr. Jana Beispiel"), person("Beispiel, Dr. Jana"), person("Jonas Beispiel")]);
  assert.deepEqual(groups.map((g) => [g.name, g.marks.length]), [["Dr. Jana Beispiel", 2], ["Jonas Beispiel", 1]]);
});
