"""
Zeitmessung pro Verarbeitungsschritt (neu in v31). Schreibt eine Zeile
pro Seite plus eine Zusammenfassung pro Dokument ins Sidecar-Terminal
und zusätzlich in die Datei sidecar.log im App-Datenverzeichnis (bzw.
im sidecar-Ordner beim Start mit `python main.py`) - so ist nachvollziehbar,
wo die Zeit tatsächlich verloren geht (OCR, GLiNER oder LLM).
"""

import logging
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from core.paths import DATA_DIR

log = logging.getLogger("redaction")


def setup_logging() -> Path:
    log_path = DATA_DIR / "sidecar.log"
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        if sys.stdout is not None:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(fmt)
            log.addHandler(console)
        file = logging.FileHandler(log_path, encoding="utf-8")
        file.setFormatter(fmt)
        log.addHandler(file)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log_path


class StepTimer:
    """Sammelt Sekunden pro Schrittname, z.B. {"GLiNER": 1.23, "LLM": 4.5}."""

    def __init__(self):
        self.seconds: dict[str, float] = {}
        self.counters: dict[str, int] = {}

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - start

    def count(self, name: str, n: int = 1):
        self.counters[name] = self.counters.get(name, 0) + n

    def add(self, other: "StepTimer"):
        for k, v in other.seconds.items():
            self.seconds[k] = self.seconds.get(k, 0.0) + v
        for k, v in other.counters.items():
            self.counters[k] = self.counters.get(k, 0) + v

    def total(self) -> float:
        return sum(self.seconds.values())

    def format(self) -> str:
        parts = [f"{name} {secs:.2f}s" for name, secs in self.seconds.items()]
        line = " | ".join(parts) if parts else "keine Schritte"
        if self.counters:
            extras = ", ".join(f"{k}: {v}" for k, v in self.counters.items())
            line += f"  ({extras})"
        return line
