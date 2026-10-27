"""
Phase 7: sentencepiece mit Umlauten im Pfad.

sentencepiece (0.2.x) öffnet Modelldateien unter Windows mit der
Schmalzeichen-API der C++-Laufzeit. Enthält der Pfad Zeichen außerhalb
von ASCII – etwa „C:\\Users\\Jürgen\\…“ oder den Installationsordner
„…\\Schwärzsiegel“ –, meldet es „NOT_FOUND“, obwohl die Datei existiert.
Dann kann transformers den Tokenizer von GLiNER (mdeberta, spm.model)
nicht bauen, und die KI-Erkennung fällt aus.

Abhilfe an der Ursache: Bei solchen Pfaden liest Python die Datei (Python
kommt mit Unicode-Pfaden zurecht) und übergibt sentencepiece nur den
Inhalt. Reine ASCII-Pfade laufen unverändert über den Originalweg.
Muss vor dem ersten Laden eines Tokenizers importiert werden (core/ner.py).
"""

from __future__ import annotations

import os

import sentencepiece

_original_load_from_file = sentencepiece.SentencePieceProcessor.LoadFromFile


def _load_from_file(self, filename):
    path = os.fspath(filename)
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if path.isascii():
        return _original_load_from_file(self, filename)
    with open(path, "rb") as handle:
        return self.LoadFromSerializedProto(handle.read())


def install() -> None:
    if sentencepiece.SentencePieceProcessor.LoadFromFile is not _load_from_file:
        sentencepiece.SentencePieceProcessor.LoadFromFile = _load_from_file


install()
