"""
Vorab-Prüfung für build-sidecar.cmd (neu in v32): zeigt, welche Pakete
aus dem globalen Python in die .exe wandern, und warnt vor GPU-Paketen.

PyInstaller packt genau das ein, was im normalen Python installiert ist.
Mit den CUDA-Paketen (siehe optionaler GPU-Schritt in der v31-Anleitung)
wird der Ordner sehr groß, und llama-cpp mit CUDA startet auf PCs ohne
NVIDIA-Treiber womöglich gar nicht.
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
warnings = []
missing = []

print(f"Python: {sys.version.split()[0]} ({sys.executable})")

for module in ("fastapi", "uvicorn", "fitz", "pytesseract", "gliner", "llama_cpp", "torch", "PyInstaller"):
    if importlib.util.find_spec(module) is None:
        missing.append(module)

if missing:
    print("FEHLER: Diese Pakete fehlen im globalen Python: " + ", ".join(missing))
    print("        Bitte zuerst:  pip install -r requirements.txt pyinstaller")
    sys.exit(1)

import torch  # noqa: E402

cuda = torch.version.cuda
print(f"PyTorch: {torch.__version__} ({'CUDA ' + cuda if cuda else 'nur CPU'})")
if cuda:
    warnings.append(
        "PyTorch ist die GPU-Version (CUDA). Die .exe wird dadurch um ca. 2-3 GB groesser.\n"
        "  Sie laeuft trotzdem auch auf PCs ohne NVIDIA-Karte (dann auf der CPU)."
    )

try:
    from llama_cpp import llama_supports_gpu_offload
    llama_gpu = bool(llama_supports_gpu_offload())
except Exception:
    llama_gpu = False
print(f"llama-cpp-python: {'GPU (CUDA)' if llama_gpu else 'nur CPU'}")
if llama_gpu:
    warnings.append(
        "llama-cpp-python ist die GPU-Version (CUDA). Diese .exe startet auf PCs ohne\n"
        "  NVIDIA-Treiber moeglicherweise nicht. Fuer die Beta-Version vor dem Bauen auf\n"
        "  die CPU-Version wechseln (siehe Anleitung, Abschnitt 'Beta-Build nur CPU')."
    )

gguf = list((HERE / "models").glob("*.gguf"))
print("Qwen-Modell: " + (gguf[0].name if gguf else "FEHLT in sidecar\\models"))
if not gguf:
    warnings.append("Keine .gguf-Datei in sidecar\\models - die .exe startet, aber ohne Kontextpruefung (LLM).")

hf_cache = HERE / "models" / "hf-cache"
print("GLiNER offline: " + ("ja" if hf_cache.is_dir() else "nein (download_models.cmd fehlt noch)"))
if not hf_cache.is_dir():
    warnings.append("sidecar\\models\\hf-cache fehlt - die .exe laedt GLiNER dann aus dem Internet.")

tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
print("Tesseract: " + (str(tesseract.parent) if tesseract.exists() else "nicht unter C:\\Program Files\\Tesseract-OCR gefunden"))
if not tesseract.exists():
    warnings.append("Tesseract nicht gefunden - gescannte PDFs (OCR) funktionieren in der .exe dann nicht.")

if warnings:
    print()
    for w in warnings:
        print("WARNUNG: " + w)
print()
