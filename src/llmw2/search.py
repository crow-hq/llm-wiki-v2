# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Search without a language model: BM25 on the whole note, and with an embedder the nearest notes by meaning.

The words of a note are counted once and kept while its text stays the same; the vectors of an embedder are kept in
a hidden file of the bundle under the hash of the note's text, so a search embeds only the notes it has not seen.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from array import array
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from llmw2.agents import lexical
from llmw2.bundle.tree import Note
from llmw2.errors import ConfigError

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]  # texts -> one vector each
VECTORS_FILE = ".llmw2/cache/embeddings.sqlite"  # the vectors of the embedder, by the hash of a note's text
EMBED_BODY_CHARS = 3000
EMBED_BATCH = 64
_KEYS_PER_QUERY = 500


@dataclass(frozen=True)
class Hit:
    """A note found by `Wiki.search`; `rank` 1 is the first."""

    path: str
    title: str
    summary: str
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text_of(note: Note) -> str:
    return f"{note.title}\n{note.summary}\n\n{note.main_body()}"


def _embedded(note: Note) -> str:
    return f"{note.title}\n{note.summary}\n\n{note.main_body()[:EMBED_BODY_CHARS]}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unit(vector: Sequence[float]) -> array[float]:
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return array("f", (x / norm for x in vector))


def _cosine(a: array[float], b: array[float]) -> float:
    """The dot product of two unit vectors; vectors of different sizes share nothing."""
    return sum(map(float.__mul__, a, b)) if len(a) == len(b) else 0.0


class VectorCache:
    """Unit vectors by key in one SQLite file; each call opens its own connection, as the classifier's cache does."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID")
        return db

    def get(self, keys: Sequence[str]) -> dict[str, array[float]]:
        if not keys or not self.path.exists():
            return {}
        found: dict[str, array[float]] = {}
        with closing(self._connect()) as db:
            for i in range(0, len(keys), _KEYS_PER_QUERY):
                chunk = keys[i : i + _KEYS_PER_QUERY]
                marks = ",".join("?" * len(chunk))
                for key, blob in db.execute(f"SELECT key, value FROM vectors WHERE key IN ({marks})", chunk):
                    vector: array[float] = array("f")
                    vector.frombytes(blob)
                    found[key] = vector
        return found

    def put(self, vectors: dict[str, array[float]], keep: set[str]) -> None:
        """Store `vectors`, and drop what no key of `keep` names any more (versions of notes that changed)."""
        with closing(self._connect()) as db, db:
            db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)", ((k, v.tobytes()) for k, v in vectors.items()))
            stale = [k for (k,) in db.execute("SELECT key FROM vectors") if k not in keep and k not in vectors]
            db.executemany("DELETE FROM vectors WHERE key = ?", ((k,) for k in stale))


class NoteSearch:
    """Ranks notes for a query. Keeps the counted words and the vectors of the notes it has seen, by the hash of their text."""

    def __init__(self, vectors: VectorCache, embedder: Embedder | None = None) -> None:
        self.vectors = vectors
        self.embedder = embedder
        self._counts: dict[str, tuple[str, Counter[str]]] = {}  # note path -> (hash of its text, its words)
        self._index: tuple[tuple[str, ...], lexical.Bm25] | None = None
        self._held: dict[str, array[float]] = {}  # hash of a text -> its unit vector
        self._lock = threading.Lock()

    def search(self, notes: list[Note], everything: list[Note], query: str, k: int) -> list[Note]:
        """The best `k` of `notes`. `everything` is every note of the wiki: the vectors of the rest are stale."""
        with self._lock:
            texts = {n.rel: _text_of(n) for n in notes}
            hashes = {rel: _hash(t) for rel, t in texts.items()}
            by_rel = {n.rel: n for n in notes}
            bm25 = self._bm25(texts, hashes)
            scores = bm25.scores(query)
            by_words = [rel for rel in bm25.rank(query) if scores[rel] > 0]
            if self.embedder is None:
                return [by_rel[rel] for rel in by_words[:k]]
            by_meaning = self._by_meaning(query, by_rel, hashes, everything)
            picked = by_meaning[: max(k - 1, 0)]
            if by_words and by_words[0] not in picked:
                picked.append(by_words[0])
            picked += [rel for rel in by_meaning if rel not in picked][: k - len(picked)]
            return [by_rel[rel] for rel in picked[:k]]

    def _bm25(self, texts: dict[str, str], hashes: dict[str, str]) -> lexical.Bm25:
        signature = tuple(f"{rel}\0{h}" for rel, h in hashes.items())
        if self._index and self._index[0] == signature:
            return self._index[1]
        counts: dict[str, Counter[str]] = {}
        for rel, text in texts.items():
            held = self._counts.get(rel)
            if held is None or held[0] != hashes[rel]:
                held = (hashes[rel], Counter(lexical.words(text)))
            counts[rel] = held[1]
            self._counts[rel] = held
        index = lexical.Bm25(counts)
        self._index = (signature, index)
        return index

    def _by_meaning(self, query: str, by_rel: dict[str, Note], hashes: dict[str, str], everything: list[Note]) -> list[str]:
        asked = _unit(self._embed([query])[0])
        wanted = list(dict.fromkeys(hashes.values()))
        known = {h: self._held[h] for h in wanted if h in self._held and len(self._held[h]) == len(asked)}
        known.update({h: v for h, v in self.vectors.get([h for h in wanted if h not in known]).items() if len(v) == len(asked)})
        todo = {hashes[rel]: rel for rel in by_rel if hashes[rel] not in known}
        fresh: dict[str, array[float]] = {}
        pairs = list(todo.items())
        for i in range(0, len(pairs), EMBED_BATCH):
            chunk = pairs[i : i + EMBED_BATCH]
            vectors = self._embed([_embedded(by_rel[rel]) for _, rel in chunk])
            fresh.update({h: _unit(v) for (h, _), v in zip(chunk, vectors, strict=True)})
        if fresh:
            self.vectors.put(fresh, {_hash(_text_of(n)) for n in everything})
            known.update(fresh)
        self._held = known
        score = {rel: _cosine(asked, known[hashes[rel]]) for rel in by_rel}
        return sorted(by_rel, key=lambda rel: -score[rel])

    def _embed(self, texts: list[str]) -> Sequence[Sequence[float]]:
        assert self.embedder is not None
        vectors = self.embedder(texts)
        if len(vectors) != len(texts):
            raise ConfigError(f"the embedder returned {len(vectors)} vectors for {len(texts)} texts")
        return vectors
