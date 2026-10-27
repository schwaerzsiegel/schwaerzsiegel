// Phase 4: Arbeitsschritte (Leiste unter der Werkzeugleiste). Abgeleitet aus
// dem Zustand, nicht separat gespeichert - so zeigt sie nie etwas anderes,
// als tatsächlich erledigt ist.

export type StepState = "done" | "active" | "todo" | "failed";

export interface WorkflowInput {
  analysisDone: boolean;
  openSuggestions: number;
  toRedact: number;
  resultCurrent: boolean;
  verificationFailed: boolean;
  exported: boolean;
}

export interface Step {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

export function deriveSteps(s: WorkflowInput): Step[] {
  const reviewing = s.analysisDone && !s.resultCurrent && !s.verificationFailed;
  return [
    { key: "open", label: "Öffnen", state: "done" },
    { key: "analyze", label: "Analysieren", state: s.analysisDone ? "done" : "active" },
    {
      key: "review",
      label: "Prüfen und markieren",
      state: s.resultCurrent || s.verificationFailed ? "done" : reviewing ? "active" : "todo",
      detail: reviewing && s.openSuggestions > 0 ? `${s.openSuggestions} offen` : undefined,
    },
    { key: "apply", label: "Freigeben und schwärzen", state: s.resultCurrent || s.verificationFailed ? "done" : "todo" },
    { key: "verify", label: "Verifizieren", state: s.verificationFailed ? "failed" : s.resultCurrent ? "done" : "todo" },
    { key: "export", label: "Exportieren", state: s.exported ? "done" : s.resultCurrent ? "active" : "todo" },
  ];
}

/** "Seite 2: …" aus einem Prüfbefund -> Seitenindex (0-basiert) oder null. */
export function pageOfProblem(problem: string): number | null {
  const m = /^Seite (\d+)/.exec(problem);
  return m ? Number(m[1]) - 1 : null;
}
