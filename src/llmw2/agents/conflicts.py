# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""When the notes read to answer a question disagree on its answer, the answer must report every value (CROW).

One typed question reads the passage of each note that is closest to the question; if it says the notes disagree,
the answer step gets a hint added to the question.
"""

from __future__ import annotations

import re

from llmw2.bundle.tree import Note

__all__ = ["CONFLICT", "CONFLICT_HINT", "CONFLICT_MIN", "MAX_NOTES", "PASSAGE_CHARS", "conflict_state"]

CONFLICT_MIN = 0.4  # the probability that the notes disagree, above which the hint is added
PASSAGE_CHARS = 900
MAX_NOTES = 8

CONFLICT = "The notes do not all agree on the answer to the question: at least one gives a value that another contradicts."
CONFLICT_HINT = (  # added to the question of the answer step, after the history hint if there is one
    "(The notes may disagree on this. If two notes give different values for what the question asks, report every value, "
    "say which note gives which and which source is more recent, and do not pick one silently.)"
)

_WORD = re.compile(r"\w{3,}")
_BLANK_LINE = re.compile(r"\n\s*\n")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _passage(question: str, note: Note) -> str:
    """The paragraph of the note that shares most words with the question (the first on a tie), cut."""
    paragraphs = [p.strip() for p in _BLANK_LINE.split(note.main_body()) if p.strip()]
    asked = _words(question)
    best = max(paragraphs, key=lambda p: len(asked & _words(p)), default="")  # max keeps the first of equals
    return best[:PASSAGE_CHARS]


def conflict_state(question: str, notes: list[Note]) -> str:
    """The question and, for each of the first `MAX_NOTES` notes, its title and the passage closest to the question."""
    blocks = [f"### Note: {n.title}\n{_passage(question, n)}" for n in notes[:MAX_NOTES]]
    return f"Question: {question}\n\n" + "\n\n".join(blocks)
