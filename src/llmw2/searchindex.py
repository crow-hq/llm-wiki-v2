# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The search index of a wiki, on disk: BM25 postings in one SQLite file that any number of processes share.

The file is a cache of the notes, never a source of truth: it can be deleted at any time and the next search builds it again, a
file that is damaged or of another schema is thrown away and built again, and nothing in it is ever run (no `pickle`). What a
note changed costs its own rows only: the postings whose count of a word changed, and the note's row. A process keeps in memory
the length of each note and the postings of the words it was asked for last, and asks the file again only when another process
wrote (a generation number in the file says so).

The file also keeps what each note links to and the raw copies it cites (`links`): a delete or a purge finds the notes that link to
what goes with no note read (`SearchIndex.linking`).

`SearchIndex.sync` brings the file to the files of the wiki (`WikiStore.scan_notes` lists them with nothing read, and only the notes
new or changed are read); `rank` scores a query exactly as `lexical.Bm25` does, over the whole wiki or over the notes of a list.

How it writes: a process keeps its connection open (WAL, `synchronous=NORMAL`: a commit is not flushed on its own, a power cut may
take the last ones, which the next sync reads again from the notes since their files say so; a checkpoint every `CHECKPOINT_PAGES`
pages), and deleted rows are zeroed (`secure_delete`). After a purge `forget` empties the WAL into the file (`TRUNCATE`) and deletes the
files put aside as `.bad`, so that the words, titles and summaries of a note purged are in no file of the cache. The connection is
tied to the file and to the process that opened it (`_ident`): a file replaced under it, or a fork, opens a new one; a search looks
at the file once, at its first step (`looked`).
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import bisect
import contextlib
import hashlib
import heapq
import json
import logging
import math
import mmap
import os
import sqlite3
import struct
import threading
import time
import zlib
from array import array
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, TypeVar

from llmw2.agents import lexical
from llmw2.bundle.filelock import FileLock
from llmw2.bundle.journal import shared_bytes
from llmw2.bundle.store import RACY_NS, Stamp, WikiStore, raw_sources, tree_key
from llmw2.bundle.tree import fields_match, link_targets

SCHEMA = 6  # the layout of the file (and part of its name: a file of another schema is left alone, an older process may hold it)
INDEX_FILE = f".llmw2/cache/search-v{SCHEMA}.sqlite"  # the postings of the notes' words, the fields of the notes and their links
BATCH = 4096  # notes read, hashed and counted together: a build touches each page of the postings once a lot, not once 512 notes
CHECKPOINT_PAGES = 16000  # the WAL is written into the file this often (8 MB at 512-byte pages): fewer checkpoints, fewer bytes per note
BUILD_CACHE_KB = 65536  # page cache of the connection that builds the index from many notes (the default is 2 MB)
HOT_ENTRIES = 1_500_000  # postings a process keeps in memory, 8 bytes each (12 MB); beyond that the least recently asked for go
BUSY_S = 180  # seconds a writer waits for the lock at most each time: one lot of a build (BATCH notes) per process ahead of it
LOCK_TRIES = 5  # and how many times it waits that long before the error is its caller's
WRITTEN_WAIT = 1.0  # seconds a section waits at most for the locks to put in what it wrote (it holds the bundle's): else the next look does
# seconds a reader trusts the last listing of the wiki, its own or one another process of the machine shares (`_listed_by_another`): what
# the library writes comes by `update`, a hand edit by the next listing
LISTING_LOOK = 1.0
# rows a file names in `recent` (its last writes, by generation, a generation with no row counted as one): a reader that finds the
# file written by another process reads those rows instead of all (`_caught_up`); a write of more rows, or older writes, are read whole.
# Measured with eight readers that search every 0.2 to 2 s, rows behind at each catch-up: filings that link and merge notes (the A1M
# sequence) p99 54, max 57; one row a filing p99 33. 88 covers those, and stays within three pages of 512 even at one row a
# generation (some 1.5 KB at most)
RECENT_IDS = 88
# seconds the search that starts a build in the background waits for it: a build that ends within it is answered as before, from the file
# whole; past it the search answers at once from what the file holds so far, marked partial (`NoteSearch.find`). A fifth of the 5 s a
# first search may take (requirement B6 of the robots' table): a small wiki is answered whole, a large one in time
BUILD_WAIT = 1.0
PAGE_SIZE = 512  # a changed note touches pages at random: small pages write fewer bytes (measured at 512, 1024, 2048, 4096)
_K1, _B = lexical._K1, lexical._B
_MASK = (1 << 64) - 1
_CORRUPT, _NOTADB = 11, 26  # SQLITE_CORRUPT, SQLITE_NOTADB
_BUSY = (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED
# the listing the processes of a machine share for a file: its epoch (a number of it), its generation, when it began (`time.monotonic_ns`,
# one clock for the machine) and a check, so that a write torn by another process, or bytes never written, are no listing
_LISTED = struct.Struct("<QQQQ")
_LISTED_MASK = 0x9E3779B97F4A7C15
_PROGRESS = struct.Struct("<QQ")  # in the lease of a build, from byte 64 (the lock is on byte 0): notes in the file, notes listed
_PROGRESS_AT = 64
_PROGRESS_EVERY = 256  # notes read between two writes of the progress
log = logging.getLogger(__name__)
_DDL = (
    "CREATE TABLE meta (k TEXT PRIMARY KEY, v) WITHOUT ROWID",
    "CREATE TABLE docs (id INTEGER PRIMARY KEY, rel TEXT NOT NULL UNIQUE, mtime INTEGER NOT NULL, size INTEGER NOT NULL,"
    " ino INTEGER NOT NULL, len INTEGER NOT NULL, bad INTEGER NOT NULL, hash TEXT NOT NULL)",
    "CREATE TABLE docbody (id INTEGER PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL, counts BLOB NOT NULL, fields TEXT NOT NULL,"
    " links TEXT NOT NULL)",
    "CREATE TABLE postings (word TEXT NOT NULL, doc INTEGER NOT NULL, f INTEGER NOT NULL, PRIMARY KEY (word, doc)) WITHOUT ROWID",
    # the paths each note links to and the raw copies it cites (`docbody.links`), by the path: who links to a path, with no note read
    "CREATE TABLE links (target TEXT NOT NULL, doc INTEGER NOT NULL, PRIMARY KEY (target, doc)) WITHOUT ROWID",
)
_expected: list[tuple[str, str, str | None]] = []

# What a read of a note gives: (the text to index, its title, its summary, its `fields` as JSON, the paths it links to and the raw copies it
# cites as a JSON list, sorted: `pack_links`), or None for a file that is not a note.
Reader = Callable[[str, str, Stamp], tuple[str, str, str, str, str] | None]
T = TypeVar("T")


class _Corrupt(sqlite3.DatabaseError):
    """The file is a database but what it says does not add up."""


class _Stale(sqlite3.DatabaseError):
    """Another process wrote while this one was answering: start again from the sync."""


class _Opaque:
    """A `fields` key that is not a string or a number (a date, a tuple): kept by its repr, equal to nothing a `where` can name."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __hash__(self) -> int:
        return hash(("opaque", self.text))

    def __eq__(self, other: object) -> bool:
        return self is other


def pack_fields(fields: Any) -> str:
    """A note's `fields` as text for the index, the keys with their types (YAML allows `1: x`, and `1` is not `"1"` to `where`).
    What is not a mapping holds no `where`, so it is stored as null."""
    if not isinstance(fields, dict):
        return "null"
    pairs = []
    for key, value in fields.items():
        if isinstance(key, bool):
            tagged: Any = ["bool", key]
        elif isinstance(key, int):
            tagged = ["int", key]
        elif isinstance(key, float):
            tagged = ["float", key]
        elif key is None:
            tagged = ["none"]
        elif isinstance(key, str):
            tagged = key
        else:
            tagged = ["repr", repr(key)]
        pairs.append([tagged, value])
    return json.dumps(pairs, default=str)


def unpack_fields(text: str) -> Any:
    """What `pack_fields` kept: a dict with its keys as they were (None when `fields` was not a mapping)."""
    pairs = json.loads(text)
    if pairs is None:
        return None
    out: dict[Any, Any] = {}
    for tagged, value in pairs:
        if isinstance(tagged, str):
            key: Any = tagged
        elif tagged[0] == "none":
            key = None
        elif tagged[0] == "repr":
            key = _Opaque(tagged[1])
        else:
            key = {"bool": bool, "int": int, "float": float}[tagged[0]](tagged[1])
        out[key] = value
    return out


def pack_links(body: str, rel: str, frontmatter: dict[str, Any]) -> str:
    """What a note links to, as text for the index: the paths of its links (`link_targets`) and the raw copies it cites ("/raw/…md",
    `raw_sources`: a path of the bundle never starts with "/"), sorted, once each, as a JSON list."""
    return json.dumps(sorted({*link_targets(body, rel), *raw_sources(frontmatter)}), ensure_ascii=False)


def _unpack_links(text: str) -> set[str]:
    return set(json.loads(text)) if text else set()


def _master(db: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    return sorted(db.execute("SELECT type, name, sql FROM sqlite_master").fetchall(), key=lambda r: (r[0], r[1]))


def _expected_master() -> list[tuple[str, str, str | None]]:
    """What `sqlite_master` of a file of this schema holds, taken from a database in memory made by the same statements."""
    if not _expected:
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            for statement in _DDL:
                db.execute(statement)
            _expected.extend(_master(db))
    return _expected


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _signed(n: int) -> int:  # an inode may not fit SQLite's signed 64 bits
    return n - (1 << 64) if n >= 1 << 63 else n


def _encode(counts: Counter[str]) -> bytes:
    return zlib.compress("\n".join(f"{w} {f}" for w, f in counts.items()).encode("utf-8"), 1)


def _decode(blob: bytes) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in zlib.decompress(blob).decode("utf-8").split("\n"):
        if line:
            w, _, f = line.rpartition(" ")
            out[w] = int(f)
    return out


def _damaged(e: sqlite3.Error) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    text = str(e).lower()
    return isinstance(e, _Corrupt) or code in (_CORRUPT, _NOTADB) or "malformed" in text or "not a database" in text


def _busy(e: sqlite3.Error) -> bool:
    return getattr(e, "sqlite_errorcode", None) in _BUSY or "locked" in str(e).lower() or "busy" in str(e).lower()


def _chunks(items: Sequence[str], n: int = 500) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _in_tree(rel: str) -> tuple[Any, str]:
    """The order of the tree (`tree_key`), and the path where two names are the same to the system."""
    return tree_key(rel), rel


def _clock_space() -> str:
    """Which monotonic clock this process reads, where processes of one machine can read different ones: on Linux the time namespace
    (the inode of `/proc/self/ns/time`); "" elsewhere, where the machine has one."""
    try:
        return str(os.stat("/proc/self/ns/time").st_ino)
    except OSError:
        return ""


def _listing_name(path: Path) -> str:
    """The name of the listing the processes of the machine share for an index file: by the file and by the clock its times are read
    on, so that processes on other clocks share none and list for themselves."""
    key = f"{os.path.normcase(os.path.realpath(path))}\0{_clock_space()}"
    return "llmw2-l-" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _epoch_number(epoch: str) -> int:
    return int.from_bytes(hashlib.blake2b(epoch.encode("utf-8"), digest_size=8).digest(), "little")


def _recent(db: sqlite3.Connection) -> list[list[Any]] | None:
    """`recent` as [[generation, [row id, ...]], ...]; None when there is none, or it has any other shape (not JSON, a None of an earlier
    form, paths): whoever reads it reads the file whole, whoever writes it starts again from none."""
    row = db.execute("SELECT v FROM meta WHERE k = 'recent'").fetchone()
    if row is None:
        return None
    try:
        recent = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    good = isinstance(recent, list) and all(
        isinstance(r, list) and len(r) == 2 and type(r[0]) is int and isinstance(r[1], list) and all(type(i) is int for i in r[1])
        for r in recent
    )
    return recent if good else None


class _Changes:
    """What a transaction did, applied to the memory of the process once it is committed."""

    def __init__(self) -> None:
        self.docs: dict[str, tuple[int, int, bool]] = {}  # rel -> (id, length, bad)
        self.stamps: dict[str, Stamp] = {}
        self.gone: list[str] = []
        self.words: set[str] = set()
        self.dirty = False  # the postings or the set of notes changed: other processes must look again


class SearchIndex:
    """BM25 over the notes of a wiki, kept in `path` (None: in memory only). Its calls are serialized by a lock of its own, held only
    while it reads or writes the file, never while a caller embeds or calls a model.

    Who writes notes puts them in (`update`, with the paths a section wrote); who reads never waits for the file's write lock: when the
    file is behind the notes it writes them only if the lock is free, else it answers from what the file holds, without the notes gone
    from the listing. A file never built whole is built (or waited for) by a `sync` that may build; the searches of a wiki (`NoteSearch`)
    leave it to a `Build` in the background and read meanwhile what its lots committed (`sync` with `build=False`)."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._mutex = threading.RLock()
        self._listed_at: int | None = None  # when this process last began to list the wiki to sync (`time.monotonic_ns`)
        self._listed_to = 0  # and when it last ended a listing: a listing of another process taken for its own must begin after
        self._listing: mmap.mmap | None = None  # the listing the processes of the machine share for the file (`_listed_by_another`)
        self._mem: sqlite3.Connection | None = None
        self._verified = False  # and its schema was looked at in full
        self._guarding = False
        self._held: sqlite3.Connection | None = None
        self._held_id: tuple[int, int, int] | None = None  # (pid, device, file number) the held connection was opened as
        self._looks = threading.local()  # `on`: None out of a search of this thread (`looked`), else whether it looked at the file
        self._raw: list[str] = []  # the listing as it came
        self._clear()
        if self.path is None:
            self._to_memory()

    def _clear(self) -> None:
        self._gen: tuple[str, int] | None = None  # (epoch, generation) of the file as this process last saw it
        self._ids: dict[str, int] = {}
        self._rels: dict[int, str] = {}
        self._len: dict[int, int] = {}  # id -> number of words, of the notes that are indexed
        self._stamps: dict[str, Stamp] = {}  # note path -> its file's stamp when read, a file that is not a note too
        self._token = object()  # tells `scan_notes` that `_stamps` is the one it saw last
        self._bad: set[str] = set()
        self._total = 0
        self._hot: dict[str, tuple[array[int], array[int]]] = {}  # word -> (notes, counts), in the order last asked for
        self._hot_n = 0
        self._norm: dict[int, float] = {}
        self._norm_avg = -1.0
        self._groups: dict[str, array[int]] | None = None  # a `fields` text -> the notes that have it, until the file changes
        self._listed_at = None
        self._partial = False  # a writer's section took from the file only its own notes: the rest is to read again before a search
        self._built = False  # the file was built whole once (`meta` "built"): from then on a reader never waits for its write lock
        # and its build listed the wiki once more after that mark, waiting for the writers (`meta` "complete", `Build`): from then on
        # a search reads it whole (`ready`)
        self._complete = False
        self._hidden: set[int] = set()  # notes the file holds that are gone from the listing, left out while a writer holds the lock
        self._order: tuple[Any, list[str]] | None = None  # (generation, notes left out) -> the notes in the order of the tree (`rels`)
        self._raw = []

    def __len__(self) -> int:
        return len(self._len)

    def notes(self) -> int:
        """How many notes the index holds: what this process read of it, else a count read from its file on a connection of its own that
        only reads and never waits (0 with no file, or one that cannot be read now)."""
        if self._len:
            return len(self._len)
        if self.path is None or not self.path.exists():
            return 0
        try:
            with contextlib.closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=0)) as db:
                return int(db.execute("SELECT count(*) FROM docs WHERE bad = 0").fetchone()[0])
        except sqlite3.Error:
            return 0

    def ready(self) -> bool:
        """Whether a search reads this index whole now: its file is complete (`meta` "complete": built whole, then the notes its writers
        left to the build put in, `Build`); an index in memory is built by its sync, as before. One row read, no lock waited for; a file
        not there yet is not made."""
        if self._complete or self._mem is not None or self.path is None:
            return True
        if not self.path.exists():
            return False
        return self._guarded(lambda: self._is_complete(self._open()))

    # ---- the file

    def _to_memory(self) -> None:
        self._close_held()
        self._mem = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self._schema(self._mem)
        self._clear()

    def _ident(self) -> tuple[int, int, int] | None:
        if self.path is None:
            return None
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (os.getpid(), st.st_dev, st.st_ino)

    @contextlib.contextmanager
    def looked(self) -> Iterator[None]:
        """One search: its steps share one look at the file (`_ident`, a `stat`). The first step that opens the file looks, the others of
        this thread trust it (a connection opened meanwhile was looked at when it was opened); a file replaced by another process, or a
        fork, is seen by the first step of the next search, before it reads. The lock is not held across the steps."""
        if getattr(self._looks, "on", None) is not None:  # inside a search already
            yield
            return
        self._looks.on = False
        try:
            yield
        finally:
            self._looks.on = None

    def _close_held(self) -> None:
        held, self._held = self._held, None
        held_id, self._held_id = self._held_id, None
        if held is not None and (held_id is None or held_id[0] == os.getpid()):  # after a fork the parent's connection is not closed here
            held.close()

    def _open(self) -> sqlite3.Connection:
        if self._mem is not None:
            return self._mem
        search = getattr(self._looks, "on", None)
        if self._held is not None:
            if search:  # this search looked at the file already
                return self._held
            if self._held_id == self._ident():
                if search is not None:
                    self._looks.on = True
                return self._held
            self._close_held()  # the file was replaced or removed under it, or this is a fork of the process that opened it
        assert self.path is not None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            ignore = self.path.parent / ".gitignore"  # the caches are not what a wiki is copied or synced for
            if not ignore.exists():
                ignore.write_text("*" + chr(10), encoding="utf-8")
        except OSError as e:  # the place of the file cannot be made: an error of the index file, not of the wiki
            raise sqlite3.OperationalError(f"unable to open database file: {e}") from e
        # held across calls, so used by more than one thread of the process: `_mutex` serializes them
        db = sqlite3.connect(self.path, timeout=BUSY_S, isolation_level=None, check_same_thread=False)
        try:
            db.execute("PRAGMA trusted_schema = OFF")  # no function of a view or trigger of a file we did not make runs
            db.execute("PRAGMA secure_delete = ON")  # a row deleted is zeroed in its page: a purged note leaves no words in the file
            if not self._verified or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
                self._prepare(db)
                self._verified = True
        except BaseException:
            db.close()
            raise
        db.execute("PRAGMA synchronous = NORMAL")  # never OFF: a commit is in the WAL; a power cut may take the last, read again
        db.execute(f"PRAGMA wal_autocheckpoint = {CHECKPOINT_PAGES}")
        self._held = db
        self._held_id = self._ident()
        if search is not None:
            self._looks.on = True
        if self._listing is None:
            self._listing = shared_bytes(_listing_name(self.path), _LISTED.size)
        return db

    def _prepare(self, db: sqlite3.Connection) -> None:
        """A file seen for the first time by this process: WAL (the file keeps it once set; the page size is that of a file still to be
        made) and the tables."""
        if str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            db.execute(f"PRAGMA page_size={PAGE_SIZE}")
            for attempt in range(100):  # a switch of the journal mode does not wait for the others
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as e:
                    if not _busy(e) or attempt == 99:
                        raise
                    time.sleep(0.05)
        self._schema(db)

    @staticmethod
    def _current(db: sqlite3.Connection) -> bool:
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
                return False
            if _master(db) != _expected_master():  # a file received may hold views or triggers: only our tables are accepted
                return False
            meta = dict(db.execute("SELECT k, v FROM meta"))
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return False
            raise
        # "secure": made with `secure_delete` from the start; a file made without it may keep words of deleted rows in free pages, and is
        # made again
        return meta.get("tokens") == lexical._WORD.pattern and {"epoch", "gen", "total", "secure"} <= meta.keys()

    def _schema(self, db: sqlite3.Connection) -> None:
        """The tables of this schema, made (or made again when the file is of another schema or tokenizer) under the write lock."""
        if self._current(db):
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            if not self._current(db):
                ours = {"meta", "docs", "docbody", "postings", "links"}
                if any(name not in ours and not name.startswith("sqlite_") for _, name, _ in _master(db)):
                    raise _Corrupt("the file defines what this schema does not: it is thrown away, not edited")
                self._drop_all(db)
                for statement in _DDL:
                    db.execute(statement)
                rows = [("gen", 0), ("total", 0), ("tokens", lexical._WORD.pattern), ("epoch", os.urandom(8).hex()), ("secure", 1)]
                db.executemany("INSERT INTO meta VALUES (?, ?)", rows)
                db.execute(f"PRAGMA user_version = {SCHEMA}")
            db.execute("COMMIT")
        except BaseException:
            self._rollback(db)
            raise

    @staticmethod
    def _drop_all(db: sqlite3.Connection) -> None:
        """Everything the file defines goes (tables, views, triggers, indexes): a file that is not exactly ours is not trusted."""
        for kind, name, _ in db.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
            if kind in ("trigger", "view", "table"):
                db.execute('DROP {} IF EXISTS "{}"'.format(kind.upper(), name.replace('"', '""')))

    @staticmethod
    def _rollback(db: sqlite3.Connection) -> None:
        with contextlib.suppress(sqlite3.Error):
            db.execute("ROLLBACK")

    def _discard(self) -> None:
        """Put the file aside (with its journal files, as `.bad`: they go together, a stale journal must not meet a new file): it is
        rebuilt by the next sync. Another process that holds it open goes on with the file it holds and finds the new one at its next
        open. Where it cannot be moved (Windows holds it) the index lives in memory for this process."""
        self._close_held()
        self._clear()
        self._verified = False
        assert self.path is not None
        for suffix in ("", "-wal", "-shm"):
            target = Path(f"{self.path}{suffix}")
            for attempt in range(20):
                try:
                    if target.exists():
                        os.replace(target, f"{target}.bad")
                    break
                except OSError:
                    if attempt == 19:
                        log.warning("search index %s cannot be removed: this process keeps its own in memory", self.path)
                        self._to_memory()
                        return
                    time.sleep(0.1)

    def run(self, work: Callable[[], T], *, reading: bool = False) -> T:
        """Run `work`, which may sync and ask the index; if the file turns out to be damaged `work` runs again from its start on a new
        one (see `_guarded`). `search` runs a whole search so: a damage found by the last step does not leave the answer to a state
        that was just thrown away. `reading`: `work` only needs to read (its sync never waits for the write lock); when another process
        wrote while it read, it runs again inside one read transaction, which sees one state of the file whatever is written meanwhile."""
        return self._guarded(work, reading)

    @contextlib.contextmanager
    def held(self, wait: float | None) -> Iterator[bool]:
        """This process's lock of the index (a build or a search of another thread holds it), waiting at most `wait` seconds (None: as
        long as it takes); yields whether it is held. The calls made inside take it again at once."""
        got = self._mutex.acquire(timeout=-1 if wait is None else wait)
        try:
            yield got
        finally:
            if got:
                self._mutex.release()

    def forget(self, store: WikiStore, read: Reader, wait: float | None) -> bool:
        """After a purge: the notes gone from the wiki go from the file (their rows zeroed), the WAL is written into the file and cut to
        nothing (`wal_checkpoint(TRUNCATE)`), and the files put aside as `.bad` are deleted. `wait`: the most it waits in all, for the
        other threads of this process, the write lock and the readers of other processes (a purge's budget); None: the locks as long as
        they take, the readers not at all. Whether it was done: False when a lock did not come in time, a reader held the WAL all along
        (the words of the notes purged stay in it until the next checkpoint) or a `.bad` file could not go."""
        end = None if wait is None else time.monotonic() + wait

        def left() -> float | None:
            return None if end is None else max(0.0, end - time.monotonic())

        with self.held(wait) as got:
            if not got or not self._guarded(lambda: self._sync(store, read, True, True, end)):
                return False
            return self._forget(left() or 0.0)

    def _forget(self, wait: float) -> bool:
        done = True
        if self._mem is None and self.path is not None and self.path.exists():
            db = self._open()
            # short tries until `wait` is over: SQLite's own wait counts once per lock, and a checkpoint takes more than one
            end = time.monotonic() + wait
            db.execute("PRAGMA busy_timeout = 100")
            try:
                while True:
                    try:
                        busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                    except sqlite3.OperationalError as e:
                        if not _busy(e):
                            raise
                        busy = 1
                    if not busy or time.monotonic() >= end:
                        break
                    time.sleep(0.1)
            finally:
                db.execute(f"PRAGMA busy_timeout = {BUSY_S * 1000}")
            done = not busy
        if self.path is not None:
            for bad in self.path.parent.glob(f"{self.path.name}*.bad"):
                try:
                    bad.unlink()
                except OSError:  # held by a process that has not seen the new file yet (Windows)
                    done = False
        return done

    def _guarded(self, work: Callable[[], T], reading: bool = False) -> T:
        """Run `work`; a damaged file is thrown away and `work` runs again on a new one; a file that cannot be used (read-only,
        no space, no I/O) is replaced by an index in memory. A file busy for too long is an error to the caller. Inside another
        `_guarded` the error goes up to it, which starts `work` again from its first step."""
        with self._mutex:
            if self._guarding:
                return work()
            self._guarding = True
            try:
                return self._guard_loop(work, reading)
            finally:
                self._guarding = False

    def _guard_loop(self, work: Callable[[], T], reading: bool = False) -> T:
        snapshot = False
        for attempt in range(6):
            try:
                if not snapshot:
                    return work()
                # a writer that writes more often than `work` reads made it stale again and again: one state of the file for all of it
                db = self._open()
                db.execute("BEGIN")
                try:
                    return work()
                finally:
                    # closed meanwhile when the file was made again under it (`_open`): nothing of that state is held any more
                    with contextlib.suppress(sqlite3.ProgrammingError):
                        if db.in_transaction:
                            db.execute("COMMIT")
            except _Stale:
                # what this process holds is still the file as of `_gen`: the next sync takes what was written since (`recent`), or
                # all of it; the postings read meanwhile go with the state read again (`_read_again`)
                snapshot = reading
            except sqlite3.DatabaseError as e:
                if self._mem is not None or _busy(e):
                    raise
                if "no such table" in str(e) and attempt < 2:  # the file was made again by another process under this one: look again
                    self._close_held()
                    self._clear()
                    self._verified = False
                elif _damaged(e) and attempt < 2:
                    log.warning("search index %s is damaged (%s): built again", self.path, e)
                    self._discard()
                elif isinstance(e, sqlite3.OperationalError):
                    log.warning("search index %s cannot be used (%s): this process keeps its own in memory", self.path, e)
                    self._to_memory()
                else:
                    raise
        raise sqlite3.DatabaseError("the search index cannot be rebuilt")

    # ---- state in memory

    @staticmethod
    def _disk_gen(db: sqlite3.Connection) -> tuple[str, int]:
        """Which file this is (it gets a new name when it is made again) and how many times a process changed what it holds."""
        meta = dict(db.execute("SELECT k, v FROM meta WHERE k IN ('epoch', 'gen')"))
        if meta.keys() != {"epoch", "gen"}:
            raise _Corrupt("meta")
        return str(meta["epoch"]), int(meta["gen"])

    def _load(self, db: sqlite3.Connection) -> None:
        """Read what the file holds of every note (not its words): path, stamp, length. Checks that it adds up."""
        gen = self._disk_gen(db)
        total = int(db.execute("SELECT v FROM meta WHERE k = 'total'").fetchone()[0])
        ids, rels, lens, stamps, bad = {}, {}, {}, {}, set()
        for id_, rel, mtime, size, ino, length, is_bad in db.execute("SELECT id, rel, mtime, size, ino, len, bad FROM docs"):
            ids[rel] = id_
            rels[id_] = rel
            stamps[rel] = (mtime, size, ino & _MASK)
            if is_bad:
                bad.add(rel)
            else:
                lens[id_] = length
        if sum(lens.values()) != total:
            raise _Corrupt("the lengths of the notes do not add up")
        self._gen, self._ids, self._rels, self._len, self._stamps, self._bad, self._total = gen, ids, rels, lens, stamps, bad, total
        self._built = db.execute("SELECT 1 FROM meta WHERE k = 'built'").fetchone() is not None
        self._complete = db.execute("SELECT 1 FROM meta WHERE k = 'complete'").fetchone() is not None
        self._read_again()

    def _read_again(self) -> None:
        """After the state of the notes was read again from the file (`_load`, `_caught_up`): what was derived from the old one goes."""
        if self._hidden:  # notes gone from the files that the file still holds: the next search lists the files again to leave them out
            self._listed_at = None
        self._hidden = set()
        self._partial = False
        self._token = object()
        self._hot, self._hot_n, self._norm, self._norm_avg = {}, 0, {}, -1.0
        self._groups = None
        # the listing (`_raw`) stays: it says what the files were when they were listed, whoever wrote the file since; a reader that
        # listed a moment ago does not list again because another process wrote (`LISTING_LOOK`)

    def close(self) -> None:
        """Let go of the file, and of the listing shared for it."""
        with self._mutex:
            self._close_held()
            listing, self._listing = self._listing, None
            if listing is not None:
                listing.close()

    def _scan(self, store: WikiStore) -> tuple[list[str], list[tuple[str, str, Stamp]]]:
        return store.scan_notes(self._stamps, time.time_ns() - RACY_NS, self._token)

    # ---- sync

    def sync(self, store: WikiStore, read: Reader, *, wait: bool = True, build: bool = True) -> None:
        """Bring the index to the files of the wiki: read and index the notes new or changed since, take out the notes that went.
        `wait` false (a reader): once the file was built whole, the lock is taken only if free; if it is not, the index answers from what
        the file holds, without the notes gone from the listing (a writer holds the lock to put in what it changed). `build` false:
        nothing on a file never built whole."""
        self._guarded(lambda: self._sync(store, read, wait, build))

    def update(self, store: WikiStore, written: Iterable[str], read: Reader) -> None:
        """Put in the notes among `written` (bundle paths a section of this process wrote, created or deleted) as they are on disk now,
        with no listing of the wiki; waits for the lock `WRITTEN_WAIT` at most, then leaves them to the next look (an error then). A file
        never built whole is left to its first reader."""
        rels = [rel for rel in dict.fromkeys(written) if store.is_note_path(rel)]
        if not rels:
            return
        if not self._mutex.acquire(timeout=WRITTEN_WAIT):  # a thread of this process holds the index (a build): not this section's wait
            raise sqlite3.OperationalError(f"database is locked: the index is busy in this process ({WRITTEN_WAIT} s)")
        try:
            self._guarded(lambda: self._update(store, rels, read))
        finally:
            self._mutex.release()

    def _update(self, store: WikiStore, rels: list[str], read: Reader) -> None:
        db = self._open()
        if not self._is_built(db):
            return

        def look() -> tuple[None, list[tuple[str, str, Stamp]], list[str]]:
            todo: list[tuple[str, str, Stamp]] = []
            gone: list[str] = []
            for rel in rels:
                path = store.root / rel
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    if rel in self._stamps:
                        gone.append(rel)
                    continue
                todo.append((rel, str(path), (st.st_mtime_ns, st.st_size, st.st_ino)))
            return None, todo, gone

        # under the lock, what the file holds of these notes alone is read (another process may have written them): the bundle's lock is
        # held, so no listing and no reading of the whole file; what the others wrote since comes from `recent`, else a search of this
        # process reads the rest again (`_partial`)
        def catch_up() -> None:
            if self._gen is None or self._partial or self._disk_gen(db) == self._gen or not self._caught_up(db):
                self._catch_up(db, rels)

        if not self._write(db, read, [], [], look, WRITTEN_WAIT, catch_up=catch_up)[0]:
            raise sqlite3.OperationalError(f"database is locked: what a section wrote waits for the next look ({WRITTEN_WAIT} s)")

    def _catch_up(self, db: sqlite3.Connection, rels: list[str]) -> None:
        """The write lock is held: take from the file its generation, its total and the rows of `rels`, as they are now. What this process
        holds of the other notes may be older: it is read again whole before a search (`_partial`)."""
        gen = self._disk_gen(db)
        if self._gen != gen:  # what this process holds is not the file as of `gen` (or nothing yet)
            self._partial = True
        for rel in rels:
            id_ = self._ids.pop(rel, None)
            if id_ is not None:
                self._rels.pop(id_, None)
                self._len.pop(id_, None)
            self._stamps.pop(rel, None)
            self._bad.discard(rel)
        for chunk in _chunks(rels):
            marks = ",".join("?" * len(chunk))
            for id_, rel, mtime, size, ino, length, is_bad in db.execute(
                f"SELECT id, rel, mtime, size, ino, len, bad FROM docs WHERE rel IN ({marks})", chunk
            ):
                self._ids[rel] = id_
                self._rels[id_] = rel
                self._stamps[rel] = (mtime, size, ino & _MASK)
                if is_bad:
                    self._bad.add(rel)
                else:
                    self._len[id_] = length
        self._total = int(db.execute("SELECT v FROM meta WHERE k = 'total'").fetchone()[0])
        self._gen = gen

    def _caught_up(self, db: sqlite3.Connection) -> bool:
        """Another process wrote the file since this one read it: take only the rows it wrote, which `recent` names by id for each
        generation since, read in one state of the file. False (read it whole) when `recent` does not cover every generation since (a
        write of many rows, an older writer, another file) or what it gives does not add up."""
        own = not db.in_transaction
        if own:
            db.execute("BEGIN")
        try:
            assert self._gen is not None
            gen = self._disk_gen(db)
            kept = _recent(db) if gen[0] == self._gen[0] else None
            recent = {g: ids for g, ids in kept or []}  # none, or of another shape: the file is read whole
            wanted = range(self._gen[1] + 1, gen[1] + 1)
            if not wanted or any(recent.get(g) is None for g in wanted):
                return False
            ids = sorted({i for g in wanted for i in recent[g]})
            for id_ in ids:  # what this process held of these rows goes; what the file holds of them now comes
                rel = self._rels.pop(id_, None)
                self._len.pop(id_, None)
                if rel is not None and self._ids.get(rel) == id_:
                    del self._ids[rel]
                    self._stamps.pop(rel, None)
                    self._bad.discard(rel)
            for chunk in _chunks(ids):
                marks = ",".join("?" * len(chunk))
                for id_, rel, mtime, size, ino, length, is_bad in db.execute(
                    f"SELECT id, rel, mtime, size, ino, len, bad FROM docs WHERE id IN ({marks})", chunk
                ):
                    self._ids[rel] = id_
                    self._rels[id_] = rel
                    self._stamps[rel] = (mtime, size, ino & _MASK)
                    if is_bad:
                        self._bad.add(rel)
                    else:
                        self._bad.discard(rel)
                        self._len[id_] = length
            self._total = int(db.execute("SELECT v FROM meta WHERE k = 'total'").fetchone()[0])
            if sum(self._len.values()) != self._total:
                self._gen = None
                return False
            self._gen = gen
            self._built = db.execute("SELECT 1 FROM meta WHERE k = 'built'").fetchone() is not None
            self._complete = db.execute("SELECT 1 FROM meta WHERE k = 'complete'").fetchone() is not None
        finally:
            if own and db.in_transaction:
                db.execute("COMMIT")
        self._read_again()
        return True

    def _still_racy(self, todo: list[tuple[str, str, Stamp]]) -> list[tuple[str, str, Stamp]]:
        """A reader's notes to write, less those the file holds as written moments ago (`RACY_NS`: kept with a stamp that never
        matches) with the same size and still that recent: the writer that wrote them put them in, and they are read again once they
        age. A file changed by hand within that time with the same size is so seen when it ages, not at once."""
        racy = time.time_ns() - RACY_NS
        keep = []
        for rel, path, stamp in todo:
            held = self._stamps.get(rel)
            if not (stamp[0] >= racy and held is not None and held[0] == 0 and held[1] == stamp[1]):
                keep.append((rel, path, stamp))
        return keep

    def _is_built(self, db: sqlite3.Connection) -> bool:
        """Whether the file was built whole once; read from the file again while it was not (another process may have built it)."""
        if not self._built:
            self._built = db.execute("SELECT 1 FROM meta WHERE k = 'built'").fetchone() is not None
        return self._built

    def _is_complete(self, db: sqlite3.Connection) -> bool:
        if not self._complete:
            self._complete = db.execute("SELECT 1 FROM meta WHERE k = 'complete'").fetchone() is not None
        return self._complete

    def _sync(self, store: WikiStore, read: Reader, wait: bool, build: bool, until: float | None = None) -> bool:
        """`until`: the write lock is waited for until then (`time.monotonic`), all the lots together, whatever `wait` says. Whether the
        file has every change."""
        db = self._open()
        held = self._gen is not None and not self._partial  # what this process holds is the file as of `_gen`
        if not held or (self._disk_gen(db) != self._gen and not self._caught_up(db)):  # another process wrote: what it wrote, or all
            self._load(db)
        if not build and not self._is_built(db):
            return True
        now = time.monotonic_ns()
        if not wait and self._built:
            if self._raw and self._listed_at is not None and now - self._listed_at < LISTING_LOOK * 1e9:
                return True  # listed a moment ago: what the library wrote since is in the file (`update`), a hand edit waits
            if self._listed_by_another(now):
                return True  # another process of the machine did, and what it found is in the file as this one holds it
        order, todo = self._scan(store)
        self._listed_at = now
        self._hidden = set()
        done = True
        live = set(order)
        if todo or order != self._raw or any(rel not in live for rel in self._stamps):  # a note gone: written, or left out

            def look() -> tuple[list[str], list[tuple[str, str, Stamp]], list[str]]:
                order, todo = self._scan(store)
                live = set(order)
                return order, todo, [rel for rel in self._stamps if rel not in live]

            gone = [rel for rel in self._stamps if rel not in live]
            busy = 0.0 if until is not None or (not wait and self._is_built(db)) else None
            if busy is not None and todo:
                todo = self._still_racy(todo)
            done, seen = self._write(db, read, todo, gone, look, busy, whole=True, until=until)
            if seen is not None:
                order = seen
            if not done:  # a writer holds the lock: what the file holds, less the notes gone from the listing
                live = set(order)
                self._hidden = {self._ids[rel] for rel in self._stamps if rel not in live and rel in self._ids}
        self._listed_to = time.monotonic_ns()
        if done and not self._is_built(db):
            self._mark_built(db)  # whole, and not marked yet (a file built before the mark existed)
        self._raw = order
        if done and self._built:
            self._publish(now)
        return done

    def _publish(self, began: int) -> None:
        """This process listed the wiki (from `began`) and the file holds all it found, as of the generation it holds now: the other
        processes of the machine may take that listing for theirs (`_listed_by_another`). Numbers only, never a path."""
        if self._listing is None or self._gen is None:
            return
        epoch, gen = _epoch_number(self._gen[0]), self._gen[1]
        with contextlib.suppress(ValueError, struct.error):
            self._listing[:] = _LISTED.pack(epoch, gen, began, epoch ^ gen ^ began ^ _LISTED_MASK)

    def _listed_by_another(self, now: int) -> bool:
        """Whether a listing another process of the machine published stands for one of this process: of this file, all it found in the
        file as this process holds it (a generation not after its own), begun less than `LISTING_LOOK` ago and after the last listing of
        this process ended (a note this one saw gone and left out does not come back by an older one). A hand edit is so seen within
        `LISTING_LOOK` as before. A process that does not see the listing (another Windows session) lists for itself."""
        if self._listing is None or self._gen is None:
            return False
        try:
            epoch, gen, began, check = _LISTED.unpack_from(self._listing, 0)
        except (ValueError, struct.error):
            return False
        return (
            check == epoch ^ gen ^ began ^ _LISTED_MASK
            and epoch == _epoch_number(self._gen[0])
            and gen <= self._gen[1]
            and self._listed_to < began <= now < began + LISTING_LOOK * 1e9
        )

    def _begin(self, db: sqlite3.Connection, wait: float | None = None) -> bool:
        """Take the write lock, waiting for the others (`BUSY_S` each time, a few times over) before giving up; with `wait`, that long
        once, and whether it was taken."""
        if db.in_transaction:  # a reader inside one state of the file (`run`, `reading`): it does not write, or ends that state first
            if wait is not None:
                return False
            db.execute("COMMIT")
        if wait is not None:
            db.execute(f"PRAGMA busy_timeout = {int(wait * 1000)}")
            try:
                db.execute("BEGIN IMMEDIATE")
                return True
            except sqlite3.OperationalError as e:
                if not _busy(e):
                    raise
                return False
            finally:
                db.execute(f"PRAGMA busy_timeout = {BUSY_S * 1000}")
        for attempt in range(LOCK_TRIES):
            try:
                db.execute("BEGIN IMMEDIATE")
                return True
            except sqlite3.OperationalError as e:
                if not _busy(e) or attempt == LOCK_TRIES - 1:
                    raise
        return False

    def _note_recent(self, db: sqlite3.Connection, gen: int, changes: _Changes) -> None:
        """The write lock is held, before `_apply`: `recent` keeps, for the last generations, the ids of the rows this one wrote (ids,
        never paths: a purged note leaves no name in the file), at most RECENT_IDS in all, a generation with none counted as one; a
        generation of more is not kept (a reader that does not find it reads the file whole)."""
        ids = {doc[0] for doc in changes.docs.values()}
        ids.update(self._ids[rel] for rel in [*changes.stamps, *changes.gone] if rel in self._ids)
        recent = [r for r in _recent(db) or [] if r[0] < gen]  # a `recent` of another shape (another version, a file sent): from none
        if len(ids) <= RECENT_IDS:  # a generation of more rows is not kept: a reader that does not find it reads the file whole
            recent.append([gen, sorted(ids)])
        while recent and sum(max(1, len(r[1])) for r in recent) > RECENT_IDS:  # each generation counts, one with no id too
            recent.pop(0)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('recent', ?)", (json.dumps(recent, separators=(",", ":")),))

    def _mark_built(self, db: sqlite3.Connection, key: str = "built") -> None:
        self._begin(db)
        try:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, 1)", (key,))
            db.execute("COMMIT")
        except BaseException:
            self._rollback(db)
            raise
        if key == "built":
            self._built = True
        else:
            self._complete = True

    def _write(
        self,
        db: sqlite3.Connection,
        read: Reader,
        todo: list[tuple[str, str, Stamp]],
        gone: list[str],
        look: Callable[[], tuple[list[str] | None, list[tuple[str, str, Stamp]], list[str]]],
        wait: float | None = None,
        *,
        whole: bool = False,
        catch_up: Callable[[], None] | None = None,
        until: float | None = None,
    ) -> tuple[bool, list[str] | None]:
        """Write what changed, `BATCH` notes to a transaction: a build of a large wiki is a series of short locks, what was committed
        stays if the process stops, and another process that opens the wiki meanwhile takes the next lots instead of waiting. `look`
        says again what to write (and the listing, when there is one) if another process wrote while this waited; `wait`: as long
        as that for each lot (`_begin`); `whole`: what was to write is every change of the wiki, and the file is marked built once
        it is in. `until`: in place of `wait`, the lots wait for the lock until then, all together (a purge's budget). `catch_up`: what
        to read of the file once the lock is held, before `look` says what to write (a writer's own notes);
        without it, the whole state when another process wrote meanwhile. Whether all of it was written, and the listing `look` gave last
        (None if it was not called)."""
        seen: list[str] | None = None
        if not todo and not gone and catch_up is None:
            return True, seen
        if len(todo) > BATCH:  # a build touches pages of the postings all over the file: a cache of them saves reads and writes
            db.execute(f"PRAGMA cache_size = -{BUILD_CACHE_KB}")
        pos = 0
        while pos < len(todo) or gone or catch_up is not None:
            if not self._begin(db, wait if until is None else max(0.0, until - time.monotonic())):
                return False, seen
            try:
                if catch_up is not None or self._disk_gen(db) != self._gen:  # another process wrote: start again from the file
                    if catch_up is not None:
                        catch_up()
                        catch_up = None
                    else:
                        self._load(db)
                    seen, todo, gone = look()
                    pos = 0
                    if not todo and not gone:
                        db.execute("COMMIT")
                        break
                changes = _Changes()
                self._index_batch(db, read, todo[pos : pos + BATCH], changes)
                last = pos + BATCH >= len(todo)
                if last:
                    for rel in gone:
                        self._remove(db, rel, changes)
                    gone = []
                    if whole and not self._built:
                        db.execute("INSERT OR REPLACE INTO meta VALUES ('built', 1)")
                gen, total = self._gen, self._total
                if changes.dirty:
                    assert gen is not None
                    gen = (gen[0], gen[1] + 1)
                    total += sum(length for _, length, bad in changes.docs.values() if not bad) - self._lengths_of(changes)
                    db.execute("UPDATE meta SET v = ? WHERE k = 'gen'", (gen[1],))
                    db.execute("UPDATE meta SET v = ? WHERE k = 'total'", (total,))
                    self._note_recent(db, gen[1], changes)
                db.execute("COMMIT")
            except BaseException:
                self._rollback(db)
                self._gen = None
                raise
            self._apply(changes, gen, total)
            if last and whole:
                self._built = True
            pos += BATCH
        return True, seen

    def _lengths_of(self, changes: _Changes) -> int:
        """The lengths, before this transaction, of the notes it changed or took out."""
        return sum(self._len.get(self._ids[rel], 0) for rel in {*changes.docs, *changes.gone} if rel in self._ids)

    def _apply(self, changes: _Changes, gen: tuple[str, int] | None, total: int) -> None:
        """Take into memory what a committed transaction did."""
        self._total = total
        for rel in changes.gone:
            id_ = self._ids.pop(rel, None)
            if id_ is not None:
                self._rels.pop(id_, None)
                self._len.pop(id_, None)
            self._stamps.pop(rel, None)
            self._bad.discard(rel)
        for rel, (id_, length, bad) in changes.docs.items():
            self._ids[rel] = id_
            self._rels[id_] = rel
            if bad:
                self._len.pop(id_, None)
                self._bad.add(rel)
            else:
                self._len[id_] = length
                self._bad.discard(rel)
        self._stamps.update(changes.stamps)
        self._gen = gen
        if changes.dirty:
            self._norm, self._norm_avg = {}, -1.0
            self._groups = None
            for w in changes.words:
                posting = self._hot.pop(w, None)
                if posting is not None:
                    self._hot_n -= len(posting)

    def _existing(self, db: sqlite3.Connection, rels: list[str]) -> dict[str, tuple[int, str, bool, str, str]]:
        out: dict[str, tuple[int, str, bool, str, str]] = {}
        for chunk in _chunks(rels):
            marks = ",".join("?" * len(chunk))
            for rel, id_, digest, bad, fields, links in db.execute(
                f"SELECT d.rel, d.id, d.hash, d.bad, b.fields, b.links FROM docs d JOIN docbody b ON b.id = d.id WHERE d.rel IN ({marks})",
                chunk,
            ):
                out[rel] = (id_, digest, bool(bad), fields, links)
        return out

    @staticmethod
    def _relink(db: sqlite3.Connection, id_: int, old: str, new: str) -> None:
        """The rows of `links` of a note whose links were `old` and are `new` (`pack_links`): only those that changed are written."""
        was, now = _unpack_links(old), _unpack_links(new)
        db.executemany("DELETE FROM links WHERE target = ? AND doc = ?", sorted((t, id_) for t in was - now))
        db.executemany("INSERT OR REPLACE INTO links VALUES (?, ?)", sorted((t, id_) for t in now - was))

    def _index_batch(self, db: sqlite3.Connection, read: Reader, chunk: list[tuple[str, str, Stamp]], changes: _Changes) -> None:
        """Read, hash and count the notes of a batch, a phase at a time, then write what differs from the file."""
        got: list[tuple[str, str, Stamp, tuple[str, str, str, str, str] | None]] = []
        for rel, path, stamp in chunk:
            try:
                got.append((rel, path, stamp, read(rel, path, stamp)))
            except OSError:  # gone since the listing, or being replaced by another process: the next listing says which
                continue
        existing = self._existing(db, [g[0] for g in got])
        read_at = time.time_ns()
        dels: list[tuple[str, int]] = []
        puts: list[tuple[str, int, int]] = []  # the rows of a batch are written together, in the order of the file
        for rel, _path, stamp, entry in got:
            row = existing.get(rel)
            # a file read within RACY_NS of its time may be written again with the same time and size: its stamp is kept as one that
            # never matches, so the next listing reads it again (v1.0.2 caches a note only once it is older, `_parsed`)
            if stamp[0] >= read_at - RACY_NS:
                stamp = (0, stamp[1], stamp[2])
            mtime, size, ino = stamp[0], stamp[1], _signed(stamp[2])
            if entry is None:  # not a note: not read again until its file changes
                if row is not None and not row[2]:  # a note that became a file that is not one
                    self._remove(db, rel, changes)
                    row = None
                if row is None:
                    cur = db.execute(
                        "INSERT INTO docs (rel, mtime, size, ino, len, bad, hash) VALUES (?, ?, ?, ?, 0, 1, '')", (rel, mtime, size, ino)
                    )
                    db.execute("INSERT INTO docbody VALUES (?, '', '', ?, 'null', '')", (cur.lastrowid, b""))
                    changes.docs[rel] = (int(cur.lastrowid or 0), 0, True)
                    changes.dirty = True
                else:
                    db.execute("UPDATE docs SET mtime = ?, size = ?, ino = ? WHERE id = ?", (mtime, size, ino, row[0]))
                    changes.docs[rel] = (row[0], 0, True)
                    changes.dirty = True  # a stamp the other processes hold too (`recent`)
                changes.stamps[rel] = stamp
                continue
            text, title, summary, fields, links = entry
            digest = _digest(text)
            if row is not None and not row[2] and row[1] == digest:  # the same text: its time changed, its `fields` or its links alone
                if self._stamps.get(rel) != stamp:
                    db.execute("UPDATE docs SET mtime = ?, size = ?, ino = ? WHERE id = ?", (mtime, size, ino, row[0]))
                    changes.dirty = True  # a stamp the other processes hold too (`recent`)
                if row[3] != fields:  # what decides whether a note changed holds its `fields` too
                    db.execute("UPDATE docbody SET fields = ? WHERE id = ?", (fields, row[0]))
                    changes.dirty = True
                if row[4] != links:  # the See-also section is not in the text: a link made or dropped changes the links alone
                    db.execute("UPDATE docbody SET links = ? WHERE id = ?", (links, row[0]))
                    self._relink(db, row[0], row[4], links)
                    changes.dirty = True
                changes.stamps[rel] = stamp
                continue
            counts = Counter(lexical.words(text))
            length = sum(counts.values())
            changes.dirty = True
            if row is None:
                cur = db.execute(
                    "INSERT INTO docs (rel, mtime, size, ino, len, bad, hash) VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (rel, mtime, size, ino, length, digest),
                )
                id_ = int(cur.lastrowid or 0)
                db.execute("INSERT INTO docbody VALUES (?, ?, ?, ?, ?, ?)", (id_, title, summary, _encode(counts), fields, links))
                self._relink(db, id_, "", links)
                puts.extend((w, id_, f) for w, f in counts.items())
                changes.words.update(counts)
            else:
                id_ = row[0]
                old = _decode(db.execute("SELECT counts FROM docbody WHERE id = ?", (id_,)).fetchone()[0]) if not row[2] else {}
                db.execute(
                    "UPDATE docs SET mtime = ?, size = ?, ino = ?, len = ?, bad = 0, hash = ? WHERE id = ?",
                    (mtime, size, ino, length, digest, id_),
                )
                db.execute(
                    "UPDATE docbody SET title = ?, summary = ?, counts = ?, fields = ?, links = ? WHERE id = ?",
                    (title, summary, _encode(counts), fields, links, id_),
                )
                self._relink(db, id_, row[4], links)
                dropped = [(w, id_) for w in old if w not in counts]  # only the rows whose count changed are written
                changed = [(w, id_, f) for w, f in counts.items() if old.get(w) != f]
                dels.extend(dropped)
                puts.extend(changed)
                changes.words.update(w for w, _ in dropped)
                changes.words.update(w for w, _, _ in changed)
            changes.docs[rel] = (id_, length, False)
            changes.stamps[rel] = stamp
        db.executemany("DELETE FROM postings WHERE word = ? AND doc = ?", sorted(dels))
        db.executemany("INSERT OR REPLACE INTO postings VALUES (?, ?, ?)", sorted(puts))

    def _remove(self, db: sqlite3.Connection, rel: str, changes: _Changes) -> None:
        """Take a note out of the file: its postings (its words are in its body row), its links and its rows."""
        id_ = self._ids.get(rel)
        if id_ is None:
            return
        if rel not in self._bad:
            blob = db.execute("SELECT counts, links FROM docbody WHERE id = ?", (id_,)).fetchone()
            old = _decode(blob[0]) if blob else {}
            db.executemany("DELETE FROM postings WHERE word = ? AND doc = ?", ((w, id_) for w in old))
            changes.words.update(old)
            if blob:
                self._relink(db, id_, blob[1], "")
        db.execute("DELETE FROM docbody WHERE id = ?", (id_,))
        db.execute("DELETE FROM docs WHERE id = ?", (id_,))
        changes.gone.append(rel)
        changes.dirty = True

    # ---- queries

    def _postings(self, words: Iterable[str]) -> dict[str, tuple[array[int], array[int]]]:
        """For each word the notes that hold it and how many times, as two arrays (8 bytes a note: the common words of a large wiki are
        in every query). Kept while no process writes; the least recently asked for words go when the memory is full."""
        found: dict[str, tuple[array[int], array[int]]] = {}
        hot = self._hot
        for w in words:
            posting = hot.pop(w, None)
            if posting is not None:
                hot[w] = found[w] = posting  # asked for now: the last to go
        missing = [w for w in words if w not in found]
        if missing:
            db = self._open()
            for w in missing:
                rows = db.execute("SELECT doc, f FROM postings WHERE word = ?", (w,)).fetchall()
                posting = (array("I", [r[0] for r in rows]), array("I", [r[1] for r in rows]))
                while hot and self._hot_n + len(rows) > HOT_ENTRIES:
                    self._hot_n -= len(hot.pop(next(iter(hot)))[0])
                hot[w] = found[w] = posting
                self._hot_n += len(rows)
            if self._disk_gen(db) != self._gen:  # a process wrote since this one synced: the postings may hold notes it does not know
                raise _Stale
        return found

    def matching(self, where: dict[str, Any]) -> frozenset[int]:
        """The notes whose `fields` hold every key of `where` (`fields_match`, the one definition: the tree filter's), from the fields the
        file keeps, with no note read. The notes are grouped by their fields text in one pass, kept until the file changes: each `where`
        then judges the distinct texts (as many as the areas, not the notes), whatever `where` comes next."""
        groups = self._groups
        if groups is None:
            groups = {}
            db = self._open()
            for id_, text in db.execute("SELECT id, fields FROM docbody"):
                if id_ in self._len:
                    got = groups.get(text)
                    if got is None:
                        got = groups[text] = array("q")
                    got.append(id_)
            if self._disk_gen(db) != self._gen:  # a process wrote since this one synced
                raise _Stale
            self._groups = groups
        ids: set[int] = set()
        for text, notes in groups.items():
            if fields_match(unpack_fields(text), where):
                ids.update(notes)
        return frozenset(ids)

    def rels(self, where: dict[str, Any] | None = None) -> list[str]:
        """The paths of the notes the file holds that `where` keeps (every note without it), in the order of the tree (`tree_key`), less
        those gone from the listing while a writer holds the file. They are what this process read of the file, listed or not: a note
        another process put in is among them as soon as this process reads the file again."""
        ids = self._guarded(lambda: self.matching(where)) if where else None
        key = (self._gen, frozenset(self._hidden))
        if self._order is None or self._order[0] != key or self._gen is None:
            self._order = (key, self._ordered())
        order = self._order[1]
        return list(order) if ids is None else [rel for rel in order if self._ids.get(rel) in ids]

    def _ordered(self) -> list[str]:
        """The notes the file holds less those left out, in the order of the tree: the order kept for the state before, the notes gone
        from it taken out and the new ones put in their place, so that a write of a few notes costs a pass, not a sort of them all."""
        now = {self._rels[i] for i in self._len if i not in self._hidden}
        if self._order is None:
            return sorted(now, key=_in_tree)
        kept = [rel for rel in self._order[1] if rel in now]
        for rel in now.difference(kept):
            bisect.insort(kept, rel, key=_in_tree)
        return kept

    def rank(self, query: str, n: int, where: dict[str, Any] | None = None) -> list[str]:
        """The paths of the `n` best notes for `query`, best first, ties in the order of the tree. `where`: only the notes that hold it
        are ranked, as an index of just them would (their count, word frequencies and average length)."""
        return [rel for rel, _ in self._guarded(lambda: self._rank(query, n, where, ()))[0]]

    def scored(
        self, query: str, n: int, where: dict[str, Any] | None, extra: Sequence[Counter[str]]
    ) -> tuple[list[tuple[str, float]], list[float]]:
        """`rank` with the score of each note, and the scores of texts that are not in the index (`extra`, the counts of their words),
        each as one more note would have with the index's counts (number of notes, mean length, notes per word): the notes' scores are
        those of `rank`, whatever `extra` holds."""
        return self._guarded(lambda: self._rank(query, n, where, extra))

    def _rank(
        self, query: str, n: int, where: dict[str, Any] | None, extra: Sequence[Counter[str]]
    ) -> tuple[list[tuple[str, float]], list[float]]:
        words = dict.fromkeys(lexical.words(query))  # in the query's order, as `Bm25.scores`: the same sum in every process
        allowed: set[int] | None
        if not where:
            count, size, allowed = len(self._len), self._total, None
        else:
            allowed = set(self.matching(where))
            count, size = len(allowed), sum(self._len[i] for i in allowed)
        avg = (size / count if count else 1.0) or 1.0
        if allowed is None:
            if self._norm_avg != avg:  # the length part of the formula, kept while no note changes
                self._norm = {i: _K1 * (1 - _B + _B * length / avg) for i, length in self._len.items()}
                self._norm_avg = avg
            norm = self._norm
        else:
            norm = {i: _K1 * (1 - _B + _B * self._len[i] / avg) for i in allowed}
        total: dict[int, float] = {}
        get = total.get
        postings = self._postings(words)
        others = [0.0] * len(extra)
        sizes = [_K1 * (1 - _B + _B * sum(c.values()) / avg) for c in extra]
        for w in words:
            docs, counts = postings[w]
            pairs: Iterable[tuple[int, int]] = zip(docs, counts, strict=True)
            found_in = len(docs)
            if allowed is not None:
                pairs = [(d, f) for d, f in pairs if d in allowed]
                found_in = len(pairs)
            idf = math.log(1 + (count - found_in + 0.5) / (found_in + 0.5))
            for j, c in enumerate(extra):
                if f := c.get(w, 0):
                    others[j] += idf * f * (_K1 + 1) / (f + sizes[j])
            for key, f in pairs:
                total[key] = get(key, 0.0) + idf * f * (_K1 + 1) / (f + norm[key])
        if self._hidden:  # gone from the listing while a writer holds the file: not served
            total = {k: s for k, s in total.items() if k not in self._hidden}
        if len(total) > n:
            floor = heapq.nlargest(n, total.values())[-1]  # nothing under the n-th score can be among them
            total = {k: s for k, s in total.items() if s >= floor}
        rels = self._rels
        found = sorted(total, key=lambda i: (-total[i], *_in_tree(rels[i])))[:n]  # at the same score, in the order of the tree
        return [(self._rels[i], total[i]) for i in found], others

    def meta(self, rels: Sequence[str]) -> dict[str, tuple[str, str]]:
        """(title, summary) of each of `rels`, from the file."""

        def run() -> dict[str, tuple[str, str]]:
            out: dict[str, tuple[str, str]] = {}
            db = self._open()
            for chunk in _chunks(list(rels)):
                marks = ",".join("?" * len(chunk))
                for rel, title, summary in db.execute(
                    f"SELECT d.rel, b.title, b.summary FROM docs d JOIN docbody b ON b.id = d.id WHERE d.rel IN ({marks})", chunk
                ):
                    out[rel] = (title, summary)
            return out

        return self._guarded(run)

    def hashes(self, rels: Sequence[str] | None = None) -> dict[str, str]:
        """The hash of the text of each of `rels` (every note by default), as it was when the note was indexed."""

        def run() -> dict[str, str]:
            out: dict[str, str] = {}
            db = self._open()
            if rels is None:
                out.update(db.execute("SELECT rel, hash FROM docs WHERE bad = 0"))
            else:
                for chunk in _chunks(list(rels)):
                    marks = ",".join("?" * len(chunk))
                    out.update(db.execute(f"SELECT rel, hash FROM docs WHERE bad = 0 AND rel IN ({marks})", chunk))
            return out

        return self._guarded(run)

    def linking(self, store: WikiStore, targets: Collection[str], wait: float = WRITTEN_WAIT) -> Linked | None:
        """The notes that may link to one of `targets` (bundle paths; a raw copy as a note cites it, "/raw/…md"), for a writer that
        holds the bundle's lock and looks at each (`WikiStore.delete_note`): those the file holds as linking to one, with their file as
        the file holds it, and every note whose file is not (new, changed, or written within `RACY_NS`), whatever it links to. The wiki is
        listed whole (`scan_notes`: each file's time and size, nothing read), whatever `LISTING_LOOK` says, so a note edited by hand a
        moment ago is among them, as a `load` would see it; nothing is written to the file. `Linked.more` asks for other targets on the
        same listing. None when the file cannot say: not complete (`ready`), not on disk, damaged or busy, or held by another thread of
        this process for longer than `wait`; the caller then reads the whole wiki."""
        if self._mem is not None or self.path is None or not self.path.exists():
            return None
        with self.held(wait) as got:
            if not got:
                return None
            try:
                return self._guarded(lambda: self._linking(store, targets, wait))
            except sqlite3.Error:
                return None

    def _linking(self, store: WikiStore, targets: Collection[str], wait: float) -> Linked | None:
        if self._mem is not None:
            return None
        db = self._open()
        if not self._is_complete(db):
            return None
        db.execute("BEGIN")  # one state of the file: the rows of `links` and the stamps they are compared with
        try:
            if self._gen is None or self._partial or (self._disk_gen(db) != self._gen and not self._caught_up(db)):
                self._load(db)
            order, todo = self._scan(store)
            hits = self._linked(db, targets)
        finally:
            if db.in_transaction:
                db.execute("COMMIT")
        return Linked(self, order, {rel for rel, _, _ in todo}, hits, wait)

    def _linked(self, db: sqlite3.Connection, targets: Collection[str]) -> set[str]:
        hits: set[str] = set()
        for chunk in _chunks(sorted(set(targets))):
            marks = ",".join("?" * len(chunk))
            query = f"SELECT d.rel FROM links l JOIN docs d ON d.id = l.doc WHERE l.target IN ({marks})"
            hits.update(rel for (rel,) in db.execute(query, chunk))
        return hits


class Linked:
    """What `SearchIndex.linking` found: `listed`, every note of the wiki as listed (in the order of the tree); `notes`, those that may link
    to one of the targets, in the same order."""

    def __init__(self, index: SearchIndex, listed: list[str], unread: set[str], hits: set[str], wait: float) -> None:
        self.listed = listed
        self._live = set(listed)
        self._unread = unread  # the notes whose file is not as the index holds it: each may link to anything
        self._index = index
        self._wait = wait
        self.notes = self._of(hits)

    def _of(self, hits: set[str]) -> list[str]:
        return sorted((hits & self._live) | self._unread, key=tree_key)  # a note gone since its row was written links to nothing

    def more(self, targets: Collection[str]) -> list[str] | None:
        """The notes that may link to one of these other targets, on the same listing (the bundle's lock is held since: only a hand
        edit made meanwhile is not seen, as for a `load`); None when the file cannot say any more."""
        index = self._index
        with index.held(self._wait) as got:
            if not got:
                return None
            try:
                hits = index._guarded(lambda: None if index._mem is not None else index._linked(index._open(), targets))
            except sqlite3.Error:
                return None
        return None if hits is None else self._of(hits)  # an index that went to memory meanwhile holds no links of the wiki


# ---- while the file is being built


class _Unusable(Exception):
    """The file of a build in the background cannot be used (read-only, no space, held): the build stops."""


class _Stopped(Exception):
    """The wiki was closed: the build stops, and what its lots committed stays."""


class _Building(SearchIndex):
    """The index a `Build` writes, with a connection, a lock and a state of its own: it tells how many notes it listed."""

    def __init__(self, path: Path, tell: Callable[[], None]) -> None:
        super().__init__(path)
        self.listed = 0
        self._tell = tell

    def _scan(self, store: WikiStore) -> tuple[list[str], list[tuple[str, str, Stamp]]]:
        order, todo = super()._scan(store)
        self.listed = len(order)
        self._tell()
        return order, todo

    def _to_memory(self) -> None:  # an index in memory would be built for nobody: the build stops, the searches go on as before
        raise _Unusable(f"search index {self.path} cannot be used")


def lease_of(path: Path) -> Path:
    """The lease of the build of the index file at `path`: who holds it builds; from byte 64, how far it got (`_PROGRESS`)."""
    return Path(f"{path}.build")


class Build:
    """The build of an index file in a thread of its own, on an index of its own (`_Building`: the searches and the writers of the process
    do not wait for it), by one process of the machine at a time, the one that holds the lease (`lease_of`). It is the build of
    `SearchIndex.sync`, in lots, each durable once committed: the searches made meanwhile read the notes those lots hold (`sync` with
    `build=False`). Once the file is marked built the wiki is listed once more, waiting for the writers' locks, so that a note written
    before its writer saw the mark (a writer leaves its notes to the build until then, `update`) is in the file; only then is it marked
    complete, and the searches read it whole (`ready`). A process that dies leaves the lots it committed and lets
    go of the lease: the next search of any process goes on from there."""

    def __init__(self, path: Path, store: WikiStore, read: Reader) -> None:
        self.path = path
        self.lease = FileLock(lease_of(path))
        self.failed = False  # the file could not be built in the background: the searches of the process build it as before
        self._store = store
        self._read = read
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._index: _Building | None = None
        self._done = 0  # notes in the file when the build began
        self._reads = 0

    def start(self) -> bool:
        """Start the build if no process builds this file now (the lease is never waited for); whether it started."""
        if not self.lease.try_acquire():
            return False
        self._thread = threading.Thread(target=self._run, name="llmw2-index-build", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self.lease.release()
            raise
        return True

    @property
    def finished(self) -> bool:
        return self._thread is not None and not self._thread.is_alive()

    def wait(self, timeout: float) -> bool:
        """Wait at most `timeout` seconds for the build to end; whether it ended."""
        if self._thread is not None:
            self._thread.join(timeout)
        return self.finished

    def stop(self, wait: float) -> None:
        """Stop the build at its next read (its lot is rolled back, the lots before stay) and wait at most `wait` seconds for it."""
        self._stop.set()
        self.wait(wait)

    def _run(self) -> None:
        index = self._index = _Building(self.path, self._tell)

        def read(rel: str, path: str, stamp: Stamp) -> tuple[str, str, str, str, str] | None:
            if self._stop.is_set():
                raise _Stopped
            self._reads += 1
            if self._reads % _PROGRESS_EVERY == 0:
                self._tell()
            return self._read(rel, path, stamp)

        try:
            self._done = index.notes()
            index.sync(self._store, read)
            # once more now that the file is marked built, the writers' locks waited for (this is no reader): the notes its writers
            # left to the build until they saw the mark; then the file is complete, and the searches read it whole
            index.sync(self._store, read)
            index.run(lambda: index._mark_built(index._open(), "complete"))
        except _Stopped:
            pass
        except Exception as e:
            self.failed = True
            log.warning("search index %s was not built in the background (%s): the searches of this process build it", self.path, e)
        finally:
            index.close()
            self._index = None
            self.lease.release()

    def _tell(self) -> None:
        """Write how far the build got in its lease, for `status` and the searches (`progress`)."""
        index = self._index
        listed = index.listed if index is not None else 0
        with contextlib.suppress(OSError), open(self.lease.path, "r+b") as f:
            f.seek(_PROGRESS_AT)
            f.write(_PROGRESS.pack(min(self._done + self._reads, listed), listed))


def progress(path: Path) -> tuple[int, int]:
    """How far the last build of the index file at `path` got, as its lease says: (notes in the file, notes it listed); (0, 0) when it
    does not say (no build yet, or before its first listing). 16 bytes read, nothing waited for."""
    with contextlib.suppress(OSError, struct.error), open(lease_of(path), "rb") as f:
        f.seek(_PROGRESS_AT)
        done, listed = _PROGRESS.unpack(f.read(_PROGRESS.size))
        return int(done), int(listed)
    return 0, 0


def build_state(path: Path) -> str | None:
    """What `status` says of the index file at `path` while it is not built whole, in a sentence; None once it is, or before it is made
    (the first search makes it). One row and 16 bytes read, nothing waited for."""
    there = path.exists()
    if there:
        try:
            with contextlib.closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0)) as db:
                if db.execute("SELECT 1 FROM meta WHERE k = 'complete'").fetchone() is not None:
                    return None
        except sqlite3.Error:
            return None
    lease = FileLock(lease_of(path))
    if not lease.path.exists() or lease.try_acquire():  # no build now (and no lease file made for asking)
        if lease.held:
            lease.release()
        return "the search index is not complete: the next search goes on building it, and its results may be partial" if there else None
    done, listed = progress(path)
    count = f" ({done} of {listed} notes)" if listed else ""
    return f"the search index is being built in the background{count}: until it is done the results of a search may be partial"
