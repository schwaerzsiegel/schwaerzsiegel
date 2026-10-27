// Phase 4: Farblogik fürs Branding und die Arbeitsschritte (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_ACCENT, accentVariables, contrast, mix, usableAccent } from "../src/lib/branding.ts";
import { deriveSteps, pageOfProblem } from "../src/lib/workflow.ts";

test("Akzentfarbe: zu helle Farben werden durch den Standard ersetzt", () => {
  assert.equal(usableAccent("#0f6b6b"), "#0F6B6B");
  assert.equal(usableAccent("#F0F0F0"), DEFAULT_ACCENT);
  assert.equal(usableAccent("rot"), DEFAULT_ACCENT);
  assert.equal(usableAccent(null), DEFAULT_ACCENT);
});

test("Abgeleitete Farben sind in beiden Modi lesbar", () => {
  for (const accent of ["#2B4C7E", "#0F6B6B", "#7A3E9D", "#9C4A12", "#000000"]) {
    const v = accentVariables(accent);
    assert.ok(contrast(v["--accent-l"], "#FFFFFF") >= 4.5, `${accent}: weiß auf Akzent (hell)`);
    assert.ok(contrast(v["--accent-ink-l"], "#FFFFFF") >= 4.5, `${accent}: Akzent-Text auf weiß`);
    assert.ok(contrast(v["--accent-d"], "#181C24") >= 4.5, `${accent}: Akzent auf dunklem Grund`);
    assert.ok(contrast(v["--accent-d"], v["--on-accent-d"]) >= 4.5, `${accent}: Schrift auf Akzent (dunkel)`);
  }
});

test("Farbmischung", () => {
  assert.equal(mix("#000000", "#FFFFFF", 0.5), "#808080");
  assert.equal(mix("#2B4C7E", "#2B4C7E", 0.3), "#2B4C7E");
});

test("Arbeitsschritte folgen dem Zustand", () => {
  const base = { analysisDone: true, openSuggestions: 2, toRedact: 5, resultCurrent: false, verificationFailed: false, exported: false };
  const review = deriveSteps(base);
  assert.equal(review.find((s) => s.key === "review")?.state, "active");
  assert.equal(review.find((s) => s.key === "review")?.detail, "2 offen");
  assert.equal(review.find((s) => s.key === "export")?.state, "todo");

  const done = deriveSteps({ ...base, resultCurrent: true });
  assert.deepEqual(done.map((s) => s.state), ["done", "done", "done", "done", "done", "active"]);

  const failed = deriveSteps({ ...base, verificationFailed: true });
  assert.equal(failed.find((s) => s.key === "verify")?.state, "failed");
  assert.equal(failed.find((s) => s.key === "export")?.state, "todo");

  assert.equal(deriveSteps({ ...base, resultCurrent: true, exported: true }).at(-1)?.state, "done");
});

test("Seite aus einem Prüfbefund", () => {
  assert.equal(pageOfProblem("Seite 2: Vektorgrafik …"), 1);
  assert.equal(pageOfProblem("PDF enthält XML-Metadaten"), null);
});
