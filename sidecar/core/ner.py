"""
Hybride Erkennung, komplett lokal (Phase 2):

  1. Regeln mit Kontext, Struktur und Prüfziffern (core/rules.py)
  2. GLiNER mit deutschen Kategorien - Treffer werden validiert
     (eine "Telefonnummer" ohne Ziffern wird verworfen) und von
     Feldbezeichnungen ("Telefon:", "Name:") befreit
  3. Konfliktauflösung mit fester Rangfolge: Kontext > Struktur/Prüfziffer
     > KI-Modell > reines Format. "Personalnummer: 104827" wird deshalb
     Personalnummer, auch wenn das Format einer Telefonnummer ähnelt.
  4. Optional: lokales LLM nur für unsichere Fälle (Modus "Gründlich").

Keine Cloud-Anbindung (siehe MVP-Review: die frühere "Waterfall"-Integration
sendete Text an app.anymize.ai - das entfällt).

bbox-Auflösung folgt dem Wort-ID-Ansatz aus dem MVP: Entitäten werden als
Menge von Wort-IDs beschrieben, deren bbox vereinigt wird - robuster als
eine Zeichen-Offset-zu-Koordinate-Umrechnung.
"""

import threading
from contextlib import nullcontext
from uuid import uuid4

# Vor gliner importieren: setzt bei vorhandenem models/hf-cache den Offline-Modus.
from core.paths import GLINER_MODEL_ID, HF_CACHE_DIR

from core import spm_paths  # noqa: E402,F401  Phase 7: Umlaute im Modellpfad (vor gliner)
from gliner import GLiNER  # noqa: E402

from core import rules, validators, vorschlagsregeln  # noqa: E402

_GERMAN_LABELS = [
    "Person", "Adresse", "IBAN", "Sozialversicherungsnummer",
    "Steuer-ID", "Aktenzeichen", "KFZ-Kennzeichen", "Telefonnummer",
    # "Geburtsort" bewusst nicht: verschlechterte im Test die Namenserkennung
    # ("Max Beispiel" -> nur "Max"); Geburtsorte findet die Kontextregel.
    "E-Mail", "Geburtsdatum",
    # Besondere Kategorien nach Art. 9 DSGVO (analog zu Schwärzwerk) -
    # GLiNER erkennt diese per Zero-Shot über die Label-Beschreibung,
    # kein Fine-Tuning nötig, um sie probeweise aufzunehmen.
    "Gesundheitsdaten", "Religionszugehörigkeit", "Sexuelle Orientierung",
    "Politische Meinung", "Gewerkschaftszugehörigkeit", "Ethnische Herkunft",
    # Organisation und Datum/Betrag sind standardmäßig nicht vorausgewählt,
    # weil sonst z.B. die ausstellende Behörde mitverschwindet
    # (rules.DEFAULT_OFF_LABELS).
    "Organisation", "Datum", "Betrag",
    # Ausweisnummer ist allgemeiner als Steuer-ID/Sozialversicherungsnummer
    # (z.B. Personalausweis-/Reisepassnummer). Mit Beschriftung findet sie
    # auch eine Regel (rules.CONTEXT_RULES), ohne nur GLiNER.
    "Ausweisnummer",
]

# Phase 2: GLiNER-Treffer dieser Kategorien müssen die Strukturprüfung
# bestehen - sonst ist es fast immer eine Feldbezeichnung oder eine andere
# Kennung (gemessen: "BIC" als IBAN, "Telefon" als Telefonnummer,
# Krankenversicherten- als Sozialversicherungsnummer).
_MODEL_VALIDATORS = {
    "Telefonnummer": validators.phone,
    "E-Mail": validators.email,
    "IBAN": validators.iban,
    "Steuer-ID": validators.steuer_id,
    "Sozialversicherungsnummer": validators.sv_nummer,
    "Ausweisnummer": lambda s: any(c.isdigit() for c in s) and len(validators.compact(s)) >= 6,
    "Geburtsdatum": lambda s: any(c.isdigit() for c in s),
    # Phase 5: Aktenzeichen und Kennzeichen enthalten immer Ziffern - sonst
    # markierte GLiNER bei Fließtext Wörter wie "Antrag" (gemessen: 11 pro Seite)
    "Aktenzeichen": lambda s: any(c.isdigit() for c in s),
    "KFZ-Kennzeichen": lambda s: any(c.isdigit() for c in s),
    # Phase 17b: Rollen, Anreden und Pronomen ("Beschäftigter", "Herrn", "er") sind keine Namen
    "Person": lambda s: not any(c.isdigit() for c in s) and "@" not in s and rules.plausible_person(s),
    "Adresse": rules.plausible_address,
    "Geburtsort": lambda s: not any(c.isdigit() for c in s),
}
_MODEL_THRESHOLD = 0.4
# Kategorien, bei denen die Kontextprüfung nichts beitragen kann
_NOT_FOR_LLM = {"Betrag", "Service-Rufnummer", "Firmenanschrift"}

_model = None
_device_label = ""
_model_lock = threading.Lock()


def load_model() -> str:
    """
    Lädt GLiNER einmal (beim Sidecar-Start, siehe core/models.py) und hält
    es im Speicher. Nutzt automatisch eine NVIDIA-GPU, wenn PyTorch mit
    CUDA installiert ist und eine Karte gefunden wird - sonst die CPU.
    Gibt das verwendete Gerät zurück (für Log und Statusanzeige).
    """
    global _model, _device_label
    with _model_lock:
        if _model is None:
            import torch
            if not HF_CACHE_DIR.is_dir():
                raise RuntimeError(
                    f"Offline-Modellcache fehlt: {HF_CACHE_DIR}. "
                    "Modelle müssen vor der Installation bereitgestellt werden."
                )
            model = GLiNER.from_pretrained(GLINER_MODEL_ID)
            if torch.cuda.is_available():
                model = model.to("cuda")
                _device_label = f"GPU ({torch.cuda.get_device_name(0)})"
            else:
                _device_label = "CPU"
            # v33: in der Statusanzeige sichtbar machen, ob offline geladen wurde
            _device_label += ", offline" if HF_CACHE_DIR.is_dir() else ", aus dem Internet"
            model.eval()
            _model = model
    return _device_label


def _get_model() -> GLiNER:
    load_model()
    return _model


def _same_visual_line(prev: dict, word: dict) -> bool:
    """
    Phase 5 (Befund mit einem Formular-PDF): Beschriftung und Wert in einer
    Tabellen-/Formularzeile ("IBAN (Testwert)" | "DE.. ....") liefert die
    Textebene als zwei Zeilen. Steht das nächste Wort auf gleicher Höhe
    rechts daneben, ist es für die Regeln dieselbe Zeile. Mehrspaltiger
    Fließtext ist nicht betroffen: dort folgt eine tiefere Zeile.
    """
    if word.get("line") is None or word.get("line") == prev.get("line"):
        return True
    try:
        ax0, ay0, ax1, ay1 = prev["bbox"]
        bx0, by0, bx1, by1 = word["bbox"]
    except (KeyError, TypeError, ValueError):
        return False
    return ay0 <= (by0 + by1) / 2 <= ay1 and bx0 >= ax1 - 1


def _build_text_and_index(words: list[dict]) -> tuple[str, list[tuple[int, int, dict]]]:
    """Baut einen zusammenhängenden Text und merkt sich pro Zeichenbereich
    das zugehörige Wort (mit id + bbox aus pdf_processor/ocr). Phase 2:
    Zeilenwechsel werden als "\\n" übernommen, damit Regeln wissen, wo ein
    Feld endet ("Name: Erika Mustermann" / nächste Zeile)."""
    parts = []
    index = []
    pos = 0
    for i, w in enumerate(words):
        if i:
            parts.append(" " if _same_visual_line(words[i - 1], w) else "\n")
            pos += 1
        start = pos
        parts.append(w["text"])
        pos += len(w["text"])
        index.append((start, pos, w))
    return "".join(parts), index


def _words_in_span(index, start: int, end: int) -> list[dict]:
    return [w for (ws, we, w) in index if we > start and ws < end]


def _bbox_union(words: list[dict]) -> list[float]:
    return [
        min(w["bbox"][0] for w in words),
        min(w["bbox"][1] for w in words),
        max(w["bbox"][2] for w in words),
        max(w["bbox"][3] for w in words),
    ]


def _trim_field_words(words: list[dict]) -> list[dict]:
    """Feldbezeichnungen am Rand eines Treffers abschneiden ("Name: Erika")."""
    while words and rules.is_field_word(words[0]["text"]):
        words = words[1:]
    while words and rules.is_field_word(words[-1]["text"]):
        words = words[:-1]
    return words


def _suggestion(label, start, end, confidence, priority, reason, source, index, trim: bool = True) -> dict | None:
    words = _words_in_span(index, start, end)
    if trim:
        words = _trim_field_words(words)
    if not words:
        return None
    return {
        "id": str(uuid4()),
        "label": label,
        "text": " ".join(w["text"] for w in words),
        "bbox": _bbox_union(words),
        "word_ids": [w["id"] for w in words],
        "confidence": confidence,
        "source": source,
        "reason": reason,
        "default_selected": label not in rules.DEFAULT_OFF_LABELS,
        "_priority": priority,
        "_span": (start, end),
    }


def _rule_pass(text, index) -> list[dict]:
    out = []
    for hit in rules.all_hits(text):
        # Phase 17: beim Organisationszeichen gehört die Bezeichnung („Org.Z.:“) zum Treffer
        s = _suggestion(hit.label, hit.start, hit.end, hit.confidence, hit.priority, hit.reason, "regex", index,
                        trim=hit.label != "Organisationszeichen")
        if s:
            out.append(s)
    return out


# Phase 3: GLiNER verarbeitet höchstens 384 Token (inkl. Kategorienliste) und
# ignoriert den Rest STILLSCHWEIGEND - gemessen: ab ~370 Wörtern wurde ein
# Name am Seitenende nicht mehr gefunden. Deshalb in überlappenden
# Abschnitten von 150 Wörtern prüfen (deutsche Wörter ≈ 1,5 Token).
_CHUNK_WORDS = 150
_CHUNK_OVERLAP = 30


def _chunks(index) -> list[tuple[int, int]]:
    """Zeichenbereiche (Start, Ende) der Abschnitte im Gesamttext."""
    spans = []
    step = _CHUNK_WORDS - _CHUNK_OVERLAP
    for first in range(0, len(index), step):
        last = min(first + _CHUNK_WORDS, len(index)) - 1
        spans.append((index[first][0], index[last][1]))
        if last == len(index) - 1:
            break
    return spans


def _model_hits(text, index) -> list[dict]:
    model = _get_model()
    spans = _chunks(index)
    # Im Stapel ca. 15 % schneller als einzeln, gleiche Ergebnisse (gemessen)
    results = model.inference([text[s:e] for s, e in spans], _GERMAN_LABELS, threshold=_MODEL_THRESHOLD,
                              batch_size=8)
    hits = []
    for (start, _), chunk_hits in zip(spans, results):
        for hit in chunk_hits:
            hit["start"] += start
            hit["end"] += start
            hits.append(hit)
    return hits


def _gliner_pass(text, index) -> list[dict]:
    out = []
    for hit in _model_hits(text, index):
        value = hit["text"]
        check = _MODEL_VALIDATORS.get(hit["label"])
        if check and not check(value):
            continue
        # Zahlenfolgen nicht allein wegen ihres Formats zur Telefonnummer machen
        if hit["label"] == "Telefonnummer" and not rules.plausible_phone(text, hit["start"], hit["end"]):
            continue
        if hit["label"] == "Telefonnummer" and rules.is_service_number(value):
            continue  # die Regel ordnet sie als Service-Rufnummer ein
        s = _suggestion(hit["label"], hit["start"], hit["end"], hit["score"], rules.PRIO_MODEL,
                        "KI-Modell (GLiNER)", "gliner", index)
        if s:
            out.append(s)
    return out


def _word_order(word_id: str):
    """Reihenfolge auf der Seite: Wort-IDs haben die Form p{Seite}_w{Index}."""
    head, _, tail = word_id.rpartition("_w")
    return (0, int(tail), "") if head and tail.isdigit() else (1, 0, word_id)


def resolve_conflicts(suggestions: list[dict]) -> list[dict]:
    """
    Überlappende Treffer (gemeinsame Wörter) werden zu einer Markierung
    zusammengefasst - auch über Ketten hinweg. Die Kategorie bestimmt der
    Treffer mit dem höchsten Rang (Kontext > Struktur > Modell > Format),
    bei Gleichstand die höhere Sicherheit. Alle anderen werden unter
    "also_detected_as" vermerkt, nichts geht verloren.
    """
    parent = list(range(len(suggestions)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, s in enumerate(suggestions):
        for wid in s["word_ids"]:
            if wid in owner:
                parent[find(i)] = find(owner[wid])
            else:
                owner[wid] = i

    groups: dict[int, list[dict]] = {}
    for i, s in enumerate(suggestions):
        groups.setdefault(find(i), []).append(s)

    result = []
    for members in groups.values():
        members.sort(key=lambda s: (s["_priority"], s["confidence"]), reverse=True)
        winner = dict(members[0])
        if len(members) > 1:
            seen = {(winner["label"], winner["source"])}
            winner["also_detected_as"] = []
            for other in members[1:]:
                if (other["label"], other["source"]) in seen:
                    continue
                seen.add((other["label"], other["source"]))
                winner["also_detected_as"].append(
                    {"label": other["label"], "source": other["source"], "confidence": other["confidence"]})
            ids = set()
            for m in members:
                ids.update(m["word_ids"])
            winner["word_ids"] = sorted(ids, key=_word_order)
            winner["bbox"] = [min(m["bbox"][0] for m in members), min(m["bbox"][1] for m in members),
                              max(m["bbox"][2] for m in members), max(m["bbox"][3] for m in members)]
            winner["_span"] = (min(m["_span"][0] for m in members), max(m["_span"][1] for m in members))
        else:
            winner["also_detected_as"] = []
        result.append(winner)
    return result


def detect(words: list[dict], timer=None, llm_cache: dict | None = None, on_step=None, use_llm: bool = True,
           use_gliner: bool = True, llm_progress=None, should_cancel=None) -> list[dict]:
    """
    timer: optionaler timing.StepTimer für die Zeitmessung pro Schritt.
    llm_cache: pro Dokument geteilter Cache für LLM-Antworten (siehe llm.py).
    on_step: optionaler Callback(name) für die Fortschrittsanzeige.
    use_llm: v38 - False im Modus "Schnell": unsichere Treffer bleiben ohne
    LLM-Prüfung mit ihrer Confidence stehen und werden im Review gezeigt.
    use_gliner: Phase 1 - False, wenn nur Regeln laufen sollen (GLiNER
    nicht verfügbar oder Modus ohne KI).
    llm_progress(i, n) / should_cancel(): Phase 3 - Fortschritt und Abbruch
    während der (langsamen) Prüfung durch das Sprachmodell.
    """
    if not words:
        return []

    def step(name):
        if on_step:
            on_step(name)
        return timer.step(name) if timer else nullcontext()

    text, index = _build_text_and_index(words)

    with step("Regex"):
        suggestions = _rule_pass(text, index)

    if use_gliner:
        with step("GLiNER"):
            suggestions.extend(_gliner_pass(text, index))

    suggestions = resolve_conflicts(suggestions)
    # Phase 17: allgemeine Datumsangaben früh entfernen (core/vorschlagsregeln.py)
    suggestions = vorschlagsregeln.vor_der_ki(text, suggestions)

    # Unsichere Fälle (mittlere Sicherheit) an das lokale LLM zur Kontext-
    # Prüfung übergeben - läuft ausschließlich lokal (llama-cpp-python,
    # GGUF), kein Netzwerk-Call. Treffer mit Kontext oder Prüfziffer sind
    # eindeutig genug und gehen nicht ans LLM.
    ambiguous = [s for s in suggestions if 0.4 <= s["confidence"] < 0.75 and s["_priority"] < rules.PRIO_STRUCTURE
                 and s["label"] not in _NOT_FOR_LLM]
    if ambiguous and use_llm:
        from core import llm
        with step("LLM"):
            llm.disambiguate(text, ambiguous, cache=llm_cache, timer=timer, on_progress=llm_progress,
                             should_cancel=should_cancel)

    # Phase 17: Organisationen ohne Personenbezug u. a. nicht vorschlagen (core/vorschlagsregeln.py)
    suggestions = vorschlagsregeln.nach_der_ki(suggestions)

    # Interne Felder nicht ans Frontend ausliefern.
    for s in suggestions:
        s.pop("_span", None)
        s.pop("_priority", None)
    return suggestions
