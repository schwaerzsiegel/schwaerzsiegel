"""
Phase 19: Namen im ganzen Dokument abgleichen.

Die Erkennung arbeitet Seite für Seite und braucht für einen Namen meist einen Hinweis („Frau Hansen“,
„Name:“) oder das KI-Modell. Steht derselbe Name an anderer Stelle ohne Hinweis („Dr. Möller, Jensen,
Hansen“ in einer Tabelle, „Personalakte Becker-Sommer“), blieb er bisher unerkannt. Hier wird nach der
Analyse aller Seiten ergänzt:

1. **Bekannte Namen:** Namenswörter aus sicheren Personen-Treffern (vorausgewählt, Sicherheit ab 0,85).
   Jede weitere Stelle mit genau diesem Wort wird als Person vorgeschlagen – vorausgewählt, aber „zu
   prüfen“ (0,8), weil ein Nachname auch ein gewöhnliches Wort sein kann („Koch“, „Richter“).
2. **Aufzählungen:** In einer durch Kommas getrennten Aufzählung einzelner Namenswörter („Dr. Möller,
   Jensen, Hansen, Thomsen“), in der mindestens zwei Einträge bekannte Namen sind, gelten auch die
   übrigen Einträge als Namen (0,75, „zu prüfen“).

Nur Wörter, die noch zu keinem Vorschlag gehören. Rein lokal, ohne KI-Modell.
"""

from __future__ import annotations

import re
from uuid import uuid4

from core import rules
from core.ner import _bbox_union, _build_text_and_index

SICHER = 0.85
CONF_BEKANNT = 0.8
CONF_LISTE = 0.75

_PUNCT = ".,;:()[]{}\"'„“”‚‘’"
_NAME_WORD = re.compile(rf"[{rules._UPPER}][{rules._LOWER}]+(?:-[{rules._UPPER}][{rules._LOWER}]+)?")
_TITEL = {"dr", "prof", "med", "jur", "dipl", "ing", "herr", "herrn", "frau", "hr", "fr"}
# Aufzählung: Einträge aus (Titel +) einem Namenswort, getrennt durch Komma (auch über Zeilenumbruch)
_ITEM = rf"(?:(?:Dr\.|Prof\.) )?{rules.NAME_WORD}"
_LISTE = re.compile(rf"(?<![\w\-]){_ITEM}(?:,[ \n]{_ITEM}){{2,}}(?:,?[ \n](?:und|sowie)[ \n]{_ITEM})?")


def _clean(word: str) -> str:
    return word.strip(_PUNCT)


def _is_name_word(word: str) -> bool:
    w = _clean(word)
    return (bool(_NAME_WORD.fullmatch(w)) and w.casefold() not in _TITEL
            and rules.plausible_person(w))


def bekannte_namen(pages_suggestions: list[list[dict]]) -> set[str]:
    names = set()
    for suggestions in pages_suggestions:
        for s in suggestions:
            if (s.get("label") == "Person" and s.get("default_selected") and s.get("confidence", 0) >= SICHER
                    and s.get("status", "suggested") != "rejected"):
                for word in s.get("text", "").split():
                    if _is_name_word(word):
                        names.add(_clean(word))
    return names


def _vorschlag(words, confidence, reason) -> dict:
    return {
        "id": str(uuid4()),
        "label": "Person",
        "text": " ".join(w["text"] for w in words),
        "bbox": _bbox_union(words),
        "word_ids": [w["id"] for w in words],
        "confidence": confidence,
        "source": "namensabgleich",
        "reason": reason,
        "default_selected": True,
        "also_detected_as": [],
    }


def ergaenzen(pages_words: list[list[dict]], pages_suggestions: list[list[dict]]) -> list[list[dict]]:
    """Neue Personen-Vorschläge je Seite (die vorhandenen bleiben unverändert)."""
    names = bekannte_namen(pages_suggestions)
    result: list[list[dict]] = []
    for words, suggestions in zip(pages_words, pages_suggestions):
        covered = {wid for s in suggestions for wid in s.get("word_ids", [])}
        person_words = {wid for s in suggestions if s.get("label") == "Person" for wid in s.get("word_ids", [])}
        new: list[dict] = []
        if not words:
            result.append(new)
            continue
        text, index = _build_text_and_index(words)

        # 2. Aufzählungen mit mindestens zwei bekannten Namen
        listed: set[str] = set()
        for m in _LISTE.finditer(text):
            items = []
            for start, end, w in index:
                if start >= m.start() and end <= m.end() and _is_name_word(w["text"]):
                    items.append(w)
            known = [w for w in items if _clean(w["text"]) in names or w["id"] in person_words]
            if len(known) < 2:
                continue
            for w in items:
                if w["id"] not in covered and _clean(w["text"]) not in names:
                    new.append(_vorschlag([w], CONF_LISTE, "In einer Aufzählung mit anderen Namen – bitte prüfen"))
                    listed.add(w["id"])

        # 1. weitere Stellen bekannter Namen (aufeinanderfolgende Wörter zu einem Vorschlag – aber je Person
        # einer: nach einem Komma beginnt ein neuer Name, damit sich jede Person einzeln auslassen lässt)
        run: list[dict] = []

        def flush():
            if run:
                new.append(_vorschlag(list(run), CONF_BEKANNT,
                                      "Name kommt im Dokument bereits vor – bitte prüfen"))
                run.clear()

        for _, _, w in index:
            if w["id"] not in covered and w["id"] not in listed and _clean(w["text"]) in names:
                if run and (not _same_line(run[-1], w) or run[-1]["text"].rstrip().endswith((",", ";"))):
                    flush()
                run.append(w)
            else:
                flush()
        flush()
        result.append(new)
    return result


def _same_line(a: dict, b: dict) -> bool:
    return abs(a["bbox"][1] - b["bbox"][1]) < 0.5 * max(1.0, a["bbox"][3] - a["bbox"][1])
