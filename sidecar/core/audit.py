"""Lokales Audit-Log: was wurde wann von wem geschwärzt/bestätigt."""

import json
import sqlite3
from datetime import datetime, timezone
from core.paths import DATA_DIR

_DB_PATH = DATA_DIR / "audit.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    return conn


def log(document_id: str, event_type: str, payload: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (document_id, event_type, payload, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (document_id, event_type, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )


def report(document_id: str) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT event_type, payload, timestamp FROM audit_log "
            "WHERE document_id = ? ORDER BY timestamp",
            (document_id,),
        ).fetchall()
    return {
        "document_id": document_id,
        "events": [
            {"type": r[0], "payload": json.loads(r[1]), "timestamp": r[2]}
            for r in rows
        ],
    }
