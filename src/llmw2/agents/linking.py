"""See-also candidates found by code: notes anywhere in the wiki that share rare values with a note.

The route only visits some folders, so two notes with the same facts in distant folders would never meet in `relate`.
Values held by few notes (codes, amounts, dates, names) bring them together. No model call.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable

from llmw2.agents.steps_classic import incoming_values
from llmw2.bundle.store import RACY_NS, Stamp, body_hash
from llmw2.bundle.tree import Folder, Note, split_see_also

RARE_TOP = 40  # most notes added to the candidates of `relate`
RARE_MAX_SHARE = 0.05  # a value is rare when at most this share of the notes hold it...
RARE_MIN_CAP = 3  # ...or this many, in a small wiki
INDEGREE_CAP = 12  # a note with this many See-also entries is not offered
_CACHE_MAX = 50_000
_INDEXES_MAX = 8  # wikis whose index is kept at once
_YEAR = re.compile(r"^(19|20)\d\d$")

_cache: dict[tuple[str, str, str, str], frozenset[str]] = {}


class _Index:
    """The values of the notes of a wiki, kept from one call of `rare_scores` to the next: by note, the stamp of the file
    they were read from, and by value the notes that hold it. A note is read again only when its stamp is not the one kept."""

    def __init__(self) -> None:
        self.held: dict[str, tuple[Stamp | None, frozenset[str]]] = {}  # note path -> (stamp, values)
        self.posting: dict[str, set[str]] = {}  # value -> note paths: its length is the value's document frequency

    def put(self, rel: str, stamp: Stamp | None, values: frozenset[str]) -> None:
        old = self.held.get(rel)
        if old is not None:
            if old[1] == values:
                self.held[rel] = (stamp, values)
                return
            self.drop(rel)
        self.held[rel] = (stamp, values)
        for v in values:
            self.posting.setdefault(v, set()).add(rel)

    def drop(self, rel: str) -> None:
        for v in self.held.pop(rel)[1]:
            holders = self.posting[v]
            holders.discard(rel)
            if not holders:
                del self.posting[v]


_indexes: dict[str, _Index] = {}


def _index_of(root: Folder) -> _Index:
    key = str(root.path)
    if key not in _indexes:
        if len(_indexes) >= _INDEXES_MAX:
            _indexes.clear()
        _indexes[key] = _Index()
    return _indexes[key]


def note_values(note: Note) -> frozenset[str]:
    """The values of a note (`incoming_values` of its text), cached by what they are computed from."""
    key = (note.rel, body_hash(note.main_body()), note.title, note.summary)
    values = _cache.get(key)
    if values is None:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        values = _cache[key] = frozenset(incoming_values(note.full_text()))
    return values


def rare_scores(
    root: Folder, note: Note, exclude: set[str], allow: Callable[[Note], bool] | None = None
) -> list[tuple[Note, float]]:
    """The notes that share rare values with `note`, with their score: best first, at most `RARE_TOP`.

    `allow`, when given, keeps only the notes it accepts (so the best ones the caller may use fill the list).

    The values of the wiki are an inverted index kept between calls, so a call reads again only the notes that changed
    and looks only at the notes that hold a rare value of `note`.
    """
    notes = root.all_notes()
    total = len(notes)
    index = _index_of(root)
    now = time.time_ns()
    by_rel: dict[str, Note] = {}
    for n in notes:
        by_rel[n.rel] = n
        kept = index.held.get(n.rel)
        if kept is not None and n.stamp is not None and kept[0] == n.stamp and now - n.stamp[0] > RACY_NS:
            continue  # the same file as when its values were read, and old enough to be sure of it
        index.put(n.rel, n.stamp, note_values(n))
    if len(index.held) != total:  # notes that are gone
        for rel in [r for r in index.held if r not in by_rel]:
            index.drop(rel)
    cap = max(RARE_MIN_CAP, RARE_MAX_SHARE * total)
    own = index.held[note.rel][1] if note.rel in index.held else note_values(note)
    mine = sorted(v for v in own if 0 < len(index.posting.get(v, ())) <= cap and not _YEAR.match(v))
    hits: dict[str, list[float]] = {}
    for v in mine:
        weight = math.log(total / len(index.posting[v]))
        for rel in index.posting[v]:
            hits.setdefault(rel, []).append(weight)
    scored: list[tuple[Note, float]] = []
    for rel, weights in hits.items():
        other = by_rel[rel]
        if rel == note.rel or rel in exclude or (allow is not None and not allow(other)):
            continue
        score = sum(weights)
        if score <= 0 or len(split_see_also(other.body)[1]) >= INDEGREE_CAP:
            continue
        scored.append((other, score))
    scored.sort(key=lambda item: (-item[1], item[0].rel))
    return scored[:RARE_TOP]
