"""See-also candidates found by code: notes anywhere in the wiki that share rare values with a note.

The route only visits some folders, so two notes with the same facts in distant folders would never meet in `relate`.
Values held by few notes (codes, amounts, dates, names) bring them together. No model call.
"""

from __future__ import annotations

import math
import re

from llmw2.agents.steps_classic import incoming_values
from llmw2.bundle.store import body_hash
from llmw2.bundle.tree import Folder, Note, split_see_also

RARE_TOP = 40  # most notes added to the candidates of `relate`
RARE_MAX_SHARE = 0.05  # a value is rare when at most this share of the notes hold it...
RARE_MIN_CAP = 3  # ...or this many, in a small wiki
INDEGREE_CAP = 12  # a note with this many See-also entries is not offered
_CACHE_MAX = 50_000
_YEAR = re.compile(r"^(19|20)\d\d$")

_cache: dict[tuple[str, str, str, str], frozenset[str]] = {}


def note_values(note: Note) -> frozenset[str]:
    """The values of a note (`incoming_values` of its text), cached by what they are computed from."""
    key = (note.rel, body_hash(note.main_body()), note.title, note.summary)
    values = _cache.get(key)
    if values is None:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        values = _cache[key] = frozenset(incoming_values(note.full_text()))
    return values


def rare_scores(root: Folder, note: Note, exclude: set[str]) -> list[tuple[Note, float]]:
    """The notes that share rare values with `note`, with their score: best first, at most `RARE_TOP`."""
    notes = root.all_notes()
    total = len(notes)
    held = {n.rel: note_values(n) for n in notes}
    df: dict[str, int] = {}
    for values in held.values():
        for v in values:
            df[v] = df.get(v, 0) + 1
    cap = max(RARE_MIN_CAP, RARE_MAX_SHARE * total)
    weight = {v: math.log(total / c) for v, c in df.items() if c <= cap and not _YEAR.match(v)}
    mine = {v for v in note_values(note) if v in weight}
    scored: list[tuple[Note, float]] = []
    for other in notes:
        if other.rel == note.rel or other.rel in exclude:
            continue
        score = sum(weight[v] for v in held[other.rel] & mine)
        if score <= 0 or len(split_see_also(other.body)[1]) >= INDEGREE_CAP:
            continue
        scored.append((other, score))
    scored.sort(key=lambda item: (-item[1], item[0].rel))
    return scored[:RARE_TOP]
