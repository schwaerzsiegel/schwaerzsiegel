"""
Kürzt den PyInstaller-Ordner vor dem Installer-Bau (neu in v35).

Der Windows-Installer (NSIS/makensis) kann keine Dateien mit Pfaden über
260 Zeichen lesen. PyTorch bringt in seinen Paket-Metadaten
(torch-*.dist-info/licenses/third_party/...) Lizenztexte in extrem tief
verschachtelten Ordnern mit - daran ist der v34-Build abgebrochen.

1. In jedem *.dist-info/licenses bleiben die Dateien direkt darin
   erhalten; nur die tiefer verschachtelten Unterordner werden entfernt.
   Zur Laufzeit liest diese Dateien niemand.
2. Danach wird geprüft, ob noch irgendein Pfad zu lang ist - so wie
   makensis ihn sieht (über src-tauri\\..\\sidecar\\dist\\...). Falls ja, bricht
   das Skript mit einer verständlichen Meldung ab, statt erst in makensis.
"""

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist" / "redaction-sidecar"
# So steht der Pfad im Installer-Skript von Tauri (resources in tauri.release.conf.json)
NSIS_PREFIX = os.path.join(str(HERE.parent), "src-tauri", "..", "sidecar", "dist", "redaction-sidecar") + os.sep
MAX_PATH = 259

if not DIST.is_dir():
    print(f"FEHLER: {DIST} fehlt - zuerst build-sidecar.cmd ausfuehren.")
    sys.exit(1)

removed = 0
for licenses in DIST.glob("_internal/*.dist-info/licenses"):
    for sub in licenses.iterdir():
        if sub.is_dir():
            removed += sum(1 for f in sub.rglob("*") if f.is_file())
            shutil.rmtree(sub)
print(f"  {removed} tief verschachtelte Lizenzdateien entfernt.")

lengths = sorted(
    ((len(NSIS_PREFIX + str(f.relative_to(DIST))), NSIS_PREFIX + str(f.relative_to(DIST)))
     for f in DIST.rglob("*") if f.is_file()),
    reverse=True,
)
too_long = [item for item in lengths if item[0] > MAX_PATH]
if too_long:
    print(f"FEHLER: {len(too_long)} Pfade sind laenger als {MAX_PATH} Zeichen, z.B.:")
    for length, path in too_long[:5]:
        print(f"  ({length}) {path}")
    print("  Abhilfe: den Projektordner naeher an C:\\ legen (z.B. C:\\ra) und neu starten.")
    sys.exit(1)
print(f"  Laengster Pfad: {lengths[0][0]} Zeichen (Grenze {MAX_PATH}) - OK.")
