# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Search without a language model: BM25 on the whole note, and with an embedder the nearest notes by meaning.

The words of a note are counted once and kept while its text stays the same, in a BM25 index that follows the notes as they
change: a search looks at what changed since the last (a note written, edited or deleted by this process or by another one) and
indexes only that, never the whole wiki again. The vectors of an embedder are kept in a hidden file of the bundle under the
hash of the note's text, so a search embeds only the notes it has not seen.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import hashlib
import logging
import re
import sqlite3
import threading
import time
from array import array
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llmw2.agents import lexical
from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.store import RACY_NS, WikiStore
from llmw2.bundle.tree import Note, fields_match
from llmw2.errors import ConfigError

if TYPE_CHECKING:
    from llmw2.searchindex import Build, SearchIndex

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]  # texts -> one vector each
Pending = tuple[str, str, str, Counter[str]]  # a text that is not a note yet, for `NoteSearch.find`: path, title, summary, its words
VECTORS_FILE = ".llmw2/cache/embeddings.sqlite"  # the vectors of the embedder, by the hash of a note's text
EMBED_BODY_CHARS = 3000
EMBED_BATCH = 64
log = logging.getLogger(__name__)
_KEYS_PER_QUERY = 500
EXACT_POOL = 16  # candidates the exact expressions of a query are looked for in
EXACT_COMMON = 3  # an expression found in more candidates than this tells them apart no more
EXACT_KEPT = 512  # texts of candidates a process keeps for the exact expressions: under 1 MB at the mean size of a note's text
_QUOTED = re.compile(r'"([^"\n]{3,60})"|«([^»\n]{3,60})»|“([^”\n]{3,60})”|`([^`\n]{3,60})`')
_CODELIKE = re.compile(r"\b(?=[\w./:-]*(?:\d|[a-z][A-Z]|[\w][-_./:][\w]))[\w][\w./:-]{3,}\b")


def exact_expressions(query: str) -> list[str]:
    """What a query quotes ("...", «...», “...”, `...`) and its code-like tokens (a digit, a capital inside, or - _ . / :
    between word characters; four characters or more), each once, in order."""
    found = [next(g for g in m.groups() if g).strip() for m in _QUOTED.finditer(query)]
    rest = _QUOTED.sub(" ", query)
    found += _CODELIKE.findall(rest)
    return list(dict.fromkeys(e for e in found if e))


@dataclass(frozen=True)
class Hit:
    """A note found by `Wiki.search`; `rank` 1 is the first."""

    path: str
    title: str
    summary: str
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Partial:
    """A search answered while the index of the wiki is being built: from the `indexed` notes its file holds so far, of the `notes` its
    build listed (0 while the build does not say yet), with the notes this process wrote since and the entries not filed yet."""

    indexed: int
    notes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sentence(self) -> str:
        of = f" ({self.indexed} of {self.notes} notes)" if self.notes else ""
        return f"search index being built{of}: the results may be partial"


class Found(list[tuple[str, str, str]]):
    """What `NoteSearch.find` gives: (path, title, summary) of each note, and `partial` while the index is being built."""

    partial: Partial | None = None


class Hits(list[Hit]):
    """What `Wiki.search` gives: a list of `Hit`, and `partial` (a `Partial`) while the index is being built, else None."""

    partial: Partial | None = None


def _text_of(note: Note) -> str:
    return f"{note.title}\n{note.summary}\n\n{note.main_body()}"


def _embedded(note: Note) -> str:
    return f"{note.title}\n{note.summary}\n\n{note.main_body()[:EMBED_BODY_CHARS]}"


def _embedded_with_hash(note: Note) -> tuple[str, str]:
    """The text to embed and the hash of the text of the note it comes from, both from this one read of the note."""
    return _embedded(note), _hash(_text_of(note))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unit(vector: Sequence[float]) -> array[float]:
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return array("f", (x / norm for x in vector))


def _cosine(a: array[float], b: array[float]) -> float:
    """The dot product of two unit vectors; vectors of different sizes share nothing."""
    return sum(map(float.__mul__, a, b)) if len(a) == len(b) else 0.0


class VectorCache:
    """Unit vectors by key in one SQLite file; each call opens its own connection, as the classifier's cache does. The rollback journal
    is kept between writes (`journal_mode=PERSIST`: its header is zeroed at each commit, and the file is not made and deleted at every
    write); it holds the pages a write changed, hashes and vectors, never a text, and a purge cuts it to nothing (`truncate_journal`)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self, wait: float = 30) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=wait)
        db.execute("PRAGMA journal_mode=PERSIST")
        db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID")
        return db

    def truncate_journal(self, keep: set[str] | None = None, wait: float | None = None) -> bool:
        """After a purge: drop the vectors no key of `keep` names (texts no longer in the wiki; None: drop none), their pages zeroed
        (`secure_delete`), and cut the rollback journal to nothing, in one write made with no room allowed to the journal
        (`journal_size_limit = 0`). `wait`: the most it waits for the file's lock, then False with nothing done (None: as any write)."""
        if not self.path.exists():
            return True
        try:
            with closing(self._connect() if wait is None else self._connect(wait)) as db, db:
                db.execute("PRAGMA secure_delete = ON")
                db.execute("PRAGMA journal_size_limit = 0")
                if keep is not None:
                    stale = [k for (k,) in db.execute("SELECT key FROM vectors") if k not in keep]
                    db.executemany("DELETE FROM vectors WHERE key = ?", ((k,) for k in stale))
                db.execute("INSERT OR REPLACE INTO vectors VALUES ('', x'')")
                db.execute("DELETE FROM vectors WHERE key = ''")
        except sqlite3.OperationalError as e:
            if wait is None or "locked" not in str(e):
                raise
            return False
        return True

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

    def __init__(
        self, vectors: VectorCache | None, embedder: Embedder | None = None, model: str = "", index: SearchIndex | None = None
    ) -> None:
        from llmw2.searchindex import SearchIndex  # imported when a search is made, not with the package

        self.vectors = vectors
        self.index = index if index is not None else SearchIndex(None)  # the index of a wiki on disk, for `find`
        self.embedder = embedder
        self.model = model  # the embedder's model when the library knows it: vectors of another model are not reused
        # note path -> (the file's stamp when it was read, or None; hash of its text); the words are in the index
        self._seen: dict[str, tuple[tuple[int, int, int] | None, str]] = {}
        self._index = lexical.Bm25()  # the words of the notes `search` was given: for a list of notes with no wiki on disk
        self._held: dict[str, array[float]] = {}  # hash of a text -> its unit vector
        self._kept: dict[str, str] = {}  # hash of a text -> the text in lower case, for the exact expressions; the latest used last
        self._lock = threading.Lock()
        self._root = Path()
        self._build: Build | None = None  # the build of the file this process runs in the background, if any
        self._background = True  # False once a build in the background failed here: the searches build the file as before
        # while the file is being built and this process searched it: the notes its sections wrote since, as a search takes a text that is
        # not in the index (path, title, summary, words), with the hash of the text and the `fields`; None for a note gone (`_building`)
        self._recent: dict[str, tuple[Pending, str, Any] | None] | None = None
        self._recent_lock = threading.Lock()

    def search(self, notes: list[Note], everything: list[Note], query: str, k: int) -> list[Note]:
        """The best `k` of `notes`. `everything` is every note of the wiki: the index follows all of them, and the vectors of
        the rest are stale."""
        with self._lock:
            synced = self._sync(everything if len(everything) >= len(notes) else notes)
            by_rel = {n.rel: n for n in notes}
            hashes = {rel: synced[rel] for rel in by_rel}
            order_of_notes = list(by_rel)
            scores = self._index.scores(query, order_of_notes)
            by_words = sorted((rel for rel in order_of_notes if scores[rel] > 0), key=lambda rel: -scores[rel])
            by_meaning = (
                self._by_meaning(
                    query, list(by_rel), hashes, lambda rel: _embedded_with_hash(by_rel[rel]), set(self._seen_hashes(everything))
                )
                if self.embedder is not None
                else []
            )

            picked = self._order_of(k, by_words, by_meaning)
            exact = exact_expressions(query)
            if exact:  # the notes that hold what the query quotes come first, in the order they had
                wide = self._order_of(max(k, EXACT_POOL), by_words, by_meaning)
                pool = picked + [rel for rel in wide if rel not in picked]
                held = {rel: _text_of(by_rel[rel]).lower() for rel in pool[: max(k, EXACT_POOL)]}
                exact = [e for e in exact if sum(e.lower() in t for t in held.values()) <= EXACT_COMMON]
                if exact:
                    pool = pool[: max(k, EXACT_POOL)]
                    hit = [rel for rel in pool if any(e.lower() in held[rel] for e in exact)]
                    picked = hit + [rel for rel in pool if rel not in hit]
            return [by_rel[rel] for rel in picked[:k]]

    def find(
        self, store: WikiStore, query: str, k: int, where: dict[str, Any] | None = None, pending: Sequence[Pending] = ()
    ) -> Found:
        """The best `k` notes of the wiki (those `where` keeps, if given: a filter on `fields`) as (path, title, summary), from the
        index on disk. While the index is being built (`_building`) they come from what its lots committed so far, with the notes this
        process wrote since and `pending`, and `Found.partial` says how far the build got; once it is built, as before. The wiki is
        listed (every file's time and size, nothing read) and only the notes new or changed since the index was written are read; no
        note is read to answer, but the candidates of the exact expressions of the query whose text no earlier search kept
        (`EXACT_KEPT`). It never waits for a writer of the index (`SearchIndex.sync`), and the index's lock is not held while the
        query is embedded.

        `pending`: texts that are not notes yet, as (path, title, summary, the count of their words), already filtered by `where`.
        Each is scored by its words as one more note of the index would be (`SearchIndex.scored`: the notes' scores do not change)
        and comes before the first note it scores higher than, a note first at the same score and the texts in the order given among
        themselves; then the first `k`."""
        with self._lock, self.index.looked():  # one look at the index's file for the whole search
            return self._find(store, query, k, where, pending)

    def indexed(self, rels: Sequence[str]) -> set[str]:
        """Which of `rels` the index file holds (none while there is no index file); while the file is being built, also those this
        process wrote since (the searches find them, `_building`)."""
        with self._recent_lock:
            recent = {rel for rel, entry in (self._recent or {}).items() if entry is not None}
        have = {rel for rel in rels if rel in recent}
        path = self.index.path
        if path is None or not path.exists():
            return have
        return have | set(self.index.hashes(rels))

    def written(self, store: WikiStore, written: list[str] | None) -> None:
        """A section of this process wrote `written` (None: paths not known): the index takes them now, so that a search of any process
        finds them without writing. Nothing when the index was never built whole (its build lists the wiki once more after it marks the
        file built); then, if this process searched it meanwhile, the notes are kept for its searches until it is (`_building`). Takes
        only the index's lock."""
        if written is not None and self._recent is not None:
            self._keep_recent(store, written)
        path = self.index.path
        if path is None or not path.exists():
            return
        if written is None:
            self.index.sync(store, self._entry, build=False)
        else:
            self.index.update(store, written, self._entry)

    def forget(self, store: WikiStore, wait: float | None) -> bool:
        """After a purge: the index takes the notes that went out of its file (their rows zeroed), its WAL is cut to nothing, its `.bad`
        files go; the vectors of texts no longer in the wiki go too (the note purged among them: a vector is derived from its text), and
        their rollback journal is cut; the texts kept for the exact expressions go. `wait`: the most it waits in all, for the locks of the
        index and of the vectors and for the readers of the index's WAL (a purge's budget); None (the filer): the locks as long as they
        take, the readers not at all. Whether all of it was done (`SearchIndex.forget`); when a lock did not come in time, the vectors are
        left to the next look too."""
        end = None if wait is None else time.monotonic() + wait

        def left() -> float | None:
            return None if end is None else max(0.0, end - time.monotonic())

        keep: set[str] | None = None
        if not self._lock.acquire(timeout=-1 if wait is None else wait):  # a search of this process holds it (its embedder)
            return False
        try:
            path = self.index.path
            vectors = self.vectors is not None and self.vectors.path.exists()
            if (path is None or not path.exists()) and not vectors:  # never searched: neither holds anything to forget
                done = True
            else:
                with self.index.held(left()) as held:
                    done = held and self.index.forget(store, self._entry, left())
                    if vectors and held and (done or end is None):
                        keep = {self._key(h) for h in self.index.hashes().values()}
            self._kept.clear()  # no text of a note purged stays in the process
            if self._recent:  # nor what a note this process wrote while the file was being built held: read again as it is on disk now
                self._keep_recent(store, list(self._recent))
        finally:
            self._lock.release()
        if self.vectors is not None and (done or end is None):
            done = self.vectors.truncate_journal(keep, left()) and done
        return done

    def close(self) -> None:
        """Stop the build this process runs in the background (what its lots committed stays: the next search goes on from there), and
        let go of the notes kept meanwhile and of the index file."""
        from llmw2.searchindex import BUILD_WAIT

        build, self._build = self._build, None
        if build is not None:
            build.stop(BUILD_WAIT)
        with self._recent_lock:
            self._recent = None
        self.index.close()

    def _building(self, store: WikiStore) -> bool:
        """Whether the index file is being built (not built whole yet): the search then reads what the build's lots committed (they are
        durable), with the notes this process wrote since (`_recent`, from then on), and says it is partial. Starts the build (`Build`)
        when no process of the machine builds it, and waits for it `BUILD_WAIT` at most: a build that ends within it answers as before.
        Never waits for the write lock of the file."""
        from llmw2.searchindex import BUILD_WAIT, Build

        index = self.index
        if not self._background or index.ready():
            if self._recent is not None:
                with self._recent_lock:
                    self._recent = None
            self._build = None
            return False
        build = self._build
        if build is not None and build.finished:
            self._build = None
            if build.failed:  # the file cannot be built in the background: the searches build it as before (or keep one in memory)
                self._background = False
                return False
            build = None  # it ended and another process took the file back since: build again
        if build is None and index.path is not None:
            build = Build(index.path, store, self._entry)
            try:
                started = build.start()
            except OSError:  # no lease can be made where the file goes (a file in place of the folder): the searches build it as before
                self._background = False
                return False
            if started:  # else another process builds it: this one tries again at its next search
                self._build = build
                if build.wait(BUILD_WAIT) and (build.failed or index.ready()):
                    return self._building(store)
        if self._recent is None:
            with self._recent_lock:
                self._recent = {}
        return True

    def _keep_recent(self, store: WikiStore, written: Sequence[str]) -> None:
        """Keep the notes among `written` as they are on disk now (None for one gone), for the searches made until the file is built."""
        from llmw2.searchindex import unpack_fields

        got: dict[str, tuple[Pending, str, Any] | None] = {}
        for rel in dict.fromkeys(written):
            if not store.is_note_path(rel):
                continue
            path = store.root / rel
            try:
                st = path.stat()
                entry = self._entry(rel, str(path), (st.st_mtime_ns, st.st_size, st.st_ino))
            except OSError:
                entry = None
            if entry is None:
                got[rel] = None
                continue
            text, title, summary, fields, _ = entry
            got[rel] = ((rel, title, summary, Counter(lexical.words(text))), _hash(text), unpack_fields(fields))
        with self._recent_lock:
            if self._recent is not None:
                self._recent.update(got)

    def _find(
        self, store: WikiStore, query: str, k: int, where: dict[str, Any] | None, pending: Sequence[Pending]
    ) -> Found:
        self._root = store.root
        exact = exact_expressions(query)
        index = self.index
        building = self._building(store)
        with self._recent_lock:
            recent = dict(self._recent or {}) if building else {}

        # one run: a damaged file found by a step starts them all again
        def words() -> tuple[list[tuple[str, float]], list[float], dict[str, str], list[str], list[Pending]]:
            index.sync(store, self._entry, wait=False, build=not building)  # while it is built: what its lots committed, no listing
            mine: list[Pending] = []
            shadow: set[str] = set()
            if recent:  # a note written since is taken as it is now, in place of the file's row; one the file holds as it is, from the file
                held = index.hashes(list(recent))
                for rel, entry in recent.items():
                    if entry is None or held.get(rel) != entry[1]:
                        shadow.add(rel)
                        if entry is not None and (not where or fields_match(entry[2], where)):
                            mine.append(entry[0])
            extra = [*mine, *pending]
            pool = max(k, EXACT_POOL)
            ranked, others = index.scored(query, pool + len(shadow), where, [p[3] for p in extra])
            ranked = [(rel, s) for rel, s in ranked if rel not in shadow][:pool]
            if self.embedder is None:
                return ranked, others, index.hashes([rel for rel, _ in ranked]) if exact else {}, [], extra
            hashes = index.hashes()
            for rel in shadow:
                hashes.pop(rel, None)
            return ranked, others, hashes, [rel for rel in index.rels(where) if rel in hashes], extra  # a note taken away meanwhile goes

        ranked, others, hashes, rels, extra = index.run(words, reading=True)
        by_words = [rel for rel, _ in ranked]
        by_meaning: list[str] = []
        if self.embedder is not None:  # out of the index's lock: a writer does not wait for the embedder
            by_meaning = self._by_meaning(
                query,
                rels,
                hashes,
                lambda rel: _embedded_with_hash(self._read(rel)),
                set(hashes.values()),
            )
        picked = self._order_of(k, by_words, by_meaning)
        if exact:  # the notes that hold what the query quotes come first, in the order they had
            wide = self._order_of(max(k, EXACT_POOL), by_words, by_meaning)
            pool = picked + [rel for rel in wide if rel not in picked]
            texts = {rel: text for rel in pool[: max(k, EXACT_POOL)] if (text := self._text_for(rel, hashes.get(rel))) is not None}
            exact = [e for e in exact if sum(e.lower() in t for t in texts.values()) <= EXACT_COMMON]
            if exact:
                pool = pool[: max(k, EXACT_POOL)]
                hit = [rel for rel in pool if any(e.lower() in texts.get(rel, "") for e in exact)]
                picked = hit + [rel for rel in pool if rel not in hit]
        picked = picked[:k]
        meta = index.meta(picked)
        found = [(rel, *meta[rel]) for rel in picked if rel in meta]  # a note taken away since is left out
        if building:  # the file holds notes gone by hand since their lot: left out (a look at each hit's file)
            found = [hit for hit in found if (store.root / hit[0]).is_file()]
        out = Found(found)
        if extra:
            score = dict(ranked)
            waiting = sorted((j for j, s in enumerate(others) if s > 0), key=lambda j: -others[j])
            out = Found()
            for hit in found:
                while waiting and others[waiting[0]] > score.get(hit[0], 0.0):
                    out.append(extra[waiting.pop(0)][:3])
                out.append(hit)
            out += [extra[j][:3] for j in waiting]
            out = Found(out[:k])
        if building:
            from llmw2.searchindex import progress

            out.partial = Partial(len(index), progress(index.path)[1] if index.path is not None else 0)
        return out

    def _order_of(self, n: int, by_words: list[str], by_meaning: list[str]) -> list[str]:
        if self.embedder is None:
            return by_words[:n]
        picked = by_meaning[: max(n - 1, 0)]
        if by_words and by_words[0] not in picked:
            picked.append(by_words[0])
        picked += [rel for rel in by_meaning if rel not in picked][: n - len(picked)]
        return picked[:n]

    def _text_for(self, rel: str, text_hash: str | None) -> str | None:
        """The text of a candidate in lower case: kept from an earlier search when the index holds the hash of a text kept (the
        text the index took for the note), else read from its file; None for a note another process took away since the index
        was synced. At most `EXACT_KEPT` texts are kept, the least recently used go first."""
        text = self._kept.pop(text_hash, None) if text_hash is not None else None
        if text is None:
            try:
                note_text = _text_of(self._read(rel))
            except OSError:
                return None
            text, text_hash = note_text.lower(), _hash(note_text)
            self._kept.pop(text_hash, None)
        self._kept[text_hash] = text
        if len(self._kept) > EXACT_KEPT:
            del self._kept[next(iter(self._kept))]
        return text

    def _read(self, rel: str) -> Note:
        path = self._root / rel
        doc = OKFDocument.read(path)
        return Note(path, rel, doc.frontmatter, doc.body)

    def _entry(self, rel: str, path: str, stamp: tuple[int, int, int]) -> tuple[str, str, str, str, str] | None:
        """What the index takes of a note's file: (text, title, summary, `fields` as JSON, what it links to and cites as JSON); None for a
        file that is not a note."""
        try:
            doc = OKFDocument.read(Path(path))
        except OKFDocumentError as e:
            log.warning("skipping %s: %s", rel, e)
            return None
        from llmw2.searchindex import pack_fields, pack_links

        note = Note(Path(path), rel, doc.frontmatter, doc.body, stamp=stamp)
        fields = pack_fields(note.frontmatter.get("fields"))
        return _text_of(note), note.title, note.summary, fields, pack_links(note.body, rel, note.frontmatter)

    def _sync(self, notes: list[Note]) -> dict[str, str]:
        """Bring the index to `notes`: a note that is new or whose text changed is indexed (its old words go), a note that is no
        longer there is taken out; the rest is left alone. A note whose file has the stamp it had when it was read, and is not
        too recent to trust it (a file written within `RACY_NS` may change again unseen), is not read again. Returns the hash of
        the text of every note of `notes`."""
        hashes: dict[str, str] = {}
        now = time.time_ns()
        for note in notes:
            seen = self._seen.get(note.rel)
            stamp = note.stamp
            if seen is not None and stamp is not None and seen[0] == stamp and now - stamp[0] > RACY_NS:
                hashes[note.rel] = seen[1]
                continue
            text = _text_of(note)
            digest = _hash(text)
            hashes[note.rel] = digest
            if seen is None or seen[1] != digest or note.rel not in self._index:
                self._index.add(note.rel, Counter(lexical.words(text)))
            # read within RACY_NS of its time, a file may be written again with the same time and size: read it again next time
            self._seen[note.rel] = (stamp if stamp is not None and stamp[0] < now - RACY_NS else None, digest)
        if len(self._seen) > len(hashes):  # a note deleted, moved away or forgotten leaves the results at once
            for rel in [rel for rel in self._seen if rel not in hashes]:
                del self._seen[rel]
                self._index.remove(rel)
        return hashes

    def _key(self, text_hash: str) -> str:
        """The key of a note's vector in the cache: its text's hash, and the model's name when there is one."""
        return _hash(f"{self.model}\0{text_hash}") if self.model else text_hash

    def _by_meaning(
        self, query: str, rels: list[str], hashes: dict[str, str], embed_text: Callable[[str], tuple[str, str]], keep: set[str]
    ) -> list[str]:
        """The notes `rels` nearest to the query by meaning, at most as many as a search can use. `hashes`: the hash of each
        note's text; `embed_text`: for a note without a vector, the text to embed and the hash of the note's text, both from one
        read (a file that changed since the index was synced is stored under the hash of what was embedded); `keep`: the
        hashes of every note of the wiki (the cache drops the vectors of the rest)."""
        asked = _unit(self._embed([query])[0])
        size = len(asked)
        keys = {rel: self._key(hashes[rel]) for rel in rels}
        wanted = list(dict.fromkeys(keys.values()))
        known = {h: self._held[h] for h in wanted if h in self._held and len(self._held[h]) == size}
        missing = [h for h in wanted if h not in known]
        if missing and self.vectors is not None:
            known.update({h: v for h, v in self.vectors.get(missing).items() if len(v) == size})
        todo: dict[str, list[str]] = {}  # the notes with one text share a vector: one of them is read, the next if it cannot be
        for rel in rels:
            if keys[rel] not in known:
                todo.setdefault(keys[rel], []).append(rel)
        fresh: dict[str, array[float]] = {}
        pairs = list(todo.items())
        for i in range(0, len(pairs), EMBED_BATCH):
            texts: list[tuple[str, str]] = []
            readable: list[str] = []
            for _, same in pairs[i : i + EMBED_BATCH]:
                for rel in same:
                    try:
                        texts.append(embed_text(rel))
                        readable.append(rel)
                        break
                    except OSError:  # a note another process took away since the index was synced
                        continue
            if not readable:
                continue
            vectors = self._embed([text for text, _ in texts])
            for rel, (_, digest), v in zip(readable, texts, vectors, strict=True):
                keys[rel] = self._key(digest)  # the key follows the text that was embedded, never the one seen before
                fresh[keys[rel]] = _unit(v)
        if fresh and self.vectors is not None:
            self.vectors.put(fresh, {self._key(h) for h in keep} | set(fresh))
            known.update(fresh)
        self._held = known
        score = {rel: _cosine(asked, known[keys[rel]]) for rel in rels if keys[rel] in known}  # a note with no vector is left out
        return sorted(score, key=lambda rel: -score[rel])

    def _seen_hashes(self, notes: list[Note]) -> list[str]:
        """The hash of the text of each of `notes`, as the last `_sync` saw them."""
        return [self._seen[n.rel][1] for n in notes if n.rel in self._seen]

    def _embed(self, texts: list[str]) -> Sequence[Sequence[float]]:
        assert self.embedder is not None
        vectors = self.embedder(texts)
        if len(vectors) != len(texts):
            raise ConfigError(f"the embedder returned {len(vectors)} vectors for {len(texts)} texts")
        return vectors
