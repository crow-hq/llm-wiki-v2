# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The wiki on disk, as an OKF v0.2 bundle.

    wiki/
      index.md        root index (frontmatter: okf_version "0.2")
      log.md          newest-first change log
      raw/            copies of every ingested source (type: Source): never edited, except that
                      `mark_origin_removed` adds a `removed` timestamp to the frontmatter
      <note>.md       type: Note, title, description (the one-line summary), tags, generated, sources
      <folder>/
        index.md      "* [sub](sub/index.md) - description" and "* [Title](note.md) - summary"

A folder's one-line description lives in its parent's index.md entry, so the
tree is fully described by standard OKF index files. Links are relative.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import bisect
import contextlib
import contextvars
import hashlib
import logging
import operator
import os
import pickle
import re
import stat
import sys
import threading
import time
import uuid
from array import array
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from llmw2.bundle import logfile, txn
from llmw2.bundle.document import DELIMITER, OKFDocument, OKFDocumentError, read_bytes
from llmw2.bundle.filelock import FileLock, WaitBudget
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.origin_index import OriginIndex
from llmw2.bundle.tree import (
    CLOSED,
    INDEX,
    LOG,
    NOTE_TYPE,
    OKF_VERSION,
    RAW,
    SOURCE_TYPE,
    Folder,
    Note,
    Stamp,
    dead_links_to_text,
    drop_links,
    drop_see_also,
    join_see_also,
    note_dir,
    parse_index,
    refresh_entries,
    rel_link,
    rewrite_links,
    same_stamp,
    see_also_targets,
    slugify,
    split_see_also,
    stamp_of,
    subfolder_of,
)
from llmw2.errors import InputError, NoteChanged, WikiError

log = logging.getLogger(__name__)

ARCHIVE_DIR = ".archive"  # superseded versions of notes; hidden from the tree
SYNC_DIR = ".llmw2/sync"  # the cursors of `sync`, one file per connector and account
CACHE_FILE = ".llmw2/cache/classifier.sqlite"  # the classifier's answers, by the hash of the request (see llmw2.models.cache)
LOCK_FILE = ".llmw2/lock"  # the lock across processes where there is no flock (Windows)
PROPOSALS_FILE = ".llmw2/proposals.json"  # the queue of areas users propose (see llmw2.bundle.proposals)
REPLACE_ATTEMPTS = 6  # about 0.3 s in all: long enough for a reader to close the file it was reading
AUTHOR_MAX = 60  # characters of an author's name in a log line
LINK_PREFILTER = 50  # notes deleted at once up to which only the notes naming their files are checked for links to them
PURGED = "[purged]"  # what log.md says in place of a line's text when the line named something purged
# the memory a process takes to read the whole wiki, per note: measured, +33 MB at 5,000 notes and +346 MB at 50,000 (about 7 KiB each)
READ_BYTES_PER_NOTE = 7 << 10
READ_SHARE = 2  # the reads outside the lock take at most 1/READ_SHARE of the free memory: the rest stays to the rest of the machine
READ_PLACE = ".llmw2/read-{}.lock"  # a place to read the whole wiki outside its lock (`WikiStore.reading`)


# the stores (by key) whose sections open with no undo log in this context (`WikiStore.undo_suspended`)
_undo_off: contextvars.ContextVar[frozenset[object]] = contextvars.ContextVar("llmw2_undo_off", default=frozenset())
# the stores (by key) whose sections do not tell `on_written` in this context (`WikiStore.written_later`)
_told_later: contextvars.ContextVar[frozenset[object]] = contextvars.ContextVar("llmw2_told_later", default=frozenset())
_author: contextvars.ContextVar[str | None] = contextvars.ContextVar("llmw2_author", default=None)
# the store (by key) whose sections wait for the lock only what a budget has left, in this context (`WikiStore.waiting`)
_bounded: contextvars.ContextVar[tuple[object, WaitBudget] | None] = contextvars.ContextVar("llmw2_bounded", default=None)


class LockBusy(WikiError):
    """Another writer held the wiki for all the wait that the call allows itself (`WikiStore.waiting`): nothing of the section was done."""


def free_memory() -> int | None:
    """The physical memory free now, in bytes; None where the system does not say."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ("total", "free", "page", "page_free", "virtual", "virtual_free", "extended")
                ]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined,unused-ignore]
                return None
            return int(status.free)
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def read_places(notes: int) -> int:
    """How many processes of the machine may read the whole wiki outside its lock at once: as many reads of `notes` notes
    (`READ_BYTES_PER_NOTE`) as half the memory free now holds (`READ_SHARE`), at least one; one when the notes or the memory are not
    known. Together they never take more than that half (besides the one reading under the lock, as a section always did)."""
    free = free_memory()
    if notes <= 0 or free is None:
        return 1
    return max(1, free // READ_SHARE // (notes * READ_BYTES_PER_NOTE))


def clean_author(author: str | None) -> str | None:
    """An author's name as a log line carries it: one line, no `*[]()`, at most `AUTHOR_MAX` characters; None if nothing is left."""
    name = " ".join(str(author or "").translate({ord(c): " " for c in "*[]()"}).split())[:AUTHOR_MAX].strip()
    return name or None


def current_author() -> str | None:
    """The author in force for this call, or None."""
    return _author.get()


@contextlib.contextmanager
def authored(author: str | None) -> Iterator[None]:
    """For the calls made inside, in this thread and in the copies of its context, the log lines end with ` · by <author>`.

    The author is declared, not verified. None (or a name with nothing left once cleaned) leaves the author already in force.
    """
    name = clean_author(author)
    if name is None:
        yield
        return
    token = _author.set(name)
    try:
        yield
    finally:
        _author.reset(token)


def body_hash(body: str) -> str:
    """The hash of a note's main body: it tells whether code alone wrote it.

    Line breaks count as "\n" whatever they were ("\r\n" is read back as "\n"), so a reload does not change the hash.
    """
    return "sha256:" + hashlib.sha256("\n".join(body.strip().splitlines()).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    """The clock, for the heading of a log day and the time of a note."""
    return datetime.now(UTC)


def now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def _entry(title: str, link: str, text: str) -> str:
    """One OKF index / See-also line: `* [Title](link) - text`."""
    title = " ".join(title.split()).replace("[", "(").replace("]", ")")
    text = " ".join(text.split())
    return f"* [{title}]({link})" + (f" - {text}" if text else "")


RACY_NS = 2_000_000_000  # a file this recent is read again: two writes in one clock tick can leave the same time and size

Stamps = tuple["array[int]", "array[int]"]  # the stamps of a folder's notes (`_kept_stamps`)
STALE = -2  # the time of a kept folder that no folder has: the next load lists it again
_U64 = (1 << 64) - 1
_STAMP = operator.attrgetter("st_mtime_ns", "st_size", "st_ino")
_TIME, _SIZE, _INO = (operator.attrgetter(a) for a in ("st_mtime_ns", "st_size", "st_ino"))


def _kept_stamps(stats: list[os.stat_result]) -> Stamps:
    """The stamps of a folder's notes as `scan_notes` keeps them, with no object per note: the times then the sizes in one array,
    the inodes in another (unsigned; an inode wider than 64 bits, ReFS, by its low 64)."""
    kept = array("q", map(_TIME, stats))
    kept.extend(map(_SIZE, stats))
    try:
        return kept, array("Q", map(_INO, stats))
    except OverflowError:
        return kept, array("Q", [n & _U64 for n in map(_INO, stats)])


def tree_key(rel: str) -> tuple[tuple[int, str], ...]:
    """Where a note's bundle path comes in the order of the tree, the order `scan_notes` lists the notes in: in each folder its notes by
    name, then its folders by name, names as the system compares them (`os.path.normcase`, which also turns `/` into the system's
    separator)."""
    *folders, name = os.path.normcase(rel).split(os.sep)
    return (*((1, folder) for folder in folders), (0, name))


class _Parsed(NamedTuple):  # a tuple, not a dataclass: one is made for every note of the tree at the first load
    """A note file as `load` last read it (inside a session too, and between sessions), valid while its modification time, size, inode
    and change time (where the system has one, see `changed`) are the same."""

    stamp: tuple[int, int, int]
    changed: int  # st_ctime_ns: a time restored by hand (`cp -p`, `rsync -t`) leaves the stamp, not this one (where there is one)
    frontmatter: bytes  # pickled: each load hands out its own copy, which callers edit in place
    body: str


@dataclass
class Deleted:
    """What a delete removed, and the notes it touched."""

    notes: list[str] = field(default_factory=list)  # bundle paths of the notes deleted
    folders: list[str] = field(default_factory=list)  # labels of the folders deleted, subfolders included
    sources: list[str] = field(default_factory=list)  # raw copies that no remaining note cites ("/raw/…")
    archived: list[str] = field(default_factory=list)  # archived versions of the notes deleted ("/.archive/…")
    unlinked: list[str] = field(default_factory=list)  # remaining notes that lost a link to what went
    # what is not done yet: "journal", the text of entries `remember` kept is still in the journal's segments (the filer of the process
    # that holds its lease seals and reuses them at its next look)
    pending: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(root: Path) -> threading.Lock:
    """The lock of a bundle folder within this process, the same for every path that names it."""
    key = os.path.realpath(root)
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class WikiStore:
    """Reads the folder tree and writes notes, folders, links, index.md and log.md."""

    def __init__(self, root: Path, *, actor: str) -> None:
        self.root = Path(root)
        self.actor = actor
        self._origins = OriginIndex(self.root)
        self._parsed: dict[str, _Parsed] = {}  # note path -> what its file held, as of the last load
        # a batch (`session`): the folders as the last load built them, by path, with the modification time of the folder
        # then, and the notes read while still recent (read again at the next load)
        self._depth = 0
        self._dirs: dict[str, tuple[int, bool, Folder]] = {}  # folder path -> (its time, whether it was old enough, the folder)
        self._recent: set[str] = set()
        self._relisted = False  # whether the last session `load` listed a folder again
        self._sessions = 0  # the sessions this store has had: the first fills no cache (a process of one session, the CLI, never reads it)
        self._keeps = False  # the session going on was opened by a caller that will open more (`session(keep=True)`): it fills the cache
        # told the paths a section wrote, once it committed and before its `after_commit` (None: a section with no undo log, which does not
        # know them, after the lock is let go). The search index of the `Wiki` takes them, so that a reader never has to write them.
        # It must not raise: what it fails to do is done by the next look of the index at the files.
        self.on_written: Callable[[list[str] | None], None] | None = None
        # what `scan_notes` keeps of a folder while its names stay the same: (its names as listed, the names of its notes, its
        # subfolders, the bundle paths of its notes, the stamps of its notes when none was to be read again (`_kept_stamps`), the caller
        # that had them). Nothing per note but its two strings and three numbers: every process that searches holds it for the wiki whole.
        self._listed: dict[str, tuple[tuple[str, ...], list[str], list[tuple[str, str]], list[str], Stamps | None, object]] = {}
        self._held: dict[str, Note] = {}  # note path -> the note a kept folder holds
        self._loose: dict[str, list[Note | Folder]] = {}  # folder path -> what a write added at the end of its kept lists
        self._session_guard = threading.RLock()
        self._undo_key = object()  # what `undo_suspended` puts in `_undo_off` for this store
        self._log = logfile.LogTail()  # what this store knows of log.md since it last read or wrote it
        self.log_rewrites = 0  # purges of log lines that had to write the file whole (a line too short to be overwritten in place)
        self._log_checked = False  # whether log.md is known to be in the order of arrival
        # whether two notes may stay linked: set by a wiki that limits its links (`link_scope`), so that a note which changes
        # area loses the See-also entries that no longer fit; None: every link stays
        self.link_reach: Callable[[Note, Note], bool] | None = None

    # -- reading --------------------------------------------------------------

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / RAW).mkdir(exist_ok=True)
        if not (self.root / INDEX).exists():
            self.write_index(Folder(self.root, ""))
        if not (self.root / LOG).exists():
            write_atomic(self.root / LOG, logfile.TITLE + "\n")
        with contextlib.suppress(OSError):  # a wiki that is only read (a read-only folder) needs none of these
            txn.ensure_journal_dir(self.root)
            self._recover(blocking=False)
            self._migrate_log()

    def _migrate_log(self) -> None:
        """A log of 1.0.5 or earlier (newest first) is turned around, once, under the lock (see `llmw2.bundle.logfile`)."""
        path = self.root / LOG
        if self._log_checked or txn.current() is not None:
            return
        if not self._log_is_current(path):
            with self.lock():
                self._turn_log(path)
        self._log_checked = True

    @staticmethod
    def _log_is_current(path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                return logfile.is_current(f.read(logfile.HEAD_BYTES))
        except OSError:
            return True  # no log: `append_log` makes one

    def _turn_log(self, path: Path) -> None:
        """The lock is held: put the log in the order of arrival, if it is not (it may have been done meanwhile)."""
        if self._log_is_current(path):
            return
        write_atomic(path, logfile.newest_first_to_oldest_first(read_utf8_or_latin1(path)))
        self._log = logfile.LogTail()

    def recover(self) -> txn.Restored:
        """Undo a filing that a process died in the middle of, if one did (the undo log is not empty and nobody holds the lock)."""
        return self._recover(blocking=True)

    def _recover(self, *, blocking: bool) -> txn.Restored:
        """`blocking` false: only if the bundle is free right now (a process that holds it is in the middle of a section of its
        own, whose log is not an orphan)."""
        done = txn.Restored()
        if not txn.pending(self.root):
            if txn.trash_left(self.root):  # a section that died before its first record: nobody owes its images (a section running now, in
                with self._exclusive(blocking=False) as got:  # another process, holds the lock: it is left alone)
                    if got:
                        self._restore()
            return done
        with self._exclusive(blocking=blocking) as got:
            if got:
                done = self._restore()
        return done

    def _restore(self) -> txn.Restored:
        """The lock is held: undo what an interrupted section left, and forget the folders this store had listed (a file put
        back has a new time, so the notes parsed before are read again)."""
        if not txn.pending(self.root):
            txn.sweep_trash(self.root)
            return txn.Restored()
        done = txn.recover(self.root)
        if done.rolled_back:
            log.warning("restored the wiki to before an interrupted filing (%d paths, %d left alone)", done.paths, len(done.conflicts))
        self.forget()
        return done

    @contextlib.contextmanager
    def lock(self, *, undo: bool = True, id: str | None = None, after_commit: Callable[[], None] | None = None) -> Iterator[None]:
        """Hold the bundle for writing: one writer at a time across threads and processes (the CLI and a server).

        A lock per bundle folder within the process, shared by every store on it (the server rebuilds its wiki when
        the settings change), then an advisory flock on the folder itself, so no lock file lands in the wiki. Where
        there is no flock (Windows) it is a byte lock on `.llmw2/lock`, retried until free; the file is hidden like the
        rest of `.llmw2/`. The lock is not reentrant: do not nest it.

        The section is a transaction (`llmw2.bundle.txn`): every file it changes is saved first in an undo log, so a section that
        raises, or a process that dies in it, leaves the bundle as it was. A section that moves or deletes whole folders
        opens with `undo=False` and writes as it always did. A log left by a dead process is undone as the lock is taken.
        `id` is stored in the commit record; `after_commit` runs once the section is durable and before the log is emptied.
        """
        unlogged = False
        with self._exclusive(blocking=True):
            self._restore()
            if not undo or self._undo_key in _undo_off.get():
                yield
                unlogged = True
            else:
                yield from self._section(id, after_commit)
        if unlogged:  # the paths are not known: the index looks at the files, with the lock let go
            self.tell_written(None)

    def _section(self, id: str | None, after_commit: Callable[[], None] | None) -> Iterator[None]:
        """The transaction of a `lock` (held)."""
        section = txn.Txn(self.root, id=id)
        try:
            with txn.active(section):
                yield
            section.commit()
        except BaseException:
            try:
                section.rollback()
            finally:
                self.forget()  # what a session kept of the tree is not what is on disk now
            raise
        self.tell_written(section.touched())  # before `after_commit`: what the journal is told is filed is in the index already
        if after_commit is not None:
            try:
                after_commit()
                section.told = True
            except BaseException:
                section.abandon()  # done and durable: not rolled back; the log keeps its commit, and the next recovery says it
                self.forget()
                raise
        section.finish()

    def tell_written(self, written: list[str] | None) -> None:
        """Tell `on_written` (if set, and not held back by `written_later`) what was written; its errors are logged, never raised."""
        if self.on_written is None or self._undo_key in _told_later.get():
            return
        try:
            self.on_written(written)
        except Exception as e:  # the section is durable all the same; the index takes the files at its next look
            log.warning("the search index did not take what a write changed: %s", e)

    @contextlib.contextmanager
    def written_later(self) -> Iterator[None]:
        """The sections this thread opens on this store (and the threads that run in a copy of its context) do not tell `on_written`:
        a batch tells it once, at its end (see `Wiki.ingest_many`)."""
        token = _told_later.set(_told_later.get() | {self._undo_key})
        try:
            yield
        finally:
            _told_later.reset(token)

    def is_note_path(self, rel: str) -> bool:
        """Whether a bundle path (posix) is where `scan_notes` would find a note."""
        parts = rel.split("/")
        if not parts[-1].endswith(".md") or parts[-1] in (INDEX, LOG) or parts[0] == RAW:
            return False
        return not any(p == CLOSED or p.startswith(".") for p in parts)

    @contextlib.contextmanager
    def reading(self, notes: int) -> Iterator[bool]:
        """One of the places of the machine to read the whole wiki outside its lock (`read_places`: as many as the free memory holds), for
        as long as the block lasts; yields whether it got one. Without one the caller reads under the lock, one process at a time, as
        a section always did: what reads at once is bounded by the memory, never more than the places plus the one holding the lock. A
        place is a `FileLock`: tried each, then, in a call that bounds its waits (`waiting`), waited for in the kernel's queue within what
        is left; the kernel lets it go when the process dies."""
        n = read_places(notes)
        # tried in order up to the first free one: as many tries as readers there are, files made only as far as they go
        got = next((place for i in range(n) if (place := FileLock(self.root / READ_PLACE.format(i))).try_acquire()), None)
        bounded = _bounded.get()
        if got is None and bounded is not None and bounded[0] is self._undo_key:
            place = FileLock(self.root / READ_PLACE.format(os.getpid() % n))
            with bounded[1].wait() as left:
                if place.acquire(left):
                    got = place
        try:
            yield got is not None
        finally:
            if got is not None:
                got.release()

    @contextlib.contextmanager
    def waiting(self, budget: WaitBudget) -> Iterator[None]:
        """The sections this thread opens on this store (and the threads that run in a copy of its context: the workers of an
        `ingest_many`) wait for the lock at most what `budget` has left, and raise LockBusy, having done nothing, when it does not come:
        a call that bounds all its waits (`Wiki.purge`)."""
        token = _bounded.set((self._undo_key, budget))
        try:
            yield
        finally:
            _bounded.reset(token)

    @contextlib.contextmanager
    def undo_suspended(self) -> Iterator[None]:
        """The sections this thread opens on this store (and the threads that run in a copy of its context: the workers of an
        `ingest_many`) have no undo log while this is held; those of other threads keep theirs. For a rebuild, which starts from
        nothing and is done again from the start if it is interrupted (see `txn.mark_rebuild`): an undo log for each of its
        documents would be written, flushed and thrown away. Not for anything that must leave the wiki as it was."""
        token = _undo_off.set(_undo_off.get() | {self._undo_key})
        try:
            yield
        finally:
            _undo_off.reset(token)

    def lock_is_free(self) -> bool:
        """Whether nobody holds the bundle's lock right now (one try that does not wait)."""
        with self._exclusive(blocking=False) as got:
            return bool(got)

    @contextlib.contextmanager
    def _exclusive(self, *, blocking: bool) -> Iterator[bool]:
        """The lock of `lock`, without the transaction; with `blocking` false it is taken only if free (yields whether it was).

        A `FileLock` (`llmw2.bundle.filelock`): the kernel waits for it, on every system, and frees it when the process dies. On
        POSIX it is an advisory `flock` on the bundle's folder itself; on Windows a lock on the first byte of `.llmw2/lock`,
        the same byte an older version locks, so versions of llmw2 that meet on a bundle still exclude each other."""
        if sys.platform == "win32":
            path = self.root / LOCK_FILE
            lock = FileLock(path)
        else:
            lock = FileLock(self.root, directory=True)
        bounded = _bounded.get() if blocking else None
        if bounded is None or bounded[0] is not self._undo_key:
            with lock.attempt(None if blocking else 0) as got:
                yield got
            return
        with bounded[1].wait() as left:
            got = lock.acquire(left)
        if not got:
            raise LockBusy(f"another writer held the wiki at {self.root} for longer than this call waits")
        try:
            yield True
        finally:
            lock.release()

    @contextlib.contextmanager
    def session(self, *, keep: bool = False) -> Iterator[None]:
        """A batch of writes by this store (see `Wiki.ingest_many`): `load` and the origin index only look again at what
        changed. The first `load` of a session lists the whole tree and parses only the notes whose file is not the one it
        parsed before (`_parsed`, by modification time, size, inode and change time, kept between sessions and outside them, for
        notes older than RACY_NS, filled from the second session of the store on: the first, all a CLI call has, parses and keeps
        nothing; `keep`, for a caller that will open more sessions (the filer), fills it from the first, and keeps the origin index
        from one session to the next, checked file by file at the first look of each); after it, a folder whose modification time
        is the same (and not recent) keeps the notes it had, and so does `raw/`: every write of the store replaces a file, which
        changes its folder's time. A note edited in place by another program, which leaves the folder's time as it was, is seen by
        the next batch, and never overwritten meanwhile (see `_save`). The folders are kept only while the session lasts. Limit, as
        in `scan_notes`: a note edited in place with the same size and the same modification time put back by hand is not seen (on
        Windows; elsewhere the change time shows it) until its size or time changes."""
        with self._session_guard:
            self._depth += 1
            if self._depth == 1:
                self._sessions += 1
                self._keeps = keep
                self._origins.distrust()  # what an earlier session trusted by the times of the folders is looked at again
            self._origins.session = True
        try:
            yield
        finally:
            with self._session_guard:
                self._depth -= 1
                if not self._depth:
                    self._origins.session = False
                    self.forget(origins=not self._keeps)

    def forget(self, *, origins: bool = True) -> None:
        """Drop what a session kept of the folders, so the next `load` lists the wiki whole (after a failure that left it unknown).
        The notes already parsed (`_parsed`) are checked against their files at that listing, not dropped. `origins` false (the end
        of a `keep` session): the origin index keeps what it parsed, which the next session checks file by file."""
        with self._session_guard:
            self._dirs.clear()
            self._recent.clear()
            self._held.clear()
            self._loose.clear()
            if origins:
                self._origins.forget()
        self._log = logfile.LogTail()

    def load(self) -> Folder:
        if not self.root.is_dir():
            raise FileNotFoundError(f"wiki not found: {self.root} (run `llmwiki2 init`)")
        if self._depth:
            with self._session_guard:
                self._relisted = False
                root = self._load_folder(self.root, "", "", None, self._parsed)  # `_parsed` outlives the session
                if self._relisted and self._parsed:  # a folder listed again: what no note of the tree is any more goes
                    live = {str(n.path) for n in root.all_notes()}
                    self._parsed = {w: p for w, p in list(self._parsed.items()) if w in live}
                return root
        seen: dict[str, _Parsed] = {}  # what this load met, so the cache never outgrows the wiki
        folder = self._load_folder(self.root, "", "", None, seen)
        self._parsed = seen
        return folder

    @property
    def _fills(self) -> bool:
        return not self._depth or self._sessions > 1 or self._keeps

    def _note_of(
        self, where: str, rel: str, folder: Folder | None, st: os.stat_result, stamp: Stamp, seen: dict[str, _Parsed]
    ) -> Note | None:
        """The note of a file: from `_parsed` (the stamp is the same) or read; `seen` (what this load keeps, `_parsed` itself in a
        session) holds it once the file is older than RACY_NS, else not (a file written this very moment may change again unseen).
        None when the file cannot be read, or is gone since the listing (another process deleted or moved the note: the load
        goes on as if it had not been listed). The first session of a store does not keep what it parses (`_fills`): the copy of the front
        matter of every note costs about 3% of that session, and nothing in a process that has only one session will ever use it."""
        # `same_stamp`: on Windows a listing gives no inode and `note_at` (a stat of the file) does
        if (hit := self._parsed.get(where)) and same_stamp(hit.stamp, stamp) and hit.changed == st.st_ctime_ns:
            seen[where] = hit
            return Note(Path(where), rel, pickle.loads(hit.frontmatter), hit.body, folder, stamp)
        try:
            doc = OKFDocument.read(Path(where))
        except (FileNotFoundError, PermissionError):  # gone since the listing; or deleted while another process had it open, where the
            return None  # deletion is the old kind (FAT/exFAT, SMB, Windows before 1903): the name stays listed and refuses to open
        except OKFDocumentError as e:
            log.warning("skipping %s: %s", rel, e)
            return None
        if self._fills and time.time_ns() - st.st_mtime_ns > RACY_NS:
            seen[where] = _Parsed(stamp, st.st_ctime_ns, pickle.dumps(doc.frontmatter), doc.body)
        else:
            seen.pop(where, None)
        return Note(Path(where), rel, doc.frontmatter, doc.body, folder, stamp)

    def _load_folder(
        self, path: Path, rel: str, description: str, parent: Folder | None, seen: dict[str, _Parsed]
    ) -> Folder:
        key = str(path)
        prior: dict[str, Note] = {}
        scanned = mtime = 0
        if self._depth:  # a session: a folder as it was is kept, with its notes and, one by one, its subfolders
            mtime, scanned = os.stat(path).st_mtime_ns, time.time_ns()
            loose = self._loose.pop(key, [])
            if (kept := self._dirs.get(key)) is not None:
                if kept[0] == mtime and kept[1] and _in_place(kept[2], loose):
                    folder = kept[2]
                    folder.parent, folder.description = parent, description
                    subfolders = []
                    for sub in folder.subfolders:
                        with contextlib.suppress(FileNotFoundError):  # gone since: its parent's time would have changed
                            subfolders.append(self._load_folder(sub.path, sub.rel, sub.description, folder, seen))
                    folder.subfolders = subfolders
                    return folder
                prior = {str(n.path): n for n in kept[2].notes}
        folder = Folder(path, rel, description, parent=parent)
        self._relisted = True  # a folder listed again: the notes it no longer holds leave `_parsed`
        descriptions: dict[str, str] | None = None  # read from index.md when there is a subfolder to describe
        with os.scandir(path) as listing:  # the entries carry their stat, a call each saved on some systems
            entries = sorted(listing, key=lambda e: os.path.normcase(e.name))
        for entry in entries:
            name = entry.name
            if name == CLOSED:
                folder.routable = False
                continue
            if name.startswith(".") or (not rel and name == RAW):
                continue
            child_rel = f"{rel}/{name}" if rel else name
            try:
                st = entry.stat()
            except OSError:  # gone since the listing
                continue
            if stat.S_ISDIR(st.st_mode):
                if descriptions is None:
                    descriptions = _descriptions(path)
                with contextlib.suppress(FileNotFoundError):  # gone since the listing
                    folder.subfolders.append(self._load_folder(Path(entry.path), child_rel, descriptions.get(name, ""), folder, seen))
            elif name.endswith(".md") and name not in (INDEX, LOG):
                stamp = stamp_of(st)
                where = entry.path
                if self._depth:
                    known = prior.get(where)
                    if known is not None and known.stamp and same_stamp(known.stamp, stamp) and where not in self._recent:
                        known.folder = folder  # the same note as the last load, as the store left it
                        folder.notes.append(known)
                        self._held[where] = known
                        continue
                    if (fresh := self._note_of(where, child_rel, folder, st, stamp, seen)) is None:
                        continue
                    if time.time_ns() - st.st_mtime_ns > RACY_NS:
                        self._recent.discard(where)
                    else:
                        self._recent.add(where)
                    self._held[where] = fresh
                    folder.notes.append(fresh)
                    continue
                if (fresh := self._note_of(where, child_rel, folder, st, stamp, seen)) is not None:
                    folder.notes.append(fresh)
        if self._depth:
            # a folder changed within RACY_NS of this listing may change again in the same tick, leaving the time as it is:
            # it is listed again at the next load (its notes, which are not recent, are not read again)
            self._dirs[key] = (mtime, scanned - mtime > RACY_NS, folder)
        return folder

    def folder_at(self, rel: str) -> Folder | None:
        """The folder at `rel` ("" the root) as `load` gives it, alone: its notes (`_note_of`: what this process parsed of a file that did
        not change since is not read again), its subfolders by name with their descriptions and nothing in them, no parent. For writing
        its own index and its notes, not a tree to walk. None for a folder a load does not list."""
        parts = rel.strip("/").split("/") if rel.strip("/") else []
        if any(not p or p == CLOSED or p.startswith(".") or ":" in p or "\\" in p for p in parts) or parts[:1] == [RAW]:
            return None
        path = Path(os.path.join(self.root, *parts)) if parts else self.root
        try:
            with os.scandir(path) as listing:
                entries = sorted(listing, key=lambda e: os.path.normcase(e.name))
        except (FileNotFoundError, NotADirectoryError):
            return None
        folder = Folder(path, "/".join(parts))
        descriptions: dict[str, str] | None = None
        for entry in entries:  # as `_load_folder` lists a folder
            name = entry.name
            if name == CLOSED:
                folder.routable = False
                continue
            if name.startswith(".") or (not parts and name == RAW):
                continue
            child_rel = f"{folder.rel}/{name}" if parts else name
            try:
                st = entry.stat()
            except OSError:  # gone since the listing
                continue
            if stat.S_ISDIR(st.st_mode):
                if descriptions is None:
                    descriptions = _descriptions(path)
                folder.subfolders.append(Folder(Path(entry.path), child_rel, descriptions.get(name, ""), parent=folder))
            elif name.endswith(".md") and name not in (INDEX, LOG):
                if (note := self._note_of(entry.path, child_rel, folder, st, stamp_of(st), self._parsed)) is not None:
                    folder.notes.append(note)
        return folder

    def notes_at(self, rels: Iterable[str], folders: dict[str, Folder | None]) -> list[Note]:
        """The notes at `rels` as `load` gives them, each in its folder (`folder_at`, kept in `folders` by path: the caller deletes them
        and writes the folders' indexes); a path with no note is left out."""
        found = []
        for rel in rels:
            if not self.is_note_path(rel):
                continue
            where = note_dir(rel)
            if where not in folders:
                folders[where] = self.folder_at(where)
            folder = folders[where]
            if folder is not None and (note := next((n for n in folder.notes if n.rel == rel), None)) is not None:
                found.append(note)
        return found

    def near(self, rels: Iterable[str], gone: Collection[str], folders: dict[str, Folder | None]) -> list[Note]:
        """The notes at `rels` but those in `gone`, for `_delete(kept=)`: from a folder of `folders` that holds one, else read alone
        (`note_at`); a path with no note is left out."""
        kept = []
        for rel in rels:
            if rel in gone:
                continue
            where = note_dir(rel)
            if where in folders:
                folder = folders[where]
                note = next((n for n in folder.notes if n.rel == rel), None) if folder is not None else None
            else:
                note = self.note_at(rel)
            if note is not None:
                kept.append(note)
        return kept

    def note_files(self, rel: str) -> int:
        """How many files of the folder at `rel` (`""` the root) a `load` would take for its notes, read from its listing alone: at least
        as many as the notes it would find there (a file that is not a note counts too). 0 for a folder that is not there."""
        try:
            with os.scandir(self.root / rel) as listing:
                names = [e.name for e in listing if e.is_file()]
            return sum(1 for n in names if n.endswith(".md") and n not in (INDEX, LOG) and not n.startswith("."))
        except (FileNotFoundError, NotADirectoryError):
            return 0

    def scan_notes(
        self, known: Mapping[str, Stamp | None], racy_before: int, owner: object = None
    ) -> tuple[list[str], list[tuple[str, str, Stamp]]]:
        """Every note of the wiki as `load` would list it, with nothing read: (the bundle paths in the order of the tree, `tree_key`,
        the notes to read again). A note is to read again when it is not in `known` (bundle path -> the stamp it had when it was
        read), or has another time or size, or was written after `racy_before` (nanoseconds: a file that recent may change
        again with the same time, see `RACY_NS`); each comes as (bundle path, file path, stamp).

        Limit, the same as in v1.0.2 (`_parsed`): a file edited in place by another program that leaves both its size and its
        modification time as they were (`rsync -t`, `cp -p`, a restore) is not seen, until its size or time changes. The
        file's inode is no help on Windows, where the listing gives none. The same limit holds for `load` in a session, which keeps
        what it parsed between sessions (`_parsed`); on a system with a change time (not Windows) it is closed.

        A folder is listed in one call that carries every file's time and size. What a folder holds by name (its notes and its
        subfolders, in order) is kept while the names are the same, so a wiki that did not change costs one call per folder and
        a look at each note's time and size, and builds no object per note. `owner`: an object the caller keeps for as long as `known`
        holds what it held after its last call, and replaces when it does not; a folder whose notes are as that call left them is
        not looked at again for the same owner."""
        if not self.root.is_dir():
            raise FileNotFoundError(f"wiki not found: {self.root} (run `llmwiki2 init`)")
        order: list[str] = []
        todo: list[tuple[str, str, Stamp]] = []
        kept_dirs = self._listed

        def walk(path: str, rel: str) -> None:
            try:
                with os.scandir(path) as listing:
                    entries = {e.name: e for e in listing}
            except FileNotFoundError:  # a folder another process took away since its parent was listed: its notes are gone too
                if not rel:
                    raise
                kept_dirs.pop(path, None)
                return
            kept = kept_dirs.get(path)
            listed = tuple(entries)  # in the order of the listing: the same names in another order only build the folder again
            if kept is None or kept[0] != listed:
                notes: list[str] = []
                subs: list[tuple[str, str]] = []
                rels: list[str] = []
                for name in sorted(entries, key=os.path.normcase):
                    if name == CLOSED or name.startswith(".") or (not rel and name == RAW):
                        continue
                    try:
                        mode = entries[name].stat().st_mode
                    except OSError:  # gone since the listing
                        continue
                    child = f"{rel}/{name}" if rel else name
                    if stat.S_ISDIR(mode):
                        subs.append((entries[name].path, child))
                    elif name.endswith(".md") and name not in (INDEX, LOG):
                        notes.append(name)
                        rels.append(child)
                kept = kept_dirs[path] = (listed, notes, subs, rels, None, None)
            _, notes, subs, rels, clean, had = kept
            try:
                stats = [entries[name].stat() for name in notes]
            except OSError:  # a note gone since the listing: the folder is looked at again by name next time
                kept_dirs.pop(path, None)
                there = [i for i, name in enumerate(notes) if os.path.exists(entries[name].path)]
                notes, rels = [notes[i] for i in there], [rels[i] for i in there]
                stats = [entries[name].stat() for name in notes]
            stamps = _kept_stamps(stats)
            if clean is not None and owner is not None and had is owner and clean == stamps:  # the same as when the caller last had it all
                order.extend(rels)
            else:
                read = False
                for name, child, stamp in zip(notes, rels, map(_STAMP, stats), strict=True):
                    order.append(child)
                    held = known.get(child)
                    if held is None or stamp[0] >= racy_before or held[:2] != stamp[:2] or (held[2] != stamp[2] and held[2] and stamp[2]):
                        todo.append((child, entries[name].path, stamp))
                        read = True
                if path in kept_dirs:
                    old = kept_dirs[path]
                    kept_dirs[path] = (*old[:4], None if read or any(st.st_mtime_ns >= racy_before for st in stats) else stamps, owner)
            for sub_path, sub_rel in subs:
                walk(sub_path, sub_rel)

        walk(str(self.root), "")
        return order, todo

    def _holds(self, folder: Folder | None) -> bool:
        """Whether a session keeps this very folder as the last load built it."""
        kept = self._dirs.get(str(folder.path)) if folder is not None and self._depth else None
        return kept is not None and kept[2] is folder

    @contextlib.contextmanager
    def _writing(self, directory: Path, *, tracked: bool) -> Iterator[None]:
        """A write of the store in `directory`. In a session, if the folder is kept and was as the last load listed it, a
        `tracked` write (one that leaves the kept folder as the write itself leaves the disk) moves the kept time to the
        folder's new time, so the next load does not list it again; any other write, or a folder someone else changed, leaves
        it to be listed. The folder is trusted at once, recent or not: what the store wrote it holds, and only a write by
        someone else within the same clock tick, after ours, can be missed (as for `OriginIndex.raw_written`)."""
        if not self._depth:
            yield
            return
        with self._session_guard:
            key = str(directory)
            kept = self._dirs.get(key)
            before = _mtime(directory) if kept is not None else STALE
            try:
                yield
            except BaseException:
                if kept is not None:
                    self._dirs[key] = (STALE, False, kept[2])
                raise
            if kept is not None:
                ok = tracked and kept[0] == before and self._dirs.get(key) is kept
                self._dirs[key] = (_mtime(directory), True, kept[2]) if ok else (STALE, False, kept[2])

    def _stale(self, *directories: Path) -> None:
        """A write the session does not follow was made in these folders (all of them if none is named): list them again."""
        with self._session_guard:
            for key in [str(d) for d in directories] or list(self._dirs):
                if (kept := self._dirs.get(key)) is not None:
                    self._dirs[key] = (STALE, False, kept[2])

    def read(self, rel: str) -> Note:
        """The note or raw source at a bundle path; InputError for anything that is not one."""
        root = self.root.resolve()
        path = (root / rel.strip().lstrip("/")).resolve()
        if not path.is_relative_to(root) or path.suffix != ".md" or path.name in (INDEX, LOG):
            raise InputError(f"not a wiki document: {rel}")
        if not path.is_file():
            raise FileNotFoundError(rel)
        stamp = stamp_of(path.stat())
        doc = OKFDocument.read(path)
        return Note(path, path.relative_to(root).as_posix(), doc.frontmatter, doc.body, stamp=stamp)

    def note_at(self, rel: str) -> Note | None:
        """The note at a bundle path as `load` would give it, with no folder: from what this process parsed of its file if the file did
        not change since (it is kept for the next time, as a load keeps it, and dropped once the file is gone). None where a load finds
        no note, or for a file gone."""
        parts = rel.split("/")
        if not self.is_note_path(rel) or any(not p or ":" in p or "\\" in p for p in parts):
            return None
        where = os.path.join(self.root, *parts)
        try:
            st = os.stat(where)
        except OSError:  # gone: what was parsed of it is of no use any more
            self._parsed.pop(where, None)
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        return self._note_of(where, rel, None, st, stamp_of(st), self._parsed)

    # -- writing --------------------------------------------------------------

    def create_folder(
        self, parent: Folder, name: str, description: str, mark: str = "", *, log_it: bool = True, routable: bool = True
    ) -> tuple[Folder, bool]:
        """Create `parent/name` and list it in the parent's index; (folder, created).

        A sibling with the same slug is reused instead of duplicated. `mark` ends the log line (", by plan"); with
        `log_it` false the caller writes the log line. With `routable` false the folder is closed to automatic filing
        (see `set_routable`).
        """
        name = slugify(name, max_len=40, fallback="topics")
        if parent.is_root and name == RAW:
            name = "raw-topics"
        if existing := parent.subfolder(name):
            return existing, False
        rel = f"{parent.rel}/{name}" if parent.rel else name
        folder = Folder(parent.path / name, rel, " ".join(description.split()), parent=parent)
        held = self._holds(parent)
        with self._writing(parent.path, tracked=held):
            txn.mkdir(folder.path, parents=True, exist_ok=True)
            parent.subfolders.append(folder)
            if held:
                self._loose.setdefault(str(parent.path), []).append(folder)
        self.write_index(folder)
        self.write_index(parent)
        if not routable:
            self._mark_closed(folder, True)
        if log_it:
            closed = "" if routable else ", closed to automatic filing"
            self.append_log("Created folder", folder.description + mark + closed, f"{rel}/{INDEX}", folder.label)
        return folder, True

    def set_routable(self, rel: str, value: bool) -> Folder:
        """Open or close a folder to automatic filing: a closed folder, and the folders below it, are never offered to the
        routing, planning or consolidation; notes can still be moved there. InputError for the root or no folder."""
        folder = self.load().find(rel)
        if folder is None:
            raise InputError(f"no folder at /{rel.strip().strip('/')}")
        if folder.is_root:
            raise InputError("the root folder cannot be closed")
        if folder.routable != value:
            self._mark_closed(folder, not value)
            self.append_log("Reopened folder" if value else "Closed folder", "to automatic filing", f"{folder.rel}/{INDEX}", folder.label)
        return folder

    def _mark_closed(self, folder: Folder, closed: bool) -> None:
        marker = folder.path / CLOSED
        with self._writing(folder.path, tracked=self._holds(folder)):
            if closed:
                txn.touch(marker)
            else:
                txn.unlink(marker, missing_ok=True)
        folder.routable = not closed

    def write_note(
        self,
        folder: Folder,
        *,
        title: str,
        summary: str,
        body: str,
        tags: list[str],
        source: dict[str, Any],
        fields: dict[str, Any] | None = None,
        overflow: dict[str, float] | None = None,
        omitted: list[str] | None = None,
        slug: str | None = None,
    ) -> Note:
        """Write a new note in `folder`. `overflow`, when given, marks a note the limit on top-level folders deviated (filed in the
        likeliest existing root instead of a new one). `omitted`: the headings of its source that the body leaves out. `slug`: the
        file name `declare_filing` chose."""
        slug = slug or self.unique_slug(folder, slugify(title))
        path = folder.path / f"{slug}.md"
        rel = f"{folder.rel}/{slug}.md" if folder.rel else f"{slug}.md"
        body = self._without_dead_links(body, rel)
        note_body = body.rstrip() + "\n"
        frontmatter: dict[str, Any] = {
            "type": NOTE_TYPE,
            "id": uuid.uuid4().hex[:12],  # stable: no update, move or revision changes it, and no link or index uses it
            "title": title.strip(),
            "description": summary.strip(),
            "tags": tags,
            "generated": {"by": self.actor, "at": now_iso(), "body_hash": body_hash(split_see_also(note_body)[0])},
            "sources": [{"id": "s1", **source}],
        }
        if fields:
            frontmatter["fields"] = dict(fields)
        if overflow is not None:
            frontmatter["overflow"] = {**overflow, "at": now_iso()}
        if omitted:
            frontmatter["omitted"] = omitted
        note = Note(path, rel, frontmatter, body.rstrip() + "\n", folder)
        self._save(note, add=True)
        self.write_index(folder)
        return note

    def update_note(
        self,
        note: Note,
        *,
        title: str,
        summary: str,
        body: str,
        tags: list[str],
        source: dict[str, Any],
        fields: dict[str, Any] | None = None,
        previous_version: str | None = None,
        omitted: list[str] | None = None,
    ) -> Note:
        """Rewrite a note in place, keeping unknown frontmatter keys and its See-also links.

        `fields` are added to the note's own (new keys win). `previous_version`: the archived copy of the body it replaces.
        `omitted`, when given, replaces the note's `omitted` headings (none: the field goes).
        """
        fm = note.frontmatter
        was = (note.title, note.summary)
        fm["title"], fm["description"] = title.strip(), summary.strip()
        fm["tags"] = list(dict.fromkeys([*note.tags, *tags]))
        if previous_version:
            fm["previous_version"] = previous_version
        sources: list[Any] = fm["sources"] if isinstance(fm.get("sources"), list) else []
        used = {str(s.get("id")) for s in sources if isinstance(s, dict)}
        new_id = next(f"s{n}" for n in range(1, len(sources) + 2) if f"s{n}" not in used)
        fm["sources"] = [*sources, {"id": new_id, **source}]
        if fields:
            old = fm.get("fields")
            fm["fields"] = {**(old if isinstance(old, dict) else {}), **fields}
        note.body = join_see_also(self._without_dead_links(body, note.rel), split_see_also(note.body)[1])
        if omitted is not None:
            if omitted:
                fm["omitted"] = omitted
            else:
                fm.pop("omitted", None)
        fm["generated"] = {"by": self.actor, "at": now_iso(), "body_hash": body_hash(split_see_also(note.body)[0])}
        self._save(note)
        if note.folder is not None:
            self.write_index(note.folder)
        if (note.title, note.summary) != was:
            self._refresh_see_also(note)
        return note

    def _without_dead_links(self, body: str, rel: str) -> str:
        """The body of the note at `rel` with the links that lead nowhere in the wiki written as text."""
        return dead_links_to_text(body, rel, lambda target: (self.root / target).is_file())

    def _refresh_see_also(self, note: Note) -> None:
        """Show the note's title and summary in the See-also entries that link to it, in the notes of the tree.

        Only the entries change, and their link stays; archived versions are not touched.
        """
        top = note.folder
        while top is not None and top.parent is not None:
            top = top.parent

        def shown(other: Note) -> str:
            return refresh_entries(other.body, other.rel, note.rel, lambda link: _entry(note.title, link, note.summary))

        def refresh(other: Note) -> bool:
            body = shown(other)
            if body == other.body:
                return False
            other.body = body
            return True

        for other in top.all_notes() if top is not None else self.load().all_notes():
            if other is note or other.rel == note.rel or note.path.name not in other.body:  # a link names the file
                continue
            if shown(other) != other.body:
                self._edit(other, refresh)

    def archive_note(self, note: Note) -> str:
        """Keep a copy of a note as it is, in the hidden archive; returns its bundle path ("/.archive/…").

        `load()` skips the folder, so no step sees the copy. No index or log entry is written.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        base = f"{ARCHIVE_DIR}/{note.rel.removesuffix('.md')}--{stamp}"
        rel = next(r for n in range(1, 1000) if not (self.root / (r := f"{base}{f'-{n}' if n > 1 else ''}.md")).exists())
        fm = {**note.frontmatter, "status": "archived", "archived_from": f"/{note.rel}", "archived_at": now_iso()}
        path = self.root / rel
        with self._writing(self.root, tracked=True):  # the first copy makes the folder, the only change of the root
            txn.mkdir(path.parent, parents=True, exist_ok=True)
            write_atomic(path, OKFDocument(fm, note.body).serialize())
        return f"/{rel}"

    def link(self, a: Note, b: Note) -> bool:
        """Add a See-also entry in both notes; False if they were already linked."""
        changed = self._add_see_also(a, b)
        changed = self._add_see_also(b, a) or changed
        return changed

    def _add_see_also(self, note: Note, target: Note) -> bool:
        link = rel_link(note_dir(note.rel), target.rel)

        def add(one: Note) -> bool:
            main, entries = split_see_also(one.body)
            if any(f"]({link})" in e for e in entries) or one is target:
                return False
            entries.append(_entry(target.title, link, target.summary))
            one.body = join_see_also(main, entries)
            return True

        if note is target or any(f"]({link})" in e for e in split_see_also(note.body)[1]):
            return False
        return self._edit(note, add)

    def raw_slug(self, title: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return self.unique_slug(Folder(self.root / RAW, RAW), f"{stamp}-{slugify(title, max_len=40)}")

    def declare_filing(self, folder: Folder | None, *, raw_title: str, note_title: str | None) -> tuple[str, str | None]:
        """Choose the file names of the raw copy (and the new note in `folder`, if there is one) a filing is about to write, and tell the
        section's undo log of them and of the index and log it is about to append to in ONE record, so that the filing pays one
        flush for what it would pay four for. Returns the names, for `save_raw` and `write_note` to use. Nothing is written."""
        raw = self.raw_slug(raw_title)
        note = self.unique_slug(folder, slugify(note_title)) if folder is not None and note_title is not None else None
        section = txn.current()
        if section is not None:
            new = [self.root / RAW / f"{raw}.md"]
            extend = [self.root / LOG]
            if folder is not None and note is not None:
                new.append(folder.path / f"{note}.md")
                extend.append(folder.path / INDEX)
            section.declare(new=new, extend=extend)
        return raw, note

    def save_raw(
        self,
        text: str,
        *,
        title: str,
        resource: str | None,
        origin: Origin | None = None,
        slug: str | None = None,
        event_at: str | None = None,
    ) -> str:
        """Keep a copy of an ingested source; returns its bundle-absolute path ("/raw/…").

        The frontmatter records the text's hash and, when given, where the original lives and `event_at`, the time of the event it tells
        of (a top-level key, written only when given: a copy without it is as it has always been). `slug`: the file name `declare_filing`
        chose.
        """
        slug = slug or self.raw_slug(title)
        fm: dict[str, Any] = {"type": SOURCE_TYPE, "title": title}
        if resource:
            fm["resource"] = resource
        fm["hash"] = content_hash(text)
        if origin is not None:
            fm["origin"] = origin.to_dict()
            fm["seq"] = 1 + max((c.seq for c in self._origins.copies(origin.key)), default=0)  # save order per key
        if event_at:
            fm["event_at"] = event_at
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        txn.mkdir(self.root / RAW, exist_ok=True)
        before = self._origins.raw_time()
        write_atomic(self.root / RAW / f"{slug}.md", OKFDocument(fm, text).serialize())
        self._origins.raw_written(f"{slug}.md", before)
        return f"/{RAW}/{slug}.md"

    def source_text(self, note: Note, bodies: dict[str, str] | None = None) -> str:
        """The text of the raw copies a note cites, as ingested; the note's own text when it cites none still there.
        `bodies`: the raw copies already read, by resource, filled as they are read (a raw copy never changes)."""
        texts = []
        for resource in _raw_sources(note):
            if bodies is not None and resource in bodies:
                texts.append(bodies[resource])
                continue
            path = self.root / resource.lstrip("/")
            with contextlib.suppress(OSError, OKFDocumentError):
                texts.append(OKFDocument.read(path).body)
                if bodies is not None:
                    bodies[resource] = texts[-1]
        return "\n\n".join(texts) or note.full_text()

    def raw_copies(self) -> list[tuple[dict[str, Any], str]]:
        """Every raw copy as (frontmatter, text), oldest first. One that cannot be read is left out."""
        found = []
        for path in sorted((self.root / RAW).glob("*.md")):
            with contextlib.suppress(OSError, OKFDocumentError):
                doc = OKFDocument.read(path)
                found.append((doc.frontmatter, doc.body))
        return found

    def find_origin(self, key: str) -> OriginRecord | None:
        """The latest raw copy with this origin key that a note cites, and the notes citing any of its copies.

        Copies no note cites (an ingest interrupted after saving the raw) are ignored: None if none is cited.
        """
        copies = self._origins.copies(key)
        if not copies:
            return None
        rels = [f"/{RAW}/{c.name}" for c in copies]
        # Notes ordered by the newest copy they cite, so the last one cites the latest cited copy.
        newest = self._origins.citing(rels)
        if not newest:
            return None
        top = max(newest.values())
        latest = copies[top]
        return OriginRecord(
            raw=rels[top],
            hash=latest.hash,
            version=latest.version,
            removed=latest.removed,
            notes=sorted(newest, key=lambda rel: (newest[rel], rel)),
        )

    def adopt_raw(self, origin: Origin, text: str) -> bool:
        """Give `origin` to a raw copy saved without one, when a note cites it and its text is `text`; False if none is.

        Only the frontmatter changes: the copy's text and hash stay, so the copy is still the text as ingested.
        """
        copies = self._origins.unclaimed(content_hash(text))
        newest = self._origins.citing([f"/{RAW}/{c.name}" for c in copies])
        if not newest:
            return False
        path = self.root / RAW / copies[max(newest.values())].name
        raw = path.read_text(encoding="utf-8")
        fm = OKFDocument.parse(raw).frontmatter
        fm["origin"] = origin.to_dict()
        fm["seq"] = 1 + max((c.seq for c in self._origins.copies(origin.key)), default=0)
        end = raw.index(f"\n{DELIMITER}\n", len(DELIMITER))  # a saved copy always has a frontmatter block
        head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
        write_atomic(path, f"{DELIMITER}\n{head}{raw[end:]}")
        self._origins.touch(path.name)
        return True

    def seen_text(self, text: str) -> tuple[str, str] | None:
        """The note that cites a raw copy of exactly this text saved without an origin, and that copy ("/raw/…"); None if there is none.
        What `adopt_raw` looks for, without writing: a text kept before (an `ingest`, or a `remember` without a key) is not filed again."""
        copies = self._origins.unclaimed(content_hash(text))
        newest = self._origins.citing([f"/{RAW}/{c.name}" for c in copies])
        if not newest:
            return None
        rel = max(newest, key=lambda r: (newest[r], r))
        return rel, f"/{RAW}/{copies[max(newest.values())].name}"

    def raw_event_at(self, raw: str) -> str | None:
        """The `event_at` a raw copy ("/raw/…") was saved with, if it has one."""
        with contextlib.suppress(OSError, OKFDocumentError):
            value = OKFDocument.read(self.root / raw.lstrip("/")).frontmatter.get("event_at")
            return str(value) if value else None
        return None

    def origin_keys(self, prefix: str) -> set[str]:
        """The origin keys of raw copies starting with `prefix`."""
        return self._origins.keys(prefix)

    def mark_origin_removed(self, key: str) -> OriginRecord | None:
        """Stamp `removed` on every raw copy of this key that lacks it; the copies' text is untouched.

        Returns the record (None if the key is unknown). Notes stay: the wiki is memory.
        """
        changed = False
        for copy in self._origins.copies(key):
            if not copy.removed:
                path = self.root / RAW / copy.name
                text = path.read_text(encoding="utf-8")
                fm = OKFDocument.parse(text).frontmatter
                end = text.index(f"\n{DELIMITER}\n", len(DELIMITER))  # a saved copy always has a frontmatter block
                fm["removed"] = now_iso()
                head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
                write_atomic(path, f"{DELIMITER}\n{head}{text[end:]}")
                self._origins.touch(path.name)
                changed = True
        record = self.find_origin(key)
        if record is not None and changed:
            self.append_log("Source removed", "deleted at its source", record.raw.lstrip("/"), key)
        return record

    def unclaimed_raws(self, hash: str) -> list[str]:
        """The raw copies ("/raw/…") of a text saved with no origin, by the hash of the text ("sha256:…"), oldest first."""
        return [f"/{RAW}/{c.name}" for c in self._origins.unclaimed(hash)]

    def unclaimed_hashes(self) -> set[str]:
        """The hashes of the texts saved with no origin."""
        return self._origins.hashes()

    def origin_raws(self, key: str) -> list[str]:
        """Every raw copy with this origin key, cited by a note or not, oldest first ("/raw/…")."""
        return [f"/{RAW}/{c.name}" for c in self._origins.copies(key)]

    def origins_raws(self, keys: Iterable[str]) -> dict[str, list[str]]:
        """`origin_raws` for many keys, with one look at the raw copies."""
        return {k: [f"/{RAW}/{c.name}" for c in v] for k, v in self._origins.copies_of(keys).items()}

    def read_raw(self, resource: str) -> tuple[dict[str, Any], str]:
        """The frontmatter and the text of a raw copy ("/raw/…")."""
        doc = OKFDocument.read(self.root / resource.lstrip("/"))
        return doc.frontmatter, doc.body

    def purge_notes(
        self, root: Folder | None, notes: list[Note], raws: list[str], keep: Collection[str] = (), *, kept: list[Note] | None = None
    ) -> Deleted:
        """Delete `notes` and every raw copy in `raws` (cited or not), with the archived versions of the notes and the ones
        citing a raw copy of `raws`. The raw copies in `keep` stay even if no note cites them. Nothing is written to the log. `kept`: as
        for `_delete`, the notes that may link to the notes or cite their raw copies, in place of the tree."""
        deleted = self._delete(root, notes, [], keep=keep, drop=raws, kept=kept)
        self.remove_raws(raws, deleted)
        return deleted

    def remove_raws(self, raws: Iterable[str], deleted: Deleted) -> None:
        """Delete these raw copies ("/raw/…") that are still there, and say so in `deleted.sources`."""
        for resource in raws:
            path = self.root / resource.lstrip("/")
            if path.is_file():
                txn.unlink(path)
                deleted.sources.append(resource)
        deleted.sources = list(dict.fromkeys(deleted.sources))

    def scrub_log(self, names: Iterable[str], spare: Collection[str] = ()) -> int:
        """Say `[purged]` in place of the text of every line of log.md that names one of `names`; how many lines changed.

        A line keeps its verb and its author (` · by <author>`), loses the rest: the title, the path, the link. The line is
        found by a name as it is written in it: a path, a raw copy's file name, "[title](" for a link. A link "[title](path)" whose
        path is one of `spare` (a note that stays, with the same title as one that goes) is not taken by its title.

        Each line is overwritten where it stands, by a line of the same length (spaces after `[purged]` make up the difference):
        the file is not rewritten, so the cost of a purge does not grow with the log, and the undo keeps the bytes of the lines, not
        the file, which `txn` erases when the section ends. A line too short to hold the marker is the exception: the file is then
        written whole, as it was, for all the lines of the call.
        """
        path = self.root / LOG
        wanted = [n for n in dict.fromkeys(names) if n]
        if not wanted or not path.exists():
            return 0
        data = read_bytes(path)
        try:
            data.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            enc = "latin-1"
        needles = [n.encode(enc) for n in wanted if n.encode(enc, errors="ignore") == n.encode(enc, errors="replace")]
        if not needles:
            return 0
        edits: list[tuple[int, bytes, bytes]] = []
        done = -1
        kept_paths = {p.lstrip("/") for p in spare}
        for m in re.finditer(b"|".join(re.escape(n) for n in needles), data):
            if m.start() < done:  # another name of the line that was just taken
                continue
            if kept_paths and m.group(0).startswith(b"[") and m.group(0).endswith(b"]("):
                line_end = data.find(b"\n", m.end())
                close = data.find(b")", m.end(), len(data) if line_end < 0 else line_end)
                target = data[m.end():close].decode(enc, errors="replace").split("#")[0] if close >= 0 else ""
                if target.lstrip("/") in kept_paths:  # a note that stays and has the title of one that goes: its line is its own
                    continue
            start = data.rfind(b"\n", 0, m.start()) + 1
            end = data.find(b"\n", m.end())
            end = len(data) if end < 0 else end
            done = end
            was = data[start:end]
            kept = was[:-1] if was.endswith(b"\r") else was
            head = _LOG_LINE.match(kept.decode(enc))
            if head is None:
                continue
            by = _LOG_BY.search(head["text"])
            tail = (by[0] if by else "").encode(enc)
            core = f"{head['verb']}{PURGED}".encode(enc)
            room = len(kept) - len(core) - len(tail)
            if room < 0:
                return self._scrub_log_whole(path, wanted)
            new = core + b" " * room + tail
            if new != kept:
                edits.append((start, kept, new))
        if edits:
            with self._writing(self.root, tracked=True):
                txn.patch(path, edits)
            self._log = logfile.LogTail()  # the tail of the file may be a line that changed
        return len(edits)

    def _scrub_log_whole(self, path: Path, wanted: list[str]) -> int:
        """`scrub_log` for a log with a line too short to be overwritten in place: the file is written whole, lines of their own length.
        Counted in `log_rewrites` and said in the log of the library: it costs the whole file, as before the lines were written in place."""
        self.log_rewrites += 1
        log.warning("log.md has a line too short to be purged in place: the file is written whole")
        found = re.compile("|".join(re.escape(n) for n in wanted))
        lines = read_utf8_or_latin1(path).splitlines()
        changed = 0
        for i, line in enumerate(lines):
            head = _LOG_LINE.match(line)
            if head is not None and found.search(line) is not None:
                by = _LOG_BY.search(head["text"])
                lines[i] = f"{head['verb']}{PURGED}{by[0] if by else ''}"
                changed += lines[i] != line
        if changed:
            with self._writing(self.root, tracked=True):
                write_atomic(path, "\n".join(lines).rstrip() + "\n")
            self._log = logfile.LogTail()  # the file is another one now
        return changed

    def write_index(self, folder: Folder) -> None:
        """Write the folder's `index.md`. When the file holds exactly the entries it should, apart from notes it lacks, and ends with
        the `# Notes` section, the lacking entries are appended (a note's entry costs its own bytes, whatever the folder holds); the
        entries of a folder are a set, and the new ones stand last until the next full write. Any other difference (an entry that
        changed, a note that left, a subfolder made or gone, a line somebody added) writes the file whole, the notes in order, as
        ever; no difference writes nothing."""
        lead: list[str] = []
        if folder.is_root:
            lead.append(f'---\nokf_version: "{OKF_VERSION}"\n---\n')
        if folder.subfolders:
            lines = [_entry(f.name, f"{f.name}/{INDEX}", f.description) for f in folder.subfolders]
            lead.append("# Subdirectories\n\n" + "\n".join(lines) + "\n")
        show_notes = bool(folder.notes or not folder.subfolders)
        entries = [_entry(n.title, f"{n.slug}.md", n.summary) for n in folder.notes] if show_notes else []
        prefix = "\n".join(lead) + "\n" if lead else ""
        text = "\n".join([*lead, "# Notes\n\n" + "\n".join(entries) + ("\n" if entries else "")]) if show_notes else "\n".join(lead)
        path = folder.path / INDEX
        with self._writing(folder.path, tracked=self._holds(folder)):
            current = read_utf8_or_latin1(path) if path.exists() else None
            if current == text:
                return
            if show_notes and current is not None and (add := self._missing_entries(current, prefix + "# Notes\n\n", entries)) is not None:
                if add:
                    txn.append(path, add)
                return
            write_atomic(path, text)

    @staticmethod
    def _missing_entries(current: str, head: str, entries: list[str]) -> str | None:
        """What to append to an index file `current` to have all of `entries` in it: None if the file is not a prefix `head` followed
        by some of the entries (once each, as they are), so that it has to be written whole; "" if it lacks none."""
        if not current.startswith(head):
            return None
        present = current[len(head) :].splitlines()
        wanted = set(entries)
        if len(set(present)) != len(present) or any(line not in wanted for line in present):
            return None
        have = set(present)
        missing = [e for e in entries if e not in have]
        if not missing:
            return ""
        return ("\n" if present and not current.endswith("\n") else "") + "\n".join(missing) + "\n"

    def append_log(self, verb: str, text: str, link: str | None = None, link_title: str | None = None) -> None:
        """One line at the end of `log.md`, in the order of arrival (see `llmw2.bundle.logfile`), under a `## YYYY-MM-DD` heading
        that is added when the day (UTC) is not the day of the last heading; inside `authored`, the line ends with ` · by <author>`.
        A log of 1.0.5 or earlier is first turned around. Nothing of the log is read or rewritten: a line costs its own bytes."""
        path = self.root / LOG
        entry = f"* **{verb}**: " + " - ".join(p for p in (f"[{link_title or link}]({link})" if link else "", text) if p)
        if (author := _author.get()) is not None:
            entry += f" · by {author}"
        if not path.exists():
            write_atomic(path, logfile.TITLE + "\n")
        tail = self._log
        if not tail.sync(path):  # the file is not as this store left it (or this store never looked): is it in the right order?
            if not self._log_is_current(path):
                self._turn_log(path)
            tail.sync(path)
        heading = f"## {utc_now().date().isoformat()}"
        new_day = tail.heading != heading
        chunk = ("\n" if tail.last and not tail.last.endswith(b"\n") else "") + (f"\n{heading}\n\n" if new_day else "") + entry + "\n"
        data = chunk.encode("utf-8")
        with self._writing(self.root, tracked=True):
            txn.append(path, chunk)
        tail.appended(path, data, heading if new_day else None, entry)

    def recent_log(self, limit: int) -> list[str]:
        """The last `limit` lines of `log.md`, newest first, read from the end. A log of 1.0.5 or earlier that has not been turned
        around yet (a read-only use) lists its lines as they are: newest first already."""
        path = self.root / LOG
        if limit <= 0 or not path.exists():
            return []
        if self._log_is_current(path):
            return logfile.newest_lines(path, limit)
        return [line for line in read_utf8_or_latin1(path).splitlines() if line.startswith("* ")][:limit]

    def last_split_at(self, folder_rel: str) -> int:
        """The most notes the folder held when a split was last tried on it, by the "Split" lines of `log.md` (0 if none): the log
        is read once by a store, then kept up to date by its own appends; a log changed by someone else is read again."""
        path = self.root / LOG
        tail = self._log
        tail.sync(path)
        if tail.splits is None:
            tail.splits = logfile.split_marks(read_utf8_or_latin1(path)) if path.exists() else {}
        return tail.splits.get(folder_rel, 0)

    # -- moving and describing ------------------------------------------------

    def move_note(
        self, rel: str, folder_rel: str, *, mark: bool = False, log_it: bool = True, root: Folder | None = None
    ) -> Note:
        """File a note in another folder, keeping every link to and from it valid; InputError when it cannot be.

        The new file is written first, then the links of the other notes and the archived copies are pointed at it,
        and the old file goes last, so a failure half way never loses the note.
        With `mark` the note says where it came from (`moved_from`: its folder) and drops its `overflow` mark; with
        `log_it` false the caller writes the log line; `root` is the loaded tree, for a caller that has it.
        """
        moved = self.move_notes([(rel, folder_rel)], mark=mark, root=root)[0]
        if log_it:
            self.append_log("Moved", f"from /{rel.strip().lstrip('/')} to /{moved.rel}", moved.rel, moved.title)
        return moved

    def move_notes(self, moves: Sequence[tuple[str, str]], *, mark: bool = False, root: Folder | None = None) -> list[Note]:
        """File many notes, as (note path, folder), in one pass: each moved note is written once, each other note that
        links to any of them at most once, the archived copies and the indexes once. InputError (nothing moved) when a note
        or folder is missing or a note is already in its folder. The caller writes the log line. `mark` as in `move_note`."""
        root = root or self.load()
        notes = {n.rel: n for n in root.all_notes()}
        plan: list[tuple[Note, Folder, str]] = []
        reserved: dict[str, set[str]] = {}
        for rel, folder_rel in moves:
            old = rel.strip().lstrip("/")
            note = notes.get(old)
            if note is None or note.folder is None:
                raise InputError(f"no note at /{old}: only notes of the tree can be moved, not the index, log, sources or archive")
            target = root.find(folder_rel)
            if target is None:
                raise InputError(f"no folder at /{folder_rel.strip().strip('/')}")
            if target is note.folder:
                raise InputError(f"/{old} is already in {target.label}")
            slug = self.unique_slug(target, note.slug, reserved.setdefault(target.rel, set()))
            reserved[target.rel].add(slug)
            plan.append((note, target, f"{target.rel}/{slug}.md" if target.rel else f"{slug}.md"))
        mapping = {note.rel: new for note, _, new in plan}

        def remap(path: str) -> str:
            return mapping.get(path, path)

        moved: list[Note] = []
        for note, target, new in plan:
            copy = Note(target.path / Path(new).name, new, dict(note.frontmatter), note.body, target)
            if mark and note.folder is not None:
                copy.frontmatter["moved_from"] = note.folder.label
                copy.frontmatter.pop("overflow", None)
            self._relink(copy, note.rel, new, remap)
            self._save(copy)  # the new files first
            moved.append(copy)
        for other in notes.values():
            if other.rel not in mapping:
                self._relink(other, other.rel, other.rel, remap)
        for path, doc, was in self._archived():
            if was in mapping:
                doc.frontmatter["archived_from"] = f"/{mapping[was]}"
                write_atomic(path, doc.serialize())
        touched: dict[str, Folder] = {}
        for (note, target, _), copy in zip(plan, moved, strict=True):
            txn.unlink(note.path)
            self._stale(note.path.parent)
            if note.folder is not None:
                note.folder.notes.remove(note)
                touched[note.folder.rel] = note.folder
            target.notes.append(copy)
            touched[target.rel] = target
        for folder in touched.values():
            self.write_index(folder)
        self._prune_links({copy.rel for copy in moved})
        return moved

    def move_folder(self, rel: str, new_rel: str, *, mark: bool = False) -> Folder:
        """Move or rename a folder with all it holds to `new_rel`, keeping every link valid; InputError when it cannot be.

        The directory is renamed whole, then the links of every note (inside and out) and the archived copies follow.
        The description stays. With `mark` each note inside says where it came from (`moved_from`: its folder) and drops
        its `overflow` mark. The caller writes the log line.
        """
        old, new = rel.strip().strip("/"), new_rel.strip().strip("/")
        root = self.load()
        folder = root.find(old)
        if folder is None or folder.is_root:
            raise InputError(f"no folder at /{old}")
        parent = root.find(new.rpartition("/")[0])
        name = new.rpartition("/")[2]
        if parent is None:
            raise InputError(f"no folder at /{new.rpartition('/')[0]}")
        if not name or new.startswith(old + "/") or parent.subfolder(name) or parent.note(name) or (parent.path / name).exists():
            raise InputError(f"cannot move /{old} to /{new}: that place is taken, or inside the folder itself")
        description, old_parent = folder.description, folder.parent
        came_from = {n.rel: n.folder.label for n in folder.all_notes() if n.folder is not None}  # the folder each note leaves
        self._stale()
        txn.replace_dir(folder.path, parent.path / name)
        root = self.load()
        moved = root.find(new)
        assert moved is not None

        def remap(path: str) -> str:
            return new + path[len(old) :] if path == old or path.startswith(old + "/") else path

        inside = {n.rel for n in moved.all_notes()}
        for note in root.all_notes():
            was = old + note.rel[len(new) :] if note.rel in inside else note.rel
            self._relink(note, was, note.rel, remap)
            if mark and note.rel in inside:
                note.frontmatter["moved_from"] = came_from[was]
                note.frontmatter.pop("overflow", None)
                self._save(note)
        for path, doc, was in self._archived():
            if was and remap(was) != was:
                doc.frontmatter["archived_from"] = f"/{remap(was)}"
                write_atomic(path, doc.serialize())
        moved.description = description
        assert moved.parent is not None
        self.write_index(moved.parent)
        self._prune_links(inside)
        if old_parent is not None and old_parent.rel != moved.parent.rel:
            self.write_index(root.find(old_parent.rel) or root)
        return moved

    def merge_folders(self, source: str, target: str) -> int:
        """Move every note of the folder `source` into `target`, keeping the structure of its subfolders (one that `target`
        has too is merged), then delete `source`; the number of notes moved. Each says where it came from (`moved_from`).
        All notes move in one pass (see `move_notes`). InputError when a folder is missing, they are the same, or one holds
        the other. The caller writes the log line."""
        src, dst = source.strip().strip("/"), target.strip().strip("/")
        root = self.load()
        if not src or not dst or root.find(src) is None or root.find(dst) is None:
            raise InputError(f"cannot merge /{src} into /{dst}: both must be folders of the wiki")
        if src == dst or dst.startswith(src + "/") or src.startswith(dst + "/"):
            raise InputError(f"cannot merge /{src} into /{dst}: they must be different, and neither inside the other")
        moves: list[tuple[str, str]] = []

        def plan(from_folder: Folder, into: Folder) -> None:
            moves.extend((n.rel, into.rel) for n in from_folder.notes)
            for sub in from_folder.subfolders:
                plan(sub, into.subfolder(sub.name) or self.create_folder(into, sub.name, sub.description, log_it=False)[0])

        source_folder, target_folder = root.find(src), root.find(dst)
        assert source_folder is not None and target_folder is not None
        plan(source_folder, target_folder)
        self.move_notes(moves, mark=True, root=root)
        left = [p for p in (self.root / src).rglob("*") if p.is_file() and p.name not in (INDEX, CLOSED)]
        if left:  # something that is not a note: the folder stays
            raise InputError(f"/{src} still holds a file that is not a note ({left[0].name}): move or delete it by hand")
        self._stale()
        txn.rmtree(self.root / src)
        parent = self.load().find(src.rpartition("/")[0])
        if parent is not None:
            self.write_index(parent)
        return len(moves)

    def _prune_links(self, moved: Collection[str]) -> None:
        """After notes changed place: the See-also entries `link_reach` no longer allows, between a moved note and any other,
        go from both notes. Only the moved notes and the notes that name one of them are looked at."""
        reach = self.link_reach
        if reach is None or not moved:
            return
        notes = {n.rel: n for n in self.load().all_notes()}
        names = {notes[r].path.name for r in moved if r in notes}
        cut: dict[str, set[str]] = {}
        for note in notes.values():
            mine = note.rel in moved
            if not mine and not any(name in note.body for name in names):
                continue
            for target in see_also_targets(note.body, note.rel):
                other = notes.get(target)
                if other is None or other is note or not (mine or target in moved) or reach(note, other):
                    continue
                cut.setdefault(note.rel, set()).add(target)
                cut.setdefault(other.rel, set()).add(note.rel)
        for rel, gone in cut.items():

            def drop(one: Note, gone: set[str] = gone) -> bool:
                body = drop_see_also(one.body, one.rel, gone.__contains__)
                if body == one.body:
                    return False
                one.body = body
                return True

            self._edit(notes[rel], drop)

    def _relink(self, note: Note, from_rel: str, to_rel: str, remap: Callable[[str], str]) -> None:
        """Point the links in a note's body through `remap` (it is written at `to_rel`); saved if they changed."""
        if rewrite_links(note.body, from_rel, to_rel, remap) == note.body:
            return

        def relink(one: Note) -> bool:
            body = rewrite_links(one.body, from_rel, to_rel, remap)
            if body == one.body:
                return False
            gen = one.frontmatter.get("generated")
            if isinstance(gen, dict) and gen.get("body_hash") == body_hash(split_see_also(one.body)[0]):
                gen["body_hash"] = body_hash(split_see_also(body)[0])  # still written by code alone: keep it provable
            one.body = body
            return True

        if note.path.exists():
            self._edit(note, relink)
        else:
            relink(note)

    def _archived(self) -> Iterator[tuple[Path, OKFDocument, str]]:
        """Every archived copy that can be read: (path, document, the bundle path it was archived from, "" if none)."""
        for path in sorted((self.root / ARCHIVE_DIR).rglob("*.md")):
            try:
                doc = OKFDocument.read(path)
            except OKFDocumentError:
                continue
            was = doc.frontmatter.get("archived_from")
            yield path, doc, was.lstrip("/") if isinstance(was, str) else ""

    def archived_of(self, gone: Callable[[str], bool], raws: Collection[str] = ()) -> list[Path]:
        """The archived versions of what `gone` says went (the path each was archived from), or that cite one of `raws`."""
        return [
            path
            for path, doc, was in self._archived()
            if (was and gone(was)) or (raws and any(r in raws for r in raw_sources(doc.frontmatter)))
        ]

    def _rehome_archive(self, old: str, new: str) -> None:
        """Archived copies of the note at `old` now say they come from `new`."""
        for path, doc, was in self._archived():
            if was == old:
                doc.frontmatter["archived_from"] = f"/{new}"
                write_atomic(path, doc.serialize())

    def describe_folder(self, rel: str, description: str) -> Folder:
        """Change the one-line description of a folder, kept in its parent's index; InputError for the root or none."""
        folder = self.load().find(rel)
        if folder is None:
            raise InputError(f"no folder at /{rel.strip().strip('/')}")
        if folder.parent is None:
            raise InputError("the root folder has no description")
        text = " ".join(description.split())
        if not text:
            raise InputError("a folder needs a description: one line saying what belongs in it")
        folder.description = text
        self.write_index(folder.parent)
        self.append_log("Described folder", text, f"{folder.rel}/{INDEX}", folder.label)
        return folder

    # -- deleting -------------------------------------------------------------

    def delete_note(
        self, rel: str, *, root: Folder | None = None, linking: Callable[[list[str]], list[str] | None] | None = None
    ) -> Deleted:
        """Delete one note; FileNotFoundError when no note of the tree is at `rel`. `root`: the tree as this section just loaded it (the
        lock held since), not loaded again. `linking`, with no `root`: given the paths of the note and of the raw copies it cites, every
        note that may link to the one or cite one of the others, more if it likes (`SearchIndex.linking`), or None when it cannot say;
        the store then reads the note's folder and those notes alone, and looks at each as at every note of the tree, so the same
        notes change, the same way. None from it: the tree is loaded."""
        wanted = rel.strip().lstrip("/")
        if root is None and linking is not None:
            folders: dict[str, Folder | None] = {}
            found = self.notes_at([wanted], folders)
            if not found:
                raise FileNotFoundError(rel)
            note = found[0]
            may = linking([note.rel, *_raw_sources(note)])
            if may is not None:
                deleted = self._delete(None, [note], [], kept=self.near(may, {note.rel}, folders))
                self.append_log("Deleted note", f"{note.title} ({note.rel})")
                return deleted
        root = root if root is not None else self.load()
        note = next((n for n in root.all_notes() if n.rel == wanted), None)
        if note is None:
            raise FileNotFoundError(rel)
        deleted = self._delete(root, [note], [])
        self.append_log("Deleted note", f"{note.title} ({note.rel})")
        return deleted

    def delete_folder(self, rel: str, *, root: Folder | None = None) -> Deleted:
        """Delete a folder with its notes and subfolders; the root is the wiki itself (see `clear`). `root`: the tree as this section
        just loaded it (the lock held since), not loaded again."""
        root = root if root is not None else self.load()
        folder = root.find(rel)
        if folder is None:
            raise FileNotFoundError(rel)
        if folder.is_root:
            raise InputError("the root folder is the wiki itself: empty the wiki instead")
        deleted = self._delete(root, folder.all_notes(), [folder])
        self.append_log("Deleted folder", f"{folder.label}, with {len(deleted.notes)} notes")
        return deleted

    def remove_empty_folder(self, rel: str, why: str) -> bool:
        """Delete the folder at `rel` when it holds no note, no subfolder and nothing but its index; True when it did.
        The parent's index is written again and the log says why."""
        root = self.load()
        folder = root.find(rel)
        if folder is None or folder.is_root or folder.notes or folder.subfolders:
            return False
        if any(p.name != INDEX for p in folder.path.iterdir()):
            return False
        self._delete(root, [], [folder])
        self.append_log("Removed folder", f"{folder.label}, {why}")
        return True

    def clear(self, *, keep_folders: bool = False) -> Deleted:
        """Empty the wiki: every note, folder (with all it holds), raw copy and archived version go, and the log starts
        again. The sync cursors go too, so the next `sync` files every document again. Other files of the top level stay.
        With `keep_folders` (a rebuild) the folders stay, with their descriptions and no notes, and so does the classifier's
        answer cache, which is what a rebuild reuses; emptying the wiki removes it.
        """
        root = self.load()
        self._stale()
        deleted = self._delete(root, root.all_notes(), [] if keep_folders else list(root.subfolders))
        for raw in sorted((self.root / RAW).glob("*.md")):
            txn.unlink(raw)
            deleted.sources.append(f"/{RAW}/{raw.name}")
        deleted.sources = list(dict.fromkeys(deleted.sources))
        archive = self.root / ARCHIVE_DIR
        deleted.archived += [f"/{p.relative_to(self.root).as_posix()}" for p in sorted(archive.rglob("*.md"))]
        deleted.archived = list(dict.fromkeys(deleted.archived))
        txn.rmtree(archive, ignore_errors=True)
        txn.rmtree(self.root / SYNC_DIR, ignore_errors=True)
        if not keep_folders:
            txn.rmtree((self.root / CACHE_FILE).parent, ignore_errors=True)
        txn.unlink(self.root / PROPOSALS_FILE, missing_ok=True)  # the notes it names are gone
        write_atomic(self.root / LOG, logfile.TITLE + "\n")
        self._log = logfile.LogTail()
        self.append_log("Emptied the wiki", f"{len(deleted.notes)} notes and {len(deleted.folders)} folders deleted")
        return deleted

    def _delete(
        self,
        root: Folder | None,
        notes: list[Note],
        folders: list[Folder],
        keep: Collection[str] = (),
        drop: Collection[str] = (),
        *,
        kept: list[Note] | None = None,
    ) -> Deleted:
        """Remove `notes` and `folders` (with all they hold), and every link to them from the notes that stay.

        `keep`: raw copies that stay even if no note cites them any more. `drop`: raw copies being purged, whose archived
        versions go too, whichever note they were archived from. `kept`: the notes that stay and may link to what goes or cite its raw
        copies, in the order of the tree, in place of every note of `root` but those that go (`delete_note` with `linking`)."""
        gone_notes = {n.rel for n in notes}
        gone_dirs = [f.rel for f in folders]
        with self._session_guard:  # a `load` in another thread fills the cache under it too
            for gone_note in [*notes, *(n for top in folders for n in top.all_notes())]:
                self._parsed.pop(str(gone_note.path), None)  # what was deleted is kept in no cache (a purge leaves no text behind)

        def gone(target: str) -> bool:
            return target in gone_notes or any(target == d or target.startswith(f"{d}/") for d in gone_dirs)

        if kept is None:
            assert root is not None
            kept = [n for n in root.all_notes() if n.rel not in gone_notes]
        deleted = Deleted(notes=[n.rel for n in notes], folders=[f.label for top in folders for f in top.walk()])

        def unlink(one: Note) -> bool:
            body = drop_links(one.body, one.rel, gone)
            if body == one.body:
                return False
            one.body = body
            return True

        # a few notes gone: only the notes that name one of their files can link to them
        names = [n.path.name for n in notes] if not folders and len(notes) <= LINK_PREFILTER else []
        for note in kept:
            if names and not any(name in note.body for name in names):
                continue
            if drop_links(note.body, note.rel, gone) != note.body and self._edit(note, unlink):
                deleted.unlinked.append(note.rel)
        touched: list[Folder] = []
        self._stale()  # whatever goes, the kept folders are listed again
        for folder in folders:
            txn.rmtree(folder.path)
            if folder.parent is not None:
                folder.parent.subfolders.remove(folder)
                touched.append(folder.parent)
        for note in notes:
            txn.unlink(note.path, missing_ok=True)  # a note inside a deleted folder is gone already
            if note.folder is not None:
                note.folder.notes.remove(note)
                touched.append(note.folder)
        for folder in dict.fromkeys(touched):
            if folder.path.is_dir():
                self.write_index(folder)
        for path in self.archived_of(gone, drop):  # the archived versions of what went
            txn.unlink(path)
            deleted.archived.append(f"/{path.relative_to(self.root).as_posix()}")
        cited = {r for n in kept for r in _raw_sources(n)} | set(keep)
        for resource in dict.fromkeys(r for n in notes for r in _raw_sources(n)):
            path = (self.root / resource.lstrip("/")).resolve()
            if resource not in cited and path.parent == (self.root / RAW).resolve() and path.is_file():
                txn.unlink(path)
                deleted.sources.append(resource)
        return deleted

    def unique_slug(self, folder: Folder, slug: str, reserved: Iterable[str] = ()) -> str:
        taken = set(reserved)
        candidate, n = slug, 2
        while (folder.path / f"{candidate}.md").exists() or candidate in taken:
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    def check_unchanged(self, note: Note) -> None:
        """NoteChanged unless the note's file is as the store last read or wrote it (a note not on disk yet always is)."""
        if note.stamp is None:
            return
        try:
            now = stamp_of(os.stat(note.path))
        except OSError:
            raise NoteChanged(f"/{note.rel} was removed while it was being filed") from None
        if not same_stamp(note.stamp, now):
            raise NoteChanged(f"/{note.rel} was changed while it was being filed")

    def _save(self, note: Note, *, add: bool = False) -> None:
        """Write the note, unless its file changed since the store read or wrote it (NoteChanged): a hand edit is never lost.
        With `add` it is a new note of its folder, which is given it."""
        self.check_unchanged(note)
        folder = note.folder
        held = self._holds(folder) and (add or self._held.get(str(note.path)) is note)
        with self._writing(note.path.parent, tracked=held):
            write_atomic(note.path, OKFDocument(note.frontmatter, note.body).serialize())
            note.stamp = stamp_of(os.stat(note.path))
            if add and folder is not None:
                folder.notes.append(note)
                if held:
                    self._held[str(note.path)] = note
                    self._loose.setdefault(str(folder.path), []).append(note)

    def _edit(self, note: Note, change: Callable[[Note], bool]) -> bool:
        """Apply `change` (True if it changed the note) to the note as its file holds it now, and save it if it did.

        A note changed on disk since the store read it is read again first, and again if it changes before the save.
        """
        for attempt in range(3):
            self._current(note)
            if not change(note):
                return False
            try:
                self._save(note)
                return True
            except NoteChanged:
                if attempt == 2:
                    raise
        return False  # not reached

    def _current(self, note: Note) -> None:
        """Bring a note up to date with its file when that changed since the store read it."""
        if note.stamp is None:
            return
        try:
            stamp = stamp_of(os.stat(note.path))
            if same_stamp(note.stamp, stamp):
                return
            doc = OKFDocument.read(note.path)
        except (OSError, OKFDocumentError):
            raise NoteChanged(f"/{note.rel} was removed or broken while it was being filed") from None
        note.frontmatter, note.body, note.stamp = doc.frontmatter, doc.body, stamp


def _mtime(path: Path) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return STALE


_LOG_LINE = re.compile(r"(?P<verb>\* \*\*[^*]+\*\*: )(?P<text>.*)")
_LOG_BY = re.compile(r" · by [^\[\]()*]+$")  # the author a line ends with (`clean_author` leaves none of these characters)


def _by_name(one: Note | Folder) -> str:
    return os.path.normcase(one.path.name)


def _in_place(folder: Folder, added: list[Note | Folder]) -> bool:
    """Put what a write added at the end of the folder's lists where a listing would have it (by name); False if the lists
    are not as the write left them."""
    lists: list[tuple[type, list[Any]]] = [(Note, folder.notes), (Folder, folder.subfolders)]
    for kind, items in lists:
        new = [a for a in added if isinstance(a, kind)]
        if not new:
            continue
        if len(items) < len(new) or any(a is not b for a, b in zip(items[-len(new) :], new, strict=True)):
            return False
        del items[-len(new) :]
        for one in new:
            bisect.insort(items, one, key=_by_name)
    return True


def _descriptions(path: Path) -> dict[str, str]:
    """The descriptions of a folder's subfolders, by name, from its index.md (none without one)."""
    found: dict[str, str] = {}
    if (path / INDEX).exists():
        for _, link, text in parse_index(read_utf8_or_latin1(path / INDEX)):
            if listed := subfolder_of(link):
                found[listed] = text
    return found


def _raw_sources(note: Note) -> list[str]:
    """The raw copies a note cites ("/raw/…md")."""
    return raw_sources(note.frontmatter)


def raw_sources(frontmatter: dict[str, Any]) -> list[str]:
    """The raw copies a note (or an archived version of one) cites, from its frontmatter ("/raw/…md")."""
    sources = frontmatter.get("sources")
    return [
        str(s["resource"])
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and str(s.get("resource", "")).startswith(f"/{RAW}/")
    ]


def read_utf8_or_latin1(path: Path) -> str:
    """An index.md or log.md as text: UTF-8, or Latin-1 when an editor saved it so.

    Latin-1 reads every byte and loses none, so the next write turns the file into UTF-8 with its characters ("Café"
    stays "Café"); replacing the bad bytes would write "Caf\ufffd" back for good.
    """
    data = read_bytes(path)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def write_atomic(path: Path, text: str) -> None:
    """Write atomically, so a concurrent reader never sees half a file."""
    # One temporary name per process and thread: two writers never share, and so never clobber, a temp file.
    if (section := txn.current()) is not None:
        section.before_write(path, text.encode("utf-8"))  # the undo log has the image before; no flush here, the commit flushes
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")  # "\n" on every system: a bundle moves between them
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: the target is open in another thread or process for a moment
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.01 * 2**attempt)
    finally:
        tmp.unlink(missing_ok=True)
