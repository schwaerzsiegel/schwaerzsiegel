// Phase 1: Rückgängig/Wiederholen für Markierungen (Strg+Z / Strg+Y).
// Jeder Schritt kennt seine Umkehrung als Aufruf an den Sidecar - die
// Markierungen leben dort, das Frontend zeigt nur den aktuellen Stand.

export interface Command {
  label: string;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
}

export interface HistoryState {
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
}

function last<T>(items: T[]): T | undefined {
  return items[items.length - 1];
}

export class History {
  private done: Command[] = [];
  private undone: Command[] = [];
  private busy = false;
  private readonly limit: number;
  private readonly onChange: () => void;

  constructor(limit = 200, onChange: () => void = () => {}) {
    this.limit = limit;
    this.onChange = onChange;
  }

  /** Bereits ausgeführten Schritt aufnehmen. */
  push(command: Command) {
    this.done.push(command);
    if (this.done.length > this.limit) this.done.shift();
    this.undone = [];
    this.onChange();
  }

  async undo(): Promise<boolean> {
    return this.step(this.done, this.undone, (c) => c.undo());
  }

  async redo(): Promise<boolean> {
    return this.step(this.undone, this.done, (c) => c.redo());
  }

  clear() {
    this.done = [];
    this.undone = [];
    this.onChange();
  }

  state(): HistoryState {
    return {
      canUndo: this.done.length > 0 && !this.busy,
      canRedo: this.undone.length > 0 && !this.busy,
      undoLabel: last(this.done)?.label ?? null,
      redoLabel: last(this.undone)?.label ?? null,
    };
  }

  // Ein Schritt nach dem anderen; schlägt er fehl, bleibt er, wo er war.
  private async step(from: Command[], to: Command[], run: (c: Command) => Promise<void>): Promise<boolean> {
    const command = last(from);
    if (!command || this.busy) return false;
    this.busy = true;
    this.onChange();
    try {
      await run(command);
      from.pop();
      to.push(command);
      return true;
    } finally {
      this.busy = false;
      this.onChange();
    }
  }
}

/** Mehrere Schritte als einen behandeln (z. B. "Alle bestätigen"). */
export function combine(label: string, commands: Command[]): Command {
  return {
    label,
    undo: async () => {
      for (const c of [...commands].reverse()) await c.undo();
    },
    redo: async () => {
      for (const c of commands) await c.redo();
    },
  };
}
