"""
Kleines, quantisiertes lokales LLM ausschließlich für die Kontextprüfung
unsicherer Erkennungstreffer (Modus "Gründliche KI") - keine Erkennung neuer
Fundstellen, kein Fließtext, kurze, strukturierte Antworten.

Phase 3:
- Modellwahl: die erste vorhandene Datei aus KNOWN_MODELS (größtes zuerst)
  oder REDACTION_LLM_MODEL=<Dateiname>. Läuft vollständig lokal
  (llama-cpp-python, GGUF), es gibt keinen Download-Pfad.
- Vor dem Laden wird geprüft, ob genug Arbeitsspeicher frei ist - sonst eine
  verständliche Meldung statt eines Absturzes.
- Antwortformat per Grammatik erzwungen (JSON-Schema): das Modell kann nur
  gültiges JSON erzeugen. Das schaltet nebenbei den "Denkmodus" von Qwen3 ab.
- Fortschritt und Abbruch zwischen den einzelnen Aufrufen.
"""

import json
import os
import re
import threading
from pathlib import Path

from llama_cpp import Llama

from core.paths import MODELS_DIR

# Bevorzugte Reihenfolge (größtes zuerst)
KNOWN_MODELS = [
    {"file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "name": "Qwen3 4B Instruct", "hybrid_thinking": False},
    {"file": "Qwen3-1.7B-Q4_K_M.gguf", "name": "Qwen3 1.7B", "hybrid_thinking": True},
]
_N_CTX = 2048
_RAM_RESERVE_GB = 0.8  # Kontextspeicher + Puffer über die Dateigröße hinaus

_llm = None
_device_label = ""
_loaded_info: dict | None = None
_llm_lock = threading.Lock()

# Wie viele Zeichen Text links und rechts vom Kandidaten das LLM als
# Kontext bekommt (nicht das ganze Dokument: schneller und gezielter).
_CONTEXT_CHARS = 200

_SYSTEM_PROMPT = (
    "Du prüfst Fundstellen aus deutschen Dokumenten. Beantworte die gestellte Frage nur anhand des "
    "Textausschnitts. Schreibe zuerst eine sehr kurze Begründung, dann die Zuordnung:\n"
    "Privatperson = gehört zu einer bestimmten Person (Name, private Kontaktdaten, Geburtsdatum, "
    "persönliche Kennnummer, ihre Krankheit).\n"
    "Firma oder Behörde = Firma, Behörde, Einrichtung, Produkt, Service- oder Zentralnummer.\n"
    "Allgemein = Brief-, Termin- oder Fristdatum, Bestell-, Rechnungs-, Vorgangs- oder Aktennummer, "
    "allgemeiner Begriff ohne Bezug zu einer bestimmten Person."
)
# Phase 3: konkrete Frage je Kategorie statt einer allgemeinen - kleine
# Modelle urteilen damit deutlich treffsicherer.
_QUESTIONS = {
    "Datum": "Ist das Datum das Geburtsdatum einer Person (Privatperson) oder ein Brief-, Termin- oder Fristdatum (Allgemein)?",
    "Geburtsdatum": "Ist das Datum das Geburtsdatum einer Person (Privatperson) oder ein anderes Datum (Allgemein)?",
    "Telefonnummer": "Wem gehört die Nummer: einer bestimmten Person oder einer Firma/Behörde (Service, Zentrale)?",
    "Organisation": "Ist das eine Privatperson oder eine Firma, Behörde oder Einrichtung?",
    "Gesundheitsdaten": "Geht es um die Krankheit einer bestimmten Person (Privatperson) oder um etwas Allgemeines?",
}
_DEFAULT_QUESTION = "Gehört diese Angabe zu einer bestimmten Privatperson?"
_USER_TEMPLATE = 'Textausschnitt: "{context}"\nFundstelle: "{candidate}" (erkannt als {label})\nFrage: {question}'
# Phase 3: Das 1,7B-Modell setzte ein Ja/Nein-Urteil oft gegen die eigene,
# richtige Begründung ("Service-Hotline der Firma" -> schwärzen). Eine
# Zuordnung (wem gehört die Angabe?) liegt näher an der Begründung; ob
# geschwärzt wird, leitet der Code ab.
OWNER_PRIVATE, OWNER_ORGANISATION, OWNER_GENERAL = "Privatperson", "Firma oder Behörde", "Allgemein"
# Wenige Beispiele als vorherige Dialogrunden (synthetisch, nicht aus dem Messkorpus)
_EXAMPLES = [
    ('Textausschnitt: "Bei Fragen erreichen Sie unser Kundencenter unter 0800 555 1234 (kostenfrei)."\n'
     'Fundstelle: "0800 555 1234" (erkannt als Telefonnummer)\nFrage: ' + _QUESTIONS["Telefonnummer"],
     '{"reason": "Kostenlose Nummer eines Kundencenters", "owner": "Firma oder Behörde"}'),
    ('Textausschnitt: "Für Rückfragen bin ich abends unter 0152 7788990 zu erreichen. Gruß, Petra"\n'
     'Fundstelle: "0152 7788990" (erkannt als Telefonnummer)\nFrage: ' + _QUESTIONS["Telefonnummer"],
     '{"reason": "Mobilnummer von Petra", "owner": "Privatperson"}'),
    ('Textausschnitt: "Frau Albrecht, geboren 04.11.1971, beantragt Elternzeit."\n'
     'Fundstelle: "04.11.1971" (erkannt als Datum)\nFrage: ' + _QUESTIONS["Datum"],
     '{"reason": "Geburtsdatum von Frau Albrecht", "owner": "Privatperson"}'),
    ('Textausschnitt: "Kiel, 03.02.2026. Sehr geehrte Damen und Herren, anbei die Unterlagen."\n'
     'Fundstelle: "03.02.2026" (erkannt als Datum)\nFrage: ' + _QUESTIONS["Datum"],
     '{"reason": "Datum des Briefes", "owner": "Allgemein"}'),
]
_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 80},
        "owner": {"type": "string", "enum": [OWNER_PRIVATE, OWNER_ORGANISATION, OWNER_GENERAL]},
    },
    "required": ["reason", "owner"],
}

# Bei diesen Kategorien darf das LLM eine Erkennung NICHT herunterstufen,
# nur bestätigen/anheben. Beobachtet: das 4B-Modell stuft freistehende
# Adressen ("Musterweg 12") ohne direkt danebenstehenden Namen zu leicht
# als "nicht personenbezogen" ein, obwohl eine vollständige Anschrift
# nach DSGVO praktisch immer personenbezogen ist, sobald das Dokument
# insgesamt einer identifizierbaren Person zuzuordnen ist. Hier wiegt ein
# übersehener echter Treffer (False Negative) schwerer als ein unnötig
# vorgeschlagener (False Positive) - deshalb bewusst konservativ.
_NO_VETO_LABELS = {"Person", "Adresse"}
# Begründungen, die auf eine bestimmte Person hindeuten (Widerspruchsprüfung)
_PERSONAL_HINT = re.compile(r"(?i)geburt|privat|persönlich|\bvon (frau|herrn?)\b|mobilnummer von|handynummer von"
                            r"|des kollegen|der kollegin|des patienten|der patientin|des mitarbeiters|der mitarbeiterin")


class Cancelled(Exception):
    """Die Analyse wurde vom Benutzer abgebrochen."""


def _gpu_offload_supported() -> bool:
    """
    True nur, wenn llama-cpp-python mit GPU-Unterstützung (z.B. CUDA)
    installiert wurde. Die normale pip-Installation ist CPU-only - dann
    ist das hier False und es läuft wie bisher auf der CPU. So läuft die
    App auf jedem Rechner, mit oder ohne NVIDIA-Karte.
    """
    try:
        from llama_cpp import llama_supports_gpu_offload
        return bool(llama_supports_gpu_offload())
    except Exception:
        return False


def _select_model() -> dict | None:
    wanted = os.environ.get("REDACTION_LLM_MODEL")
    candidates = [m for m in KNOWN_MODELS if not wanted or m["file"] == wanted]
    if wanted and not candidates:
        candidates = [{"file": wanted, "name": Path(wanted).stem, "hybrid_thinking": False}]
    for m in candidates:
        path = MODELS_DIR / m["file"]
        if path.is_file():
            size_gb = path.stat().st_size / 1e9
            return {**m, "path": path, "size_gb": round(size_gb, 2),
                    "ram_needed_gb": round(size_gb + _RAM_RESERVE_GB, 1)}
    return None


def _available_ram_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:
        return None


def model_info() -> dict:
    """Für die Statusanzeige: welches Modell, vorhanden, genug RAM?"""
    info = _loaded_info or _select_model()
    ram = _available_ram_gb()
    ram_text = round(ram, 1) if ram is not None else None
    if info is None:
        return {"available": False, "file": None, "name": None, "size_gb": None, "ram_needed_gb": None,
                "ram_available_gb": ram_text,
                "problem": f"Kein Sprachmodell in {MODELS_DIR} gefunden "
                           f"(erwartet: {' oder '.join(m['file'] for m in KNOWN_MODELS)})."}
    enough = _llm is not None or ram is None or ram >= info["ram_needed_gb"]
    return {"available": True, "file": info["file"], "name": info["name"], "size_gb": info["size_gb"],
            "ram_needed_gb": info["ram_needed_gb"], "ram_available_gb": ram_text,
            "problem": None if enough else
            f"Zu wenig freier Arbeitsspeicher: {ram:.1f} GB frei, {info['ram_needed_gb']} GB nötig. "
            "Andere Programme schließen oder den Modus „Schnell-KI“ verwenden."}


def load_model() -> str:
    """Lädt das LLM (falls noch nicht geschehen) und gibt das Gerät zurück."""
    global _llm, _device_label, _loaded_info
    with _llm_lock:
        if _llm is None:
            info = model_info()
            if not info["available"] or info["problem"]:
                raise RuntimeError(info["problem"])
            selected = _select_model()
            use_gpu = _gpu_offload_supported()
            # Physische Kerne statt aller Threads - Hyperthreads bringen bei
            # llama.cpp kaum etwas, cpu_count() zählt sie aber mit.
            threads = max(1, (os.cpu_count() or 2) // 2)
            try:
                _llm = Llama(
                    model_path=str(selected["path"]),
                    n_ctx=_N_CTX,
                    n_threads=threads,
                    n_gpu_layers=-1 if use_gpu else 0,  # -1 = alle Schichten auf die GPU
                    verbose=False,
                )
            except MemoryError as e:
                raise RuntimeError("Zu wenig Arbeitsspeicher, um das Sprachmodell zu laden.") from e
            _loaded_info = selected
            _device_label = f"{selected['name']}, " + ("GPU" if use_gpu else f"CPU ({threads} Threads)")
    return _device_label


def unload_model() -> bool:
    global _llm, _loaded_info, _device_label
    with _llm_lock:
        if _llm is None:
            return False
        try:
            _llm.close()
        except Exception:
            pass
        _llm = None
        _loaded_info = None
        _device_label = ""
    import gc
    gc.collect()
    return True


def _get_llm() -> Llama:
    load_model()
    return _llm


def _context_for(full_text: str, suggestion: dict) -> str:
    span = suggestion.get("_span")
    if span is None:
        pos = full_text.find(suggestion["text"])
        span = (pos, pos + len(suggestion["text"])) if pos >= 0 else (0, 0)
    start = max(0, span[0] - _CONTEXT_CHARS)
    end = min(len(full_text), span[1] + _CONTEXT_CHARS)
    return " ".join(full_text[start:end].split())


def _parse_response(raw: str) -> dict | None:
    """Robust parsen (Denkblock, Codefences, abgeschnittene Antworten)."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned.strip(), flags=re.I | re.M).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if data.get("owner") in (OWNER_PRIVATE, OWNER_ORGANISATION, OWNER_GENERAL):
        data["redact"] = data["owner"] == OWNER_PRIVATE
    if not isinstance(data.get("redact"), bool):
        return None
    return data


def build_messages(full_text: str, suggestion: dict) -> list[dict]:
    no_think = "\n/no_think" if (_loaded_info or {}).get("hybrid_thinking") else ""
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for question, answer in _EXAMPLES:
        messages += [{"role": "user", "content": question + no_think}, {"role": "assistant", "content": answer}]
    user = _USER_TEMPLATE.format(label=suggestion["label"], candidate=suggestion["text"],
                                 context=_context_for(full_text, suggestion),
                                 question=_QUESTIONS.get(suggestion["label"], _DEFAULT_QUESTION))
    messages.append({"role": "user", "content": user + no_think})
    return messages


def _ask(llm: Llama, messages: list[dict]) -> str:
    response = llm.create_chat_completion(
        messages=messages,
        response_format={"type": "json_object", "schema": _SCHEMA},
        temperature=0,
        max_tokens=60,
    )
    return response["choices"][0]["message"]["content"] or ""


def disambiguate(full_text: str, ambiguous_suggestions: list[dict], cache: dict | None = None, timer=None,
                 on_progress=None, should_cancel=None) -> None:
    """
    cache: pro Dokument geteilt (über alle Seiten). Derselbe Kandidat mit
    demselben Label (z.B. dieselbe E-Mail-Adresse in der Fußzeile jeder
    Seite) wird nur einmal ans LLM geschickt.
    on_progress(i, n): Fortschritt; should_cancel(): True -> Cancelled.
    """
    llm = _get_llm()
    if cache is None:
        cache = {}
    total = len(ambiguous_suggestions)
    for i, suggestion in enumerate(list(ambiguous_suggestions)):
        if should_cancel and should_cancel():
            raise Cancelled()
        if on_progress:
            on_progress(i + 1, total)
        key = (suggestion["text"].strip().casefold(), suggestion["label"])
        if key in cache:
            raw = cache[key]
            if timer:
                timer.count("LLM aus Cache")
        else:
            try:
                raw = _ask(llm, build_messages(full_text, suggestion))
            except MemoryError:
                raw = ""
            cache[key] = raw
            if timer:
                timer.count("LLM-Aufrufe")
        apply_answer(suggestion, _parse_response(raw))


def apply_answer(suggestion: dict, parsed: dict | None) -> None:
    """Überträgt das Urteil auf den Vorschlag (konservativ, siehe _NO_VETO_LABELS)."""
    if parsed is None:
        # Kein auswertbares JSON -> Vorschlag bleibt mit ursprünglicher
        # (mittlerer) Confidence bestehen; lieber im Review anzeigen
        # als durch einen Parse-Fehler stillschweigend verlieren.
        suggestion["llm_reviewed"] = False
        suggestion["llm_reason"] = "KI-Antwort nicht auswertbar – bitte selbst prüfen"
        return

    suggestion["llm_reviewed"] = True
    suggestion["llm_reason"] = f"KI-Prüfung: {parsed.get('reason', '')}".strip()
    if not parsed.get("redact", True):
        if _PERSONAL_HINT.search(parsed.get("reason", "")):
            # Begründung und Urteil widersprechen sich (gemessen beim 1,7B-Modell:
            # "Geburtstag des Kollegen ..." + "Allgemein"). In der riskanten
            # Richtung (Abstufen) nichts ändern, sondern zur Prüfung markieren.
            suggestion["llm_reviewed"] = False
            suggestion["llm_reason"] = (f"KI-Antwort widersprüchlich („{parsed.get('reason', '')}“) – "
                                        "bitte selbst prüfen")
        elif suggestion["label"] in _NO_VETO_LABELS:
            # Bewusst KEIN Downgrade bei Person/Adresse - die Begründung
            # bleibt sichtbar, Confidence/Vorauswahl bleiben unverändert.
            suggestion["llm_reason"] = (
                f"KI-Prüfung: {parsed.get('reason', '')} (bei {suggestion['label']} wird eine "
                "KI-Ablehnung aus Vorsicht nicht übernommen – bitte selbst prüfen)"
            )
        else:
            # Nicht löschen, sondern abstufen und abwählen: bleibt sichtbar.
            suggestion["confidence"] = min(suggestion["confidence"], 0.15)
            suggestion["default_selected"] = False
    else:
        # Die Sicherheitsangabe kleiner Modelle ist nicht aussagekräftig
        # (gemessen: immer 0,95) - es zählt nur das Urteil. Bestätigt wird
        # auf "eher sicher" (0,8, weiter "zu prüfen" unter 0,85) und
        # vorausgewählt; ein generisches "Datum" wird so zum Geburtsdatum-
        # Kandidaten, bleibt aber in der Sammelbestätigung außen vor.
        suggestion["confidence"] = max(suggestion["confidence"], 0.8)
        suggestion["default_selected"] = True
        if suggestion["label"] == "Datum":
            # Die Frage für "Datum" lautet "Geburtsdatum?" - Kategorie nachziehen
            suggestion.setdefault("also_detected_as", []).append(
                {"label": "Datum", "source": suggestion.get("source", ""), "confidence": suggestion["confidence"]})
            suggestion["label"] = "Geburtsdatum"
