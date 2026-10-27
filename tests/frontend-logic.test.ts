// Tests der Frontend-Logik ohne Browser und ohne zusätzliche Pakete:
//   node --test tests/
// (Node 24 führt TypeScript direkt aus.)
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  clientToPage,
  handleAt,
  moveRect,
  nextZoom,
  normalizeRect,
  renderZoomFor,
  resizeRect,
  wordIndexAt,
  wordsBetween,
  type Rect,
} from "../src/lib/geometry.ts";
import { History, combine } from "../src/lib/history.ts";

const W = 595;
const H = 842;

test("Mausposition wird bei jedem Zoom und Scrollstand gleich auf die Seite abgebildet", () => {
  for (const scale of [0.5, 1, 1.5, 3]) {
    for (const origin of [{ left: 0, top: 0 }, { left: 120, top: -900 }]) {
      const pagePoint: [number, number] = [100, 200];
      const client = [origin.left + pagePoint[0] * scale, origin.top + pagePoint[1] * scale];
      const [x, y] = clientToPage(client[0], client[1], origin, scale);
      assert.ok(Math.abs(x - 100) < 1e-9 && Math.abs(y - 200) < 1e-9, `scale ${scale}`);
    }
  }
});

test("Rechteck aufziehen in jede Richtung", () => {
  assert.deepEqual(normalizeRect([50, 80], [10, 20]), [10, 20, 50, 80]);
  assert.deepEqual(normalizeRect([10, 80], [50, 20]), [10, 20, 50, 80]);
});

test("Verschieben bleibt innerhalb der Seite", () => {
  assert.deepEqual(moveRect([10, 10, 60, 30], 5, 5, W, H), [15, 15, 65, 35]);
  assert.deepEqual(moveRect([10, 10, 60, 30], -100, -100, W, H), [0, 0, 50, 20]);
  assert.deepEqual(moveRect([500, 800, 590, 840], 100, 100, W, H), [505, 802, 595, 842]);
});

test("Größe ändern über Anfasser, Mindestgröße bleibt erhalten", () => {
  const r: Rect = [100, 100, 200, 150];
  assert.deepEqual(resizeRect(r, "se", 10, 20, W, H), [100, 100, 210, 170]);
  assert.deepEqual(resizeRect(r, "nw", -10, -20, W, H), [90, 80, 200, 150]);
  assert.deepEqual(resizeRect(r, "e", -500, 0, W, H), [100, 100, 102, 150]);
  assert.deepEqual(resizeRect(r, "n", 0, 999, W, H), [100, 148, 200, 150]);
  assert.deepEqual(resizeRect(r, "w", -500, 0, W, H), [0, 100, 200, 150]);
});

test("Anfasser-Treffer hängt vom Zoom ab (Pixelradius)", () => {
  const r: Rect = [100, 100, 200, 150];
  assert.equal(handleAt(r, [203, 153], 1), "se");
  assert.equal(handleAt(r, [203, 153], 3), null); // 3 pt = 9 px bei 300 %
  assert.equal(handleAt(r, [150, 100], 1), "n");
  assert.equal(handleAt(r, [150, 125], 1), null);
});

test("Textauswahl: Wort unter dem Zeiger und Bereich in Lesereihenfolge", () => {
  const words = [
    { id: "a", bbox: [72, 60, 100, 72] as Rect },
    { id: "b", bbox: [105, 60, 160, 72] as Rect },
    { id: "c", bbox: [72, 74, 120, 86] as Rect },
    { id: "d", bbox: [125, 74, 170, 86] as Rect },
  ];
  assert.equal(wordIndexAt(words, [80, 65]), 0);
  assert.equal(wordIndexAt(words, [102, 65]), 0); // Leerraum: nächstes Wort innerhalb der Toleranz
  assert.equal(wordIndexAt(words, [300, 300]), -1);
  assert.deepEqual(wordsBetween(words, 1, 2).map((w) => w.id), ["b", "c"]); // mehrzeilig
  assert.deepEqual(wordsBetween(words, 3, 0).map((w) => w.id), ["a", "b", "c", "d"]); // rückwärts
  assert.deepEqual(wordsBetween(words, -1, 2), []);
});

test("Render-Auflösung in Stufen, begrenzt auf 3", () => {
  assert.equal(renderZoomFor(1, 1), 1);
  assert.equal(renderZoomFor(1.25, 1), 1.5);
  assert.equal(renderZoomFor(1.5, 2), 3);
  assert.equal(renderZoomFor(3, 2), 3);
  assert.equal(renderZoomFor(0.25, 1), 0.5);
  assert.equal(nextZoom(1, 1), 1.25);
  assert.equal(nextZoom(1, -1), 0.75);
  assert.equal(nextZoom(3, 1), 3);
  assert.equal(nextZoom(0.5, -1), 0.5);
});

test("Rückgängig/Wiederholen in richtiger Reihenfolge", async () => {
  const log: string[] = [];
  const history = new History();
  const cmd = (name: string) => ({
    label: name,
    undo: async () => void log.push(`undo ${name}`),
    redo: async () => void log.push(`redo ${name}`),
  });
  history.push(cmd("A"));
  history.push(cmd("B"));
  assert.equal(history.state().undoLabel, "B");
  await history.undo();
  await history.undo();
  assert.equal(await history.undo(), false);
  await history.redo();
  assert.deepEqual(log, ["undo B", "undo A", "redo A"]);
  history.push(cmd("C")); // neuer Schritt verwirft "B" aus dem Wiederholen-Stapel
  assert.equal(history.state().canRedo, false);
});

test("Fehlgeschlagener Schritt bleibt auf dem Stapel", async () => {
  const history = new History();
  history.push({ label: "X", undo: async () => { throw new Error("offline"); }, redo: async () => {} });
  await assert.rejects(history.undo());
  assert.equal(history.state().canUndo, true);
  assert.equal(history.state().canRedo, false);
});

test("Zusammengefasste Schritte werden rückwärts rückgängig gemacht", async () => {
  const log: string[] = [];
  const c = (n: string) => ({ label: n, undo: async () => void log.push(`u${n}`), redo: async () => void log.push(`r${n}`) });
  const all = combine("Alle", [c("1"), c("2"), c("3")]);
  await all.undo();
  await all.redo();
  assert.deepEqual(log, ["u3", "u2", "u1", "r1", "r2", "r3"]);
});
