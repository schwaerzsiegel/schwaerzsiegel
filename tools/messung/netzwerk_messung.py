"""
Phase 10 (V3/V4): Netzwerk- und Dateimessung des Erkennungsdienstes.

Startet den Sidecar genau wie die App (main.py --exit-with-parent, eigener
Datenordner und TEMP-Ordner) und spielt alle Arbeitsschritte mit
synthetischen Dokumenten durch:
  Start und Laden der KI-Modelle, Analyse "Gründliche KI" (inkl. Sprachmodell),
  Vorschau, Markieren, Schwärzen mit Prüfung, Pseudonymisieren, Herunterladen,
  Scan mit Texterkennung (Schnell-KI), Dokument schließen, Beenden.

Währenddessen wird alle 50 ms die Verbindungstabelle des Betriebssystems für
den Sidecar und alle seine Unterprozesse (z. B. Tesseract) abgefragt (psutil,
ohne Administratorrechte). Danach: welche Dateien liegen im Datenordner und
im TEMP-Ordner, und stehen darin Inhalte der Testdokumente?

Aufruf (aus dem Projektordner):
    .venv\\Scripts\\python.exe tools\\messung\\netzwerk_messung.py <arbeitsordner> <bericht.txt>

Grenze: Sehr kurze Verbindungen (unter 50 ms) zwischen zwei Abfragen
könnten unbemerkt bleiben. Ergänzend protokolliert die Netzwerksperre des
Sidecars jeden verweigerten Versuch im Log (wird mit ausgewertet).
"""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import fitz
import psutil
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "sidecar"
PYTHON = sys.executable
TOKEN = "e" * 64

LETTER = [
    "Personalakte (synthetisch)",
    "Name: Erika Mustermann",
    "Anschrift: Musterweg 12, 12345 Musterstadt",
    "Personalnummer: 104827",
    "Telefon: 030 12345678",
    "E-Mail: erika.mustermann@example.org",
    "IBAN: DE89 3704 0044 0532 0130 00",
    "Geburtsdatum: 12.03.1985",
    "Frau Mustermann hat den Antrag am 03.02.2026 gestellt.",
]
SCAN_LINES = ["Bescheid (synthetisch)", "Herr Max Beispielmann", "Telefon 040 9876543", "Aktenzeichen 77-2026"]
SECRETS = ["Mustermann", "104827", "erika.mustermann", "DE89 3704", "Beispielmann", "9876543", "Musterweg"]


def letter_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in LETTER:
        page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 16
    return doc.tobytes()


def scan_pdf() -> bytes:
    img = Image.new("L", (1654, 2339), 255)  # A4 bei 200 dpi
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(SCAN_LINES):
        draw.text((160, 200 + 90 * i), line, fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=buf.getvalue())
    return doc.tobytes()


class Monitor(threading.Thread):
    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.root = psutil.Process(pid)
        self.stop = threading.Event()
        self.samples = 0
        self.seen: dict[tuple, dict] = {}
        self.processes: set[str] = set()

    def run(self):
        while not self.stop.is_set():
            try:
                procs = [self.root] + self.root.children(recursive=True)
            except psutil.NoSuchProcess:
                break
            for p in procs:
                try:
                    name = p.name()
                    self.processes.add(f"{name} (PID {p.pid})")
                    for c in p.net_connections(kind="inet"):
                        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
                        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
                        key = (name, "TCP" if c.type == 1 else "UDP", c.laddr.ip if c.laddr else "",
                               c.raddr.ip if c.raddr else "", c.status)
                        entry = self.seen.setdefault(key, {"count": 0, "example": f"{laddr} -> {raddr or '-'}"})
                        entry["count"] += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            self.samples += 1
            time.sleep(0.05)


def is_local(ip: str) -> bool:
    return ip in ("", "127.0.0.1", "::1", "0.0.0.0", "::") or ip.startswith("127.")


def main():
    work = Path(sys.argv[1]).resolve()
    report_path = Path(sys.argv[2]).resolve()
    if work.exists():
        shutil.rmtree(work)
    data_dir, temp_dir, out_dir = work / "daten", work / "temp", work / "ausgabe"
    for d in (data_dir, temp_dir, out_dir):
        d.mkdir(parents=True)
    env = {**os.environ, "REDACTION_APP_DATA_DIR": str(data_dir), "TEMP": str(temp_dir), "TMP": str(temp_dir),
           "REDACTION_SESSION_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}

    # Entwickler-Testlizenz in den Messordner (gilt nur in der Entwicklung)
    subprocess.run([PYTHON, "-c", "from testhilfe_lizenz import install_dev_license; install_dev_license()"],
                   cwd=SIDECAR, env=env, check=True)

    log = []

    def step(text):
        stamp = datetime.now().strftime("%H:%M:%S")
        log.append(f"{stamp}  {text}")
        print(f"{stamp}  {text}", flush=True)

    started = time.time()
    proc = subprocess.Popen([PYTHON, "main.py", "--exit-with-parent"], cwd=SIDECAR, env=env, text=True,
                            encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    monitor = Monitor(proc.pid)
    monitor.start()
    port = None
    for line in proc.stdout:
        if line.startswith("SIDECAR_PORT="):
            port = int(line.split("=")[1])
            break
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    step(f"Sidecar gestartet, Port {port}")

    def call(method, path, body=None, files=None, raw=False):
        headers = {"X-Redaction-Session": TOKEN}
        data = None
        if files:
            boundary = "----messung"
            parts = []
            for name, value in (body or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            fname, content = files
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                         f'Content-Type: application/pdf\r\n\r\n'.encode() + content + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            data = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=600) as res:
            content = res.read()
        return content if raw else json.loads(content or b"null")

    def wait_done(doc_id):
        while True:
            p = call("GET", f"/documents/{doc_id}/progress")
            if p["state"] in ("done", "error", "cancelled"):
                return p
            time.sleep(0.3)

    try:
        while not call("GET", "/status")["ready"]:
            time.sleep(0.5)
        status = call("GET", "/status")
        step(f"Schnell-KI bereit; Netzwerksperre aktiv: {status['offline_guard']}; Texterkennung: {status['ocr']}; "
             f"Gründliche KI verfügbar: {status['modes']['ai']['available']}")

        mode = "ai" if status["modes"]["ai"]["available"] else "fast"
        doc = call("POST", "/documents", body={"mode": mode}, files=("brief.pdf", letter_pdf()))
        progress = wait_done(doc["document_id"])
        doc_id = doc["document_id"]
        marks = call("GET", f"/documents/{doc_id}/marks")
        step(f"Analyse Modus {mode}: {progress['state']}, {len(marks['marks'])} Vorschläge")
        call("GET", f"/documents/{doc_id}/pages/0/image?zoom=1.5", raw=True)
        for m in marks["marks"]:
            if m["status"] == "suggested":
                call("PATCH", f"/documents/{doc_id}/marks/{m['id']}", {"status": "confirmed"})
        call("POST", f"/documents/{doc_id}/review/confirm")
        res = call("POST", f"/documents/{doc_id}/finalize", {"mode": "redact"})
        (out_dir / "geschwaerzt.pdf").write_bytes(call("GET", f"/documents/{doc_id}/result/download", raw=True))
        step(f"Geschwärzt, Prüfung bestanden: {res['verification']['passed']}")
        call("POST", f"/documents/{doc_id}/review/confirm")
        res = call("POST", f"/documents/{doc_id}/finalize", {"mode": "pseudonymize"})
        (out_dir / "pseudonymisiert.pdf").write_bytes(call("GET", f"/documents/{doc_id}/result/download", raw=True))
        (out_dir / "schluessel.json").write_bytes(call("GET", f"/documents/{doc_id}/result/mapping-download", raw=True))
        step(f"Pseudonymisiert, Prüfung bestanden: {res['verification']['passed']}")

        session_dirs = sorted(p.name for p in (temp_dir / "Schwaerzsiegel").iterdir())
        temp_files_during = sorted(str(p.relative_to(temp_dir)) for p in temp_dir.rglob("*") if p.is_file())

        doc = call("POST", "/documents", body={"mode": "fast"}, files=("scan.pdf", scan_pdf()))
        progress = wait_done(doc["document_id"])
        scan_id = doc["document_id"]
        scan_marks = call("GET", f"/documents/{scan_id}/marks")["marks"]
        step(f"Scan mit Texterkennung: {progress['state']}, {len(scan_marks)} Vorschläge "
             f"(ganzseitiges Bild vorausgewählt: {any(m['source'] == 'image_heuristik' and m['default_selected'] for m in scan_marks)})")
        call("DELETE", f"/documents/{scan_id}")
        step("Dokument geschlossen")
        temp_files_after_close = sorted(str(p.relative_to(temp_dir)) for p in temp_dir.rglob("*") if p.is_file())
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
            step(f"Sidecar beendet (Code {proc.returncode})")
        except subprocess.TimeoutExpired:
            proc.kill()
            step("Sidecar musste hart beendet werden")
        monitor.stop.set()
        monitor.join(timeout=5)
    duration = time.time() - started

    temp_files_after_exit = sorted(str(p.relative_to(temp_dir)) for p in temp_dir.rglob("*") if p.is_file())
    temp_files_after_exit += sorted(f"{p.relative_to(temp_dir)} (Sitzungsordner)"
                                    for p in (temp_dir / "Schwaerzsiegel").glob("sitzung-*"))
    data_files = sorted((str(p.relative_to(data_dir)), p.stat().st_size) for p in data_dir.rglob("*") if p.is_file())

    # Stehen Inhalte der Testdokumente in den bleibenden Dateien?
    findings = []
    for rel, _ in data_files:
        path = data_dir / rel
        if path.suffix == ".db":
            with sqlite3.connect(path) as conn:
                text = "\n".join(" ".join(map(str, r)) for r in conn.execute("SELECT * FROM audit_log"))
        else:
            text = path.read_bytes().decode("utf-8", "replace")
        findings += [f"{rel}: „{s}“" for s in SECRETS if s in text]
    log_text = (data_dir / "sidecar.log").read_text(encoding="utf-8")
    blocked = [line for line in log_text.splitlines() if "[Offline]" in line]

    remote = {k: v for k, v in monitor.seen.items() if not is_local(k[3])}
    listening = {k: v for k, v in monitor.seen.items() if k[4] == "LISTEN"}
    lines = [
        "Netzwerk- und Dateimessung Erkennungsdienst (Phase 10)",
        f"Datum: {datetime.now():%d.%m.%Y %H:%M}, Dauer {duration:.0f} s, {monitor.samples} Abfragen der Verbindungstabelle",
        f"Python {sys.version.split()[0]}, psutil {psutil.__version__}, Windows {sys.getwindowsversion().major}.{sys.getwindowsversion().build}",
        "",
        "Ablauf:", *[f"  {x}" for x in log],
        "",
        "Beobachtete Prozesse:", *[f"  {p}" for p in sorted(monitor.processes)],
        "",
        "Alle beobachteten Netzwerk-Endpunkte (Prozess, Art, lokal, entfernt, Zustand, Anzahl Beobachtungen):",
        *[f"  {k[0]:<22} {k[1]} {v['example']:<40} {k[4]:<12} {v['count']}" for k, v in sorted(monitor.seen.items())],
        "",
        f"Lauschende Endpunkte: {', '.join(sorted({v['example'].split(' ')[0] for v in listening.values()})) or 'keine'}",
        f"Verbindungen zu Zielen außerhalb dieses Rechners: {len(remote)}",
        *[f"  {k} {v}" for k, v in remote.items()],
        f"Von der Netzwerksperre verweigerte Versuche (Log): {len(blocked)}",
        *[f"  {b}" for b in blocked],
        "",
        f"Sitzungsordner während der Arbeit: {', '.join(session_dirs)}",
        "Temporäre Dateien während der Arbeit (nach Schwärzen und Pseudonymisieren):",
        *[f"  {f}" for f in temp_files_during],
        "Temporäre Dateien nach dem Schließen des Dokuments:", *([f"  {f}" for f in temp_files_after_close] or ["  keine"]),
        "TEMP-Ordner nach dem Beenden:", *([f"  {f}" for f in temp_files_after_exit] or ["  leer"]),
        "",
        "Bleibende Dateien im Datenordner:", *[f"  {f} ({s} Bytes)" for f, s in data_files],
        f"Inhalte der Testdokumente in diesen Dateien: {len(findings)}", *[f"  {f}" for f in findings],
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    ok = not remote and not blocked and not findings and not temp_files_after_exit
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
