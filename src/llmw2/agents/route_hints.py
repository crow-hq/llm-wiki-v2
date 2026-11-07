# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Routing hints: what the classifier reads about a subfolder besides its description, computed from the notes in it.

A subfolder's description is often too short to tell it from its siblings. Its distinctive words (the grouping step's
TF-IDF over the siblings, one document per subtree) and a few of its note titles say what is actually filed there. They
are given for the subfolders of a top-level area only, the one depth where they were measured to help: the choice among
the top-level areas, and the one among deeper subfolders, read the descriptions alone. The hints exist only in the option
text the classifier reads: they are never stored and never shown. No model call.

A build routes once per document and reads the tree again each time, so nothing is cached by object: the words of a note,
the words common to the wiki and a folder's hints are kept under the paths of the notes and folders and the stamps of the
notes, and computed again only for what changed. The caches are shared by the threads of a run.
"""

from __future__ import annotations

import threading
from collections import Counter

from llmw2.agents import grouping, lexical
from llmw2.bundle.tree import Folder, Note

HINT_DEPTH = 1  # the depth of the folder whose subfolders get hints: a top-level area (the only depth measured)
HINT_WORDS = 8  # distinctive words per subfolder
HINT_TITLES = 3  # example titles per subfolder
DESCRIPTION_CHARS = 300
OPTION_TEXT_CHARS = 600
COMMON_SHARE = 0.3  # a word in more than this share of the wiki's notes is no hint (a language-agnostic stopword filter)
MIN_TITLE_CHARS = 4
MARKUP = frozenset(
    {"nbsp", "begin", "end", "mathbf", "mathrm", "cdot", "overline", "frac", "array", "wedge", "quad", "left", "right", "text"}
    | {"sqrt", "http", "https", "www", "com", "html", "png", "jpg", "img", "src", "href", "amp", "quot", "div", "span"}
)

_lock = threading.Lock()
_words: dict[str, tuple[tuple, Counter[str]]] = {}  # note path -> (its stamp, its words)
_common: dict[str, tuple[tuple, frozenset[str]]] = {}  # wiki root path -> (stamps of its notes, words common to them)
_hints: dict[str, tuple[tuple, dict[str, str]]] = {}  # folder path -> (its children and their notes' stamps, suffixes)


def _stamp(note: Note) -> tuple:
    return (note.rel, note.stamp, len(note.body), note.title)


def _counts(note: Note) -> Counter[str]:
    """The words of a note (title and body); counted again only when the note changed. Never modified by the caller."""
    stamp, key = _stamp(note), str(note.path)
    with _lock:
        cached = _words.get(key)
    if cached is None or cached[0] != stamp:
        tokens = lexical.words(f"{note.title}\n{note.main_body()}")
        cached = (stamp, Counter(w for w in tokens if w.isalpha() and w not in MARKUP))
        with _lock:
            _words[key] = cached
    return cached[1]


def _common_words(root: Folder) -> frozenset[str]:
    """The words in more than `COMMON_SHARE` of the wiki's notes."""
    notes = root.all_notes()
    stamps = tuple(map(_stamp, notes))
    key = str(root.path)
    with _lock:
        cached = _common.get(key)
    if cached is None or cached[0] != stamps:
        df = Counter(w for n in notes for w in _counts(n))
        cached = (stamps, frozenset(w for w, c in df.items() if c > COMMON_SHARE * len(notes)))
        with _lock:
            _common[key] = cached
    return cached[1]


def option_texts(folder: Folder, children: list[Folder]) -> dict[str, str]:
    """The option text of each of `children`, the subfolders of the top-level area `folder`: its description, then its
    distinctive words and example titles. Descriptions alone unless the notes are in at least two of the subfolders, as
    there is nothing to tell apart otherwise."""
    plain = {c.name: c.description or c.name for c in children}
    subtrees = {c.name: c.all_notes() for c in children}
    if sum(1 for notes in subtrees.values() if notes) < 2:
        return plain
    common = _common_words(folder.ancestors()[0])
    key = (common, tuple((c.name, tuple(map(_stamp, subtrees[c.name]))) for c in children))
    with _lock:
        cached = _hints.get(str(folder.path))
    if cached is None or cached[0] != key:
        cached = (key, _compute(children, subtrees, common))
        with _lock:
            _hints[str(folder.path)] = cached
    return {name: (text[:DESCRIPTION_CHARS] + cached[1].get(name, ""))[:OPTION_TEXT_CHARS] for name, text in plain.items()}


def _compute(children: list[Folder], subtrees: dict[str, list[Note]], common: frozenset[str]) -> dict[str, str]:
    """Per subfolder the suffix of its option text: " Typical words: a, b." and " Examples: t1; t2; t3."."""
    # One text per sibling (its whole subtree): its words are its notes' summed, and weighed as `grouping.vectors` weighs
    # them (a word of one sibling only, or of more than half of them, has no weight)
    per_note = {c.name: [_counts(n) for n in subtrees[c.name]] for c in children}
    texts = [Counter() for _ in children]
    for text, c in zip(texts, children, strict=True):
        for counts in per_note[c.name]:
            text.update(counts)
    weights = grouping.weigh(texts)
    out = {}
    for c, vector in zip(children, weights, strict=True):
        notes = subtrees[c.name]
        if not notes:
            continue
        in_notes = Counter(w for counts in per_note[c.name] for w in counts)
        need = 2 if len(notes) >= 2 else 1  # a word of one note only is that note's, not the subfolder's
        scored = {w: x for w, x in vector.items() if w not in common and in_notes[w] >= need}
        words = sorted(scored, key=lambda w: (-scored[w], w))[:HINT_WORDS]
        titles = [n.title for n in notes if len(n.title) >= MIN_TITLE_CHARS][:HINT_TITLES]
        out[c.name] = (f" Typical words: {', '.join(words)}." if words else "") + (f" Examples: {'; '.join(titles)}." if titles else "")
    return out
