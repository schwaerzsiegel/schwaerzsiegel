# PyInstaller-Bauplan für den Sidecar (neu in v32).
# Aufruf über build-sidecar.cmd, nicht direkt nötig.
#
# Ergebnis: dist\redaction-sidecar\redaction-sidecar.exe plus Ordner _internal
# mit Python und allen Paketen. Bewusst ein Ordner statt einer einzelnen
# .exe ("onefile"): die müsste bei jedem Start erst mehrere GB in einen
# Temp-Ordner entpacken.
#
# models\ (Qwen-.gguf) und vendor\tesseract\ legt build-sidecar.cmd danach
# neben die .exe - die gehören nicht in _internal, damit man sie ohne
# Neubau austauschen kann.

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Pakete mit Daten-/DLL-Dateien oder dynamischen Importen, die PyInstaller
# nicht von allein vollständig erkennt.
for package in ("gliner", "llama_cpp", "fitz", "pymupdf"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

# transformers prüft beim Import die Versionen dieser Pakete über ihre
# Metadaten - ohne die bricht der Start mit "PackageNotFoundError" ab.
for package in (
    "transformers", "tokenizers", "huggingface-hub", "safetensors", "tqdm",
    "regex", "requests", "packaging", "filelock", "numpy", "pyyaml", "torch",
    "sentencepiece", "gliner",
):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass  # Paket nicht installiert - dann wird es auch nicht gebraucht


hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "multipart",
    "python_multipart",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Nicht benötigte Schwergewichte, die sonst mitgezogen würden, falls sie
    # im globalen Python installiert sind (ultralytics/YOLO wird seit der
    # schlanken Bilderkennung in pdf_processor.py nicht mehr verwendet).
    # torchvision/torchaudio: werden nicht gebraucht (torchvision kam nur
    # über ultralytics). PyInstaller bündelt torchvision ohne seine native
    # Erweiterung _C_stable.pyd; transformers importiert es trotzdem
    # optional, und der Start bricht ab mit "operator torchvision::nms does
    # not exist" -> "Could not import module 'ModernBertModel'" (GLiNER).
    excludes=[
        "ultralytics", "torchvision", "torchaudio",
        "tkinter", "matplotlib", "IPython", "jupyter", "notebook",
        "tensorflow", "keras", "jax", "flax", "pytest",
    ],
    noarchive=False,
)

# Visual-C++-Laufzeit: PyInstaller nimmt msvcp140.dll/vcruntime140*.dll
# aus dem Python-Ordner (dort Version 14.26 von 2020). PyTorch 2.x braucht
# eine neuere Laufzeit - mit der alten schlägt das Laden von c10.dll mit
# "WinError 1114" fehl. Deshalb die aktuelle Version aus System32 bündeln
# (sofern dort neuer), damit die .exe auch auf PCs ohne bzw. mit älterem
# VC++-Redistributable funktioniert.
import ctypes
import os

def _file_version(path):
    """Dateiversion einer DLL als Tupel, (0, 0, 0, 0) wenn unbekannt."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    buf = ctypes.create_string_buffer(size)
    ptr, length = ctypes.c_void_p(), ctypes.c_uint()
    if not size or not ver.GetFileVersionInfoW(path, 0, size, buf) or not ver.VerQueryValueW(
        buf, "\\", ctypes.byref(ptr), ctypes.byref(length)
    ):
        return (0, 0, 0, 0)
    ms, ls = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents[2:4]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)

_VC_RUNTIME = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
               "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
               "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"}
_system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
_binaries = []
for _dest, _src, _kind in a.binaries:
    _name = os.path.basename(_dest).lower()
    _newer = os.path.join(_system32, os.path.basename(_dest))
    if (_name in _VC_RUNTIME and os.path.dirname(_dest) in ("", ".")
            and os.path.isfile(_newer) and _file_version(_newer) > _file_version(_src)):
        print(f"VC-Laufzeit: {_name} {_file_version(_src)} -> {_file_version(_newer)} (System32)")
        _src = _newer
    _binaries.append((_dest, _src, _kind))
a.binaries = _binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="redaction-sidecar",
    # Mit Konsolenfenster beim Doppelklick (praktisch für die Beta: man sieht
    # das Log). Aus der Tauri-App heraus wird es ohne Fenster gestartet.
    console=True,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="redaction-sidecar",
)
