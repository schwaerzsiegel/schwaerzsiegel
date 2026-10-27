"""
Arbeitsordner für temporäre Dateien (Phase 10).

Alles, was der Sidecar vorübergehend auf die Festplatte schreibt (Ergebnis-
PDFs, Zuordnungstabelle der Pseudonymisierung, Zwischendateien von
Tesseract und des Webservers beim Hochladen), landet in einem eigenen
Ordner je Sitzung:

    %TEMP%\\Schwaerzsiegel\\sitzung-<Prozess-ID>-<Zufall>

Dafür wird tempfile.tempdir auf diesen Ordner gesetzt; alle Bibliotheken,
die das tempfile-Modul nutzen, schreiben dann dorthin.

Gelöscht wird der Ordner beim Beenden des Sidecars (cleanup_session) und -
falls der Prozess hart beendet wurde oder abgestürzt ist - beim nächsten
Start (cleanup_stale). Eine laufende zweite Sitzung wird dabei nicht
angetastet: Jede Sitzung hält die Datei "aktiv.lock" offen, und Windows
erlaubt das Löschen einer geöffneten Datei nicht.
"""

import os
import secrets
import shutil
import tempfile
from pathlib import Path

LOCK_NAME = "aktiv.lock"
_PREFIX = "sitzung-"

BASE_DIR = Path(tempfile.gettempdir()) / "Schwaerzsiegel"
SESSION_DIR: Path | None = None
_lock_handle = None


def _remove_if_inactive(folder: Path) -> bool:
    """Löscht den Ordner einer beendeten Sitzung. False, wenn die Sitzung
    noch läuft (Sperrdatei geöffnet) oder das Löschen scheitert."""
    lock = folder / LOCK_NAME
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def cleanup_stale() -> list[str]:
    """Reste früherer Sitzungen entfernen. Gibt die gelöschten Ordner zurück."""
    removed = []
    if not BASE_DIR.is_dir():
        return removed
    for folder in BASE_DIR.iterdir():
        if folder == SESSION_DIR or not folder.is_dir() or not folder.name.startswith(_PREFIX):
            continue
        if _remove_if_inactive(folder):
            removed.append(folder.name)
    return removed


def start() -> Path:
    """Sitzungsordner anlegen, sperren und als Standard für tempfile setzen."""
    global SESSION_DIR, _lock_handle
    if SESSION_DIR is not None:
        return SESSION_DIR
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        folder = BASE_DIR / f"{_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
        try:
            folder.mkdir()
            _lock_handle = open(folder / LOCK_NAME, "w", encoding="utf-8")
            break
        except OSError:
            # Sehr unwahrscheinlich: gleichzeitig räumt eine andere Sitzung auf
            continue
    else:
        raise RuntimeError(f"Arbeitsordner unter {BASE_DIR} konnte nicht angelegt werden.")
    SESSION_DIR = folder
    tempfile.tempdir = str(folder)
    return folder


def cleanup_session() -> None:
    """Eigenen Sitzungsordner samt Inhalt löschen (beim Beenden)."""
    global SESSION_DIR, _lock_handle
    if SESSION_DIR is None:
        return
    if _lock_handle is not None:
        try:
            _lock_handle.close()
        except OSError:
            pass
        _lock_handle = None
    shutil.rmtree(SESSION_DIR, ignore_errors=True)
    if tempfile.tempdir == str(SESSION_DIR):
        tempfile.tempdir = None
    SESSION_DIR = None
