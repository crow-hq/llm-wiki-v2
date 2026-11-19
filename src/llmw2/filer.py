# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `Wiki.remember` leaves behind and what files it: the receipts, the state of the entries, and the filer thread.

`remember` writes an entry (`E`) in the journal (`llmw2.bundle.journal`) and returns when it is on the disk. A thread of the process,
the filer, files the entries one at a time with the models of the `Wiki` that started it, the way `ingest` does (one section of the
bundle's lock, so a transaction), and says so in the journal: `F` (filed) is written inside the section's commit (see
`llmw2.bundle.txn`, `file_committed`), `D` (done) after the split of the folder, `A` a failed attempt, `X` an entry given up.

One `Hub` per bundle and process holds what the process knows of the journal (it reads it from where it stopped, never again from
the start), the journal object, and the filer. The `Wiki`s of the process on the same bundle share it, so a `Wiki` made for one
call (the MCP server makes one per call) sees the entries and the receipts of the others. Only one process files at a time: the one
that holds `filer.lock`, for all the entries of the journal, whoever wrote them.

A purge (`Wiki.purge`) writes a tombstone: an `E` record of its own id with no text, key or title, only the hash of the purged key
(`kh`). It forgets every entry of that key read before it (they close for good: no later record of theirs is taken), and the purge is
complete when `O` is written for it, after the segment in use was sealed and every segment before the next one was reused (what is not
done in them copied forward first, the tombstone with its whole state). Whoever holds the lease does that last part (`close_forget`):
the purge itself if the lease is free, else the filer at its next look at the journal.

Limits that are known: the duplicate of an entry is recognised when it is acknowledged only among what this process has read of the
journal; between processes it is recognised when it is filed, where `wait()` gives `unchanged` and the note that already holds the
text. The queue is limited by `QUEUE_LIMIT` per bundle, and a caller that waits for room is not served in order across processes.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import atexit
import contextlib
import contextvars
import hashlib
import logging
import os
import shutil
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llmw2.agents import lexical
from llmw2.bundle import journal as J
from llmw2.bundle import txn
from llmw2.bundle.filelock import FileLock
from llmw2.errors import FilerError, InputError, NoteChanged, ReplyInvalid, StepError

if TYPE_CHECKING:
    from llmw2.agents.librarian import IngestResult
    from llmw2.wiki import Wiki

log = logging.getLogger(__name__)

QUEUE_LIMIT = 256  # entries that are not done: a caller that finds this many waits for room
STALL = 10.0  # seconds with no entry closed while a caller waits for room: then QueueFull
DISK_RESERVE = 64 << 20  # bytes free on the disk of the bundle, under which nothing is accepted
LEASE_POLL = 0.2  # a process that does not file looks again for the lease this often
IDLE_POLL = 0.05  # the filer looks at the journal for entries of other processes this often
RETRY_FIRST = 2.0  # seconds before an entry that failed for a reason that may pass is tried again, doubling
RETRY_LAST = 300.0
ATTEMPTS = 3  # failed attempts for a reason that will not pass (the entry is the problem) before the entry is given up (`X`)
ENTRY_OVERHEAD = 1024  # bytes an entry costs in the journal besides its text (its record, and the records of its state)
CLOSED_KEPT = 4096  # entries done that a process remembers, for the duplicates and the receipts
DISK_LOOK = 1.0  # seconds a look at the free space of the disk is believed
FORGET_WAIT = 30.0  # seconds `purge` waits for the filer that holds the lease to complete its tombstone, before it says "pending"
TEXT_KEY = "text:"  # the key of an entry remembered without one: this and the hash of its text
ENTRY_KEYS = ("queue", "queue_limit", "filing", "failed", "oldest_s", "filer", "last_error", "journal_bytes", "disk_free", "warnings")


def sha_key(text: str) -> str:
    """The key an entry has when the caller gave none: the hash of its text."""
    return TEXT_KEY + hashlib.sha256(text.encode("utf-8")).hexdigest()


def key_hash(key: str) -> str:
    """What a tombstone keeps of the key it purges: its hash, never the key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


PENDING = "pending:"  # the path of an entry that `Wiki.search` shows before its note is in the index: this and its id
PENDING_SUMMARY = 200  # characters of the text an entry shown by `search` has for summary


@dataclass(frozen=True)
class Pending:
    """An entry as `Wiki.search` shows it, while its note is not in the index (`Hub.searchable`)."""

    id: str
    title: str  # the one the caller gave, or the first line of the text
    text: str
    fields: dict[str, Any]
    note: str  # the note its filing made or met (`F`), "" before
    at: str  # when it was acknowledged (`format_at`: the order of the strings is the order of the times)
    words: Counter[str] = field(default_factory=Counter, compare=False)  # counted once, in `of`

    @property
    def path(self) -> str:
        return PENDING + self.id

    @staticmethod
    def of(ident: str, title: str, text: str, fields: dict[str, Any], note: str, at: str) -> Pending:
        """The entry shown, with the count of its words: its text, after its title when the title is not its first line."""
        searched = text if text.lstrip().startswith(title) else f"{title}\n{text}"
        return Pending(ident, title, text, fields, note, at, Counter(lexical.words(searched)))


class Forgotten(FilerError):
    """The entry being filed was forgotten by a purge meanwhile: nothing of it is written."""


@dataclass
class Entry:
    """An entry of the journal and what the records after it say of it."""

    id: str
    key: str
    hash: str
    text: str
    title: str
    origin: dict[str, Any] | None  # set when the caller gave a key: Origin("remember", "", key)
    fields: dict[str, Any]
    author: str
    at: str  # when it was acknowledged
    event_at: str | None
    seg: int
    seen: float = field(default_factory=time.monotonic)  # when this process first read it
    filed: dict[str, Any] | None = None  # the `F` record: what the filing did
    done: bool = False
    failed: str | None = None  # the `X` record
    attempts: int = 0  # `A` records
    error: str | None = None  # the last error of an attempt, in this process
    cost: int = 0  # bytes of its record in the journal, and of the records of its state: counted once, when it is read
    counted: str | None = None  # "open" or "failed": which counter of the hub holds `cost`
    forget: dict[str, Any] | None = None  # a tombstone: `kh`, the hash of the key purged, and `upto` once the segment in use was sealed
    forgotten: bool = False  # a tombstone forgot it: closed for good, whatever comes after

    @property
    def closed(self) -> bool:
        return self.done or self.failed is not None or self.forgotten or self.forget is not None

    @property
    def sig(self) -> tuple[str, str, str | None]:
        return (self.key, self.hash, self.event_at)


class Hub:
    """What a process knows of the journal of one bundle."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.journal = J.Journal(root)
        self.cond = threading.Condition(threading.RLock())
        self.pos: tuple[int, int] | None = None
        self.entries: OrderedDict[str, Entry] = OrderedDict()
        self.by_sig: dict[tuple[str, str, str | None], str] = {}
        self.closed_ids: OrderedDict[str, None] = OrderedDict()
        # copies of a forgotten entry read after the tombstone that forgot it (let through by a reuse that did not see the tombstone in
        # time): read again from the journal by any process that reads the original, so that the reuse of the original's segment names
        # them on the tombstone even after a crash (`reuse`)
        self.let_through: dict[str, Entry] = {}
        self.errors: dict[str, tuple[int, str]] = {}  # entry id -> (count of failed attempts in this process, the last message)
        self.error_seq = 0
        self.open_bytes = 0  # the live backlog, kept as the entries are folded: `remember` reads it, it does not walk the entries
        self.failed_bytes = 0
        self.failed_n = 0
        self.open_n = 0
        self.closed_total = 0  # entries that closed in this process's reading: a caller that waits for room sees the queue move
        self.last_error: str | None = None
        self.filing: str | None = None
        self.compact_stopped: tuple[int, int] | None = None  # (segment, bytes) when the guard of the compaction held it back
        self.files_done: set[str] = set()  # tombstones whose files and undo log this process purged: a look again does the rest only
        self.shown: dict[str, Pending] = {}  # the entries `searchable` gives, kept after they close until the index has their note
        self.filer: Filer | None = None
        self.refs = 0
        self.prepared = False
        self._disk: tuple[float, int] = (0.0, 1 << 62)
        self._read_lock = threading.Lock()
        self._searched_at = -float("inf")  # when `searchable` last read the journal

    # -- reading the journal ------------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Read what was written since the last look and fold it into the entries."""
        with self._read_lock:
            records, pos = self.journal.read_from(self.pos)
            if self.journal.restarted:  # the bundle was deleted and made again under this process: what it knew is of another journal
                with self.cond:
                    self.entries.clear()
                    self.open_bytes = self.failed_bytes = self.failed_n = self.open_n = 0
                    self.by_sig.clear()
                    self.closed_ids.clear()
                    self.let_through.clear()
                    self.errors.clear()
                    self.shown.clear()
            if not records:
                self.pos = pos
                return
            with self.cond:
                self.pos = pos
                for rec in records:
                    self._fold(rec)
                self.cond.notify_all()

    def _fold(self, rec: J.Record) -> None:
        self._fold_one(rec)
        e = self.entries.get(str(rec.meta.get("id", "")))
        if e is not None:
            self._recount(e)

    def _recount(self, e: Entry) -> None:
        """Put the entry in the counter that holds it now (open, given up, none): once per record, never a walk of the hub."""
        now = "failed" if e.failed is not None and not e.forgotten else (None if e.closed else "open")
        if now == e.counted:
            return
        if e.counted == "open":
            self.open_bytes -= e.cost
            self.open_n -= 1
        elif e.counted == "failed":
            self.failed_bytes -= e.cost
            self.failed_n -= 1
        if now == "open":
            self.open_bytes += e.cost
            self.open_n += 1
        elif now == "failed":
            self.failed_bytes += e.cost
            self.failed_n += 1
        e.counted = now

    def _fold_one(self, rec: J.Record) -> None:
        meta = rec.meta
        ident = str(meta.get("id", ""))
        if rec.type == J.T_ENTRY:
            if not ident:
                return
            forget = meta["state"].get("forget") if isinstance(meta.get("state"), dict) else None
            if isinstance(forget, dict):
                self._tombstone(rec, ident, forget)
                return
            if ident in self.entries and self.entries[ident].forgotten:
                self.let_through[ident] = self.entries[ident]  # a copy after the tombstone: named before the original is zeroed
                return  # it stays forgotten
            if ident in self.entries:
                # A copy carried forward by the compaction (the first may still be there): one entry. The copy wins, text included, and
                # brings the state the filer had written (one record, so nothing of it can be missing).
                old = self.entries[ident]
                if rec.seg > old.seg:
                    self._uncount(old)  # the copy is the record that weighs now
                    old.seg = rec.seg
                    old.text = rec.text.decode("utf-8", errors="surrogatepass")
                    old.cost = Hub.entry_cost(rec.size)
                    self._state_of(old, meta)
                return
            text = rec.text.decode("utf-8", errors="surrogatepass")
            e = Entry(
                ident,
                str(meta.get("key", "")),
                str(meta.get("hash", "")),
                text,
                str(meta.get("title", "")),
                meta.get("origin") if isinstance(meta.get("origin"), dict) else None,
                meta.get("fields") if isinstance(meta.get("fields"), dict) else {},
                str(meta.get("author", "")),
                str(meta.get("at", "")),
                meta.get("event_at") if isinstance(meta.get("event_at"), str) else None,
                rec.seg,
            )
            e.cost = Hub.entry_cost(rec.size)
            self._state_of(e, meta)  # a copy carried forward is the first record of the entry that this process reads
            self.entries[ident] = e
            self.by_sig[e.sig] = ident
            if e.closed:
                self._closed(e)
            return
        e = self.entries.get(ident)
        if e is None or e.forgotten:
            return
        if rec.type == J.T_CLEANED:
            if e.forget is not None and not e.done:
                e.done = True
                self._closed(e)
            return
        if e.forget is not None:
            if rec.type == J.T_FILED and meta.get("files") is True:
                e.forget["files"] = True  # the purge's files are done: taking it up does not scan for the key (carried with it)
            if rec.type == J.T_FILED and isinstance(meta.get("forgot"), list):
                # copies that a reuse made after this tombstone without seeing it: forgotten by whoever reads them, even a process that
                # never read the original (`reuse` writes this before it zeroes the segment that held it)
                kh = str(e.forget.get("kh", ""))
                for other_id in meta["forgot"]:
                    self.let_through.pop(str(other_id), None)  # named: a reader that never saw the original forgets it
                    other = self.entries.get(str(other_id))
                    if other is not None and other.forget is None and not other.forgotten and key_hash(other.key) == kh:
                        self._forget(other)
            return
        if rec.type == J.T_FILED:
            e.filed = {k: v for k, v in meta.items() if k != "id"}
        elif rec.type == J.T_DONE:
            e.done = True
        elif rec.type == J.T_ATTEMPT:
            n = meta.get("n")
            e.attempts = max(e.attempts, n) if isinstance(n, int) else e.attempts + 1  # "n": a copy carried forward says how many
            e.error = str(meta.get("error", ""))
        elif rec.type == J.T_ABANDON:
            e.failed = str(meta.get("error", "")) or "given up"
        if e.closed:
            self._closed(e)

    def _tombstone(self, rec: J.Record, ident: str, forget: dict[str, Any]) -> None:
        """A tombstone, read for the first time: it forgets every entry of its key read before it, and, once it was carried forward,
        only those in the segments before `upto` (the seal of its completion) or, for a copy made by an ordinary reuse, before `seg`
        (the segment of the original): an entry of the same key written after it is a new memory, even if it stands before the copy.
        A copy read after the original only moves it."""
        old = self.entries.get(ident)
        if old is not None:
            if rec.seg > old.seg:
                old.seg, old.cost = rec.seg, Hub.entry_cost(rec.size)
                old.forget = {**(old.forget or {}), **forget}
            return
        kh, upto = str(forget.get("kh", "")), forget.get("upto")
        if not isinstance(upto, int):  # a copy made by an ordinary reuse: an entry after the original's segment was written after it
            upto = forget.get("seg")
        e = Entry(ident, "", "", "", "", None, {}, "", str(rec.meta.get("at", "")), None, rec.seg, forget=dict(forget))
        e.cost = Hub.entry_cost(rec.size)
        self.entries[ident] = e
        for other in list(self.entries.values()):
            if other.forget is not None or other.forgotten or (isinstance(upto, int) and other.seg >= upto):
                continue
            if key_hash(other.key) == kh:
                self._forget(other)

    def _forget(self, other: Entry) -> None:
        other.forgotten = True
        self.shown.pop(other.id, None)  # a search shows it no more, and no copy of its text stays
        self.errors.pop(other.id, None)
        if self.by_sig.get(other.sig) == other.id:
            del self.by_sig[other.sig]  # the same text may be remembered again
        self._closed(other)

    @staticmethod
    def _state_of(e: Entry, meta: dict[str, Any]) -> None:
        state = meta.get("state") if isinstance(meta.get("state"), dict) else {}
        if isinstance(state.get("filed"), dict):
            e.filed = state["filed"]
        if isinstance(state.get("attempts"), int):
            e.attempts = max(e.attempts, state["attempts"])
            e.error = str(state.get("error", e.error or ""))
        if isinstance(state.get("failed"), str):
            e.failed = state["failed"]

    def _uncount(self, e: Entry) -> None:
        if e.counted == "open":
            self.open_bytes -= e.cost
            self.open_n -= 1
        elif e.counted == "failed":
            self.failed_bytes -= e.cost
            self.failed_n -= 1
        e.counted = None

    def _closed(self, e: Entry) -> None:
        self._recount(e)
        self.closed_total += 1
        if e.failed is not None and not e.forgotten:
            return  # an entry given up is kept, text included: the compaction carries it forward, and it counts in the queue limit
        e.text = ""  # what a closed entry no longer needs
        self.closed_ids[e.id] = None
        while len(self.closed_ids) > CLOSED_KEPT:
            old, _ = self.closed_ids.popitem(last=False)
            gone = self.entries.pop(old, None)
            if gone is not None and self.by_sig.get(gone.sig) == old:
                del self.by_sig[gone.sig]

    # -- what the entries are -----------------------------------------------------------------------------------

    def searchable(self, look: float) -> list[Pending]:
        """The entries a search shows, after reading what is new: every entry open (not given up, forgotten or a tombstone), and every
        one shown before that was filed since (`F`, `D`): the caller keeps it while its index lacks the note (`unshow` once it has it;
        the entry's own text goes when it is done, this copy stays). A tombstone drops the copy of each entry it forgets. The journal is
        read when the shared tail hint says it moved (a `remember` of any process on the machine), else at most once every `look`
        seconds (a writer the hint does not show: another segment, no hint). In the order they were acknowledged, then of their ids."""
        now = time.monotonic()
        if self.journal.moved_since(self.pos) or now - self._searched_at >= look:
            self.refresh()
            self._searched_at = now
        with self.cond:
            for e in self.entries.values():
                if not e.closed and e.text:
                    title = e.title or next((line.strip() for line in e.text.splitlines() if line.strip()), "")
                    note = str(e.filed.get("note") or "") if e.filed else ""
                    shown = self.shown.get(e.id)
                    if shown is None or shown.text != e.text or shown.note != note:
                        self.shown[e.id] = Pending.of(e.id, title, e.text, dict(e.fields), note, e.at)
            for ident, shown in list(self.shown.items()):
                e = self.entries.get(ident)
                if e is None or e.forgotten or e.forget is not None or e.failed is not None:
                    del self.shown[ident]
                elif e.closed or e.filed:
                    note = str(e.filed.get("note") or "") if e.filed else ""
                    if not note:
                        del self.shown[ident]  # closed with no note: nothing to wait for
                    elif note != shown.note:
                        self.shown[ident] = Pending(shown.id, shown.title, shown.text, shown.fields, note, shown.at, shown.words)
            return sorted(self.shown.values(), key=lambda p: (p.at, p.id))  # the same order in every process, whatever it read first

    def unshow(self, ids: list[str]) -> None:
        """Entries whose note the index has: a search shows the note now."""
        with self.cond:
            for ident in ids:
                self.shown.pop(ident, None)

    def open_entries(self) -> list[Entry]:
        return [e for e in self.entries.values() if not e.closed]

    def find(self, sig: tuple[str, str, str | None]) -> Entry | None:
        """The entry with this key, text and event time that was not given up: the one a retry is the same as."""
        ident = self.by_sig.get(sig)
        e = self.entries.get(ident) if ident else None
        return e if e is not None and e.failed is None and not e.forgotten else None

    def with_key(self, key: str) -> list[Entry]:
        """The entries of this key that a purge would forget (open, filed, given up)."""
        return [e for e in self.entries.values() if e.key == key and e.forget is None and not e.forgotten]

    def forgets(self) -> list[Entry]:
        """The tombstones whose purge is not complete (no `O` yet)."""
        return [e for e in self.entries.values() if e.forget is not None and not e.done]

    @staticmethod
    def entry_cost(record_size: int) -> int:
        """What an entry weighs in the journal: its record (head, metadata, text) and the records of its state: the admission, the guard
        of the compaction and the copy count the same."""
        return record_size + ENTRY_OVERHEAD

    def backlog_bytes(self) -> tuple[int, int]:
        """(bytes of the entries open, bytes of the entries given up): the live backlog of the journal, from the counters."""
        return self.open_bytes, self.failed_bytes

    def failed_count(self) -> int:
        return self.failed_n

    def next_open(self) -> Entry | None:
        for e in self.entries.values():
            if not e.closed:
                return e
        return None

    def notify(self) -> None:
        with self.cond:
            self.cond.notify_all()

    # -- the disk -----------------------------------------------------------------------------------------------

    def disk_free(self) -> int:
        now = time.monotonic()
        if now - self._disk[0] > DISK_LOOK:
            try:
                self._disk = (now, shutil.disk_usage(self.root).free)
            except OSError:
                self._disk = (now, 1 << 62)
        return self._disk[1]

    # -- the filer ----------------------------------------------------------------------------------------------

    def start_filer(self, wiki: Wiki) -> None:
        with self.cond:
            if self.filer is not None and self.filer.is_alive() and not self.filer.stopping:
                self.filer.wiki = wiki  # the models of the wiki that asked last: the processes on a bundle share one configuration
                return
            self.filer = Filer(self, wiki)
            self.filer.start()

    def stop_filer(self, wiki: Wiki | None = None, timeout: float = 120.0) -> None:
        """Stop the thread (the one of `wiki`, when given) and wait for the section it is in: the queue is not emptied."""
        with self.cond:
            f = self.filer
        if f is None or (wiki is not None and f.wiki is not wiki):
            return
        f.stop()
        if f is not threading.current_thread():
            f.join(timeout)

    def nobody_files(self) -> bool:
        """No filer of this process is running, and nobody else holds the lease: what is queued will not be filed by itself."""
        f = self.filer
        if f is not None and f.is_alive() and not f.stopping:
            return False
        lease = FileLock(self.root / J.JOURNAL_DIR / "filer.lock")
        if lease.try_acquire():
            lease.release()
            return True
        return False


_hubs: dict[str, Hub] = {}
_hubs_guard = threading.Lock()


def hub_for(root: Path) -> Hub:
    """The hub of a bundle in this process, made if it is not there; the caller gives it back with `release`."""
    key = os.path.normcase(os.path.realpath(root))
    with _hubs_guard:
        hub = _hubs.get(key)
        if hub is None:
            hub = _hubs[key] = Hub(Path(root))
        hub.refs += 1
        return hub


def release(hub: Hub) -> None:
    with _hubs_guard:
        hub.refs -= 1
        if hub.refs > 0:
            return
        for k, h in list(_hubs.items()):
            if h is hub:
                del _hubs[k]
    hub.stop_filer()
    hub.journal.close()


@atexit.register
def _stop_all() -> None:  # the section a filer is in is let to end; a process killed anyway is recovered by the next one
    for hub in list(_hubs.values()):
        with contextlib.suppress(Exception):
            hub.stop_filer(timeout=30.0)


# -- the receipt ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Receipt:
    """What `remember` gives back: the entry is on the disk. `state` and `note` are read from the journal at each access."""

    id: str
    key: str
    at: str  # when the entry was acknowledged (UTC, `+00:00`, microseconds)
    event_at: str | None
    _hub: Hub = field(repr=False, compare=False)
    _ref: tuple[int, int] | None = field(default=None, repr=False, compare=False)  # (segment, end) of its record

    def _entry(self) -> Entry | None:
        self._hub.refresh()
        return self._hub.entries.get(self.id)

    @property
    def state(self) -> str:
        """"queued", "filing" (the filer of this process is on it), "filed" (the note exists), "failed" (given up) or "forgotten"
        (a purge took it out)."""
        e = self._entry()
        if e is None:
            self._unread()
            return "filed"  # read, and dropped from the hub: only a closed entry is
        if e.forgotten:
            return "forgotten"
        if e.failed is not None:
            return "failed"
        if e.filed is not None:
            return "filed"
        return "filing" if self._hub.filing == self.id else "queued"

    def _unread(self) -> None:
        """The hub has no such entry: it is closed and dropped, or it was never read (the journal cannot be read up to it). The second
        is `FilerError`: such an entry is not filed, and nothing here says it is."""
        hub = self._hub
        if hub.journal.stuck is not None or (self._ref is not None and hub.pos is not None and hub.pos < self._ref):
            seg, at = hub.journal.stuck or hub.pos or (0, 0)
            raise FilerError(f"the journal cannot be read up to entry {self.id} (stopped at byte {at} of segment {seg}): see check()")

    @property
    def note(self) -> str | None:
        e = self._entry()
        if e is None or e.forgotten or e.filed is None or not e.filed.get("note"):
            return None
        return str(e.filed["note"])

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """The record of the filing (`action`, `note`, `folder`, `title`, `related`: the ids of the notes it links to, `deviations`, ...)
        once the entry is filed.

        `FilerError` when an attempt to file it failed (the message is the first error), it was given up or forgotten, or nobody files;
        `TimeoutError` when `timeout` seconds pass first (the entry stays queued)."""
        hub = self._hub
        deadline = None if timeout is None else time.monotonic() + timeout
        errors_at = hub.errors.get(self.id, (0, ""))[0]
        quiet = time.monotonic()
        while True:
            e = self._entry()
            if e is not None and e.forgotten:
                raise FilerError(f"entry {self.id} was forgotten: a purge took its key out")
            if e is not None and e.failed is not None:
                raise FilerError(f"entry {self.id} was given up: {e.failed}")
            if e is None or e.filed is not None:
                if e is None:
                    self._unread()
                    return {"action": "unchanged"}
                return dict(e.filed or {})
            err = hub.errors.get(self.id)
            if err is not None and err[0] > errors_at:
                raise FilerError(f"filing entry {self.id} failed: {err[1]}")
            if hub.filer is not None and hub.filer.stopping and hub.nobody_files():
                raise FilerError("no filer is running: this wiki was closed or its filer stopped")
            if time.monotonic() - quiet > 1.0 and hub.nobody_files():
                raise FilerError("no filer is running: open the wiki with models and call drain()")
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise TimeoutError(f"entry {self.id} is not filed yet")
            with hub.cond:
                hub.cond.wait(IDLE_POLL if left is None else min(IDLE_POLL, left))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "at": self.at, "event_at": self.event_at, "state": self.state, "note": self.note}


# -- the filer ------------------------------------------------------------------------------------------------------


class Filer(threading.Thread):
    """Files the entries of the journal, in order, one at a time, while this process holds the lease."""

    def __init__(self, hub: Hub, wiki: Wiki) -> None:
        super().__init__(name="llmw2-filer", daemon=True)
        self.hub = hub
        self.wiki = wiki
        self.stopping = False
        self.state = "idle"
        self.kicked = threading.Event()  # drain: do not wait out the delay of a retry
        self.dirty = False  # entries were closed since the segments were last looked at for reuse
        self.forget_at = 0.0  # a tombstone left pending is looked at again after this time (`time.monotonic`)
        self.forget_tries = 0  # looks at it that did not complete it: the delay doubles, as for an entry that failed
        self.forget_for: str | None = None  # the tombstone those looks were at: a new one starts again from no delay

    def stop(self) -> None:
        self.stopping = True
        self.kicked.set()
        self.hub.notify()

    def kick(self) -> None:
        self.kicked.set()

    def run(self) -> None:
        """In a context of its own, empty: a thread that inherits its starter's context (a copy, or Python 3.14) would inherit
        `undo_suspended` and file without an undo log for as long as it lives."""
        contextvars.Context().run(self._main)

    def _main(self) -> None:
        lease = FileLock(self.hub.root / J.JOURNAL_DIR / "filer.lock", keep_open=True)
        try:
            while not self.stopping:
                if not lease.try_acquire():
                    self.state = "idle"
                    self._sleep(LEASE_POLL)
                    continue
                try:
                    self._serve()
                finally:
                    lease.release()
        finally:
            lease.close()
            self.state = "none"
            self.hub.filing = None
            self.hub.notify()

    def _sleep(self, seconds: float) -> None:
        self.kicked.wait(seconds)
        self.kicked.clear()

    def _serve(self) -> None:
        hub = self.hub
        with contextlib.suppress(Exception):
            self.wiki.store.recover()  # a section that a process died in, and the `F` record it owes
        while not self.stopping:
            try:
                hub.refresh()
                entry = hub.next_open()
                tombs = hub.forgets()
            except Exception as e:  # a journal that cannot be read: said, and tried again
                self._failed_read(e)
                self._sleep(IDLE_POLL * 4)
                continue
            if tombs and tombs[0].id != self.forget_for:
                self.forget_for, self.forget_tries, self.forget_at = tombs[0].id, 0, 0.0
            if tombs and hub.journal.stuck is None and time.monotonic() >= self.forget_at:
                try:
                    finished = self.wiki._finish_forget(tombs[0])
                except Exception as e:  # tried again later; the purge stays pending
                    self._failed_read(e)
                    finished = False
                if finished:
                    self.forget_tries = 0
                    continue
                # a reader holding the index, the guard of the compaction, an error: looked at again later, the filing goes on meanwhile
                self.forget_tries += 1
                self.forget_at = time.monotonic() + min(RETRY_LAST, RETRY_FIRST * 2 ** (self.forget_tries - 1))
            if entry is None:
                self.state = "idle"
                if self.dirty:
                    self.dirty = False
                    self._compact()
                with hub.cond:
                    if not self.stopping and not hub.open_entries():
                        hub.cond.wait(IDLE_POLL)
                continue
            self._file(entry)
            if self.dirty:
                self.dirty = False
                self._compact()

    def _failed_read(self, e: Exception) -> None:
        self.hub.last_error = f"{type(e).__name__}: {e}"
        self.state = "error"

    def _compact(self) -> None:
        """Reuse the oldest segment (one a call, `reuse`). A closure is never in an older segment than the `E` of its entry, so the `E`
        goes first and an entry is never seen open again. A copy is one record (the entry with its state): a crash between the copies and
        the reuse leaves the same entry twice and the hub takes the copy; the next compaction finds it already ahead and copies nothing."""
        hub, j = self.hub, self.hub.journal
        with contextlib.suppress(Exception):
            if self.stopping or j.stuck is not None:  # one segment a time: the filing does not wait for a long compaction
                return
            hub.refresh()
            older = j.reusable()
            hub.compact_stopped = None
            if not older:
                return
            reuse(hub, older[0])

    def _file(self, entry: Entry) -> None:
        hub = self.hub
        hub.filing = entry.id
        self.state = "filing"
        try:
            self.wiki._work_on(entry, self)
        except (InputError, StepError, ReplyInvalid) as e:
            if isinstance(e, NoteChanged):
                self._retry(entry, e, quick=True)
            else:
                self._attempt_failed(entry, e)
        except Exception as e:  # a model that is down, a spending cap, a disk that is full: the entry stays first
            self._retry(entry, e)
        finally:
            hub.filing = None
            hub.notify()

    def done(self, entry: Entry) -> None:
        self.hub.journal.append(J.T_DONE, {"id": entry.id}, sync=False)
        self.hub.errors.pop(entry.id, None)
        self.dirty = True
        self.hub.refresh()

    def _note_error(self, entry: Entry, e: BaseException) -> tuple[int, str]:
        hub = self.hub
        message = f"{type(e).__name__}: {e}"[:500]
        count = hub.errors.get(entry.id, (0, ""))[0] + 1
        hub.errors[entry.id] = (count, message)
        hub.last_error = message
        hub.error_seq += 1
        hub.notify()
        return count, message

    def _attempt_failed(self, entry: Entry, e: BaseException) -> None:
        """A failure that belongs to the entry: `A`, and the third time `X` (the text stays in the journal)."""
        _, message = self._note_error(entry, e)
        log.warning("filing entry %s failed: %s", entry.id, message)
        last = entry.attempts + 1 >= ATTEMPTS  # before the `A`: another thread's refresh would count it
        try:
            self.hub.journal.append(J.T_ATTEMPT, {"id": entry.id, "error": message})
            if last:
                self.hub.journal.append(J.T_ABANDON, {"id": entry.id, "error": message})
        except OSError as w:
            self._retry(entry, w)
            return
        self.hub.refresh()

    def _retry(self, entry: Entry, e: BaseException, *, quick: bool = False) -> None:
        """A failure that may pass (a model, the network, a cap, a full disk): the entry stays first, and is tried again after a delay
        that grows, which `drain` cuts short."""
        count, message = self._note_error(entry, e)
        log.warning("filing entry %s failed, to be tried again: %s", entry.id, message)
        self.state = "error"
        delay = 0.05 if quick else min(RETRY_LAST, RETRY_FIRST * 2 ** (count - 1))
        self._sleep(delay)


def carry(hub: Hub, e: Entry, *, notes_gone: bool = False) -> J.Ref:
    """Write an entry again at the tail, as it was (the same id, the same time) and with its state, in ONE record: a copy that is cut is
    whole or it is not there, and the hub takes the copy, text and state, over the original. A tombstone is carried with its whole state.
    `notes_gone` (a purge): the record of a filing whose note is not there any more is carried without its path and title."""
    state: dict[str, Any] = {}
    if e.forget is not None:
        state["forget"] = dict(e.forget)
        if not isinstance(e.forget.get("upto"), int):  # an ordinary reuse: a reader of the copy alone forgets only what was before it
            state["forget"].setdefault("seg", e.seg)  # the segment of the original
    if e.filed is not None:
        filed = e.filed
        note = str(filed.get("note") or "")
        if notes_gone and note and not (hub.root / note.lstrip("/")).exists():
            filed = {k: v for k, v in filed.items() if k not in ("note", "title")}
        state["filed"] = filed
    if e.attempts:
        state["attempts"], state["error"] = e.attempts, e.error or ""
    if e.failed is not None:
        state["failed"] = e.failed
    return hub.journal.append_entry(
        e.text, key=e.key, hash=e.hash, title=e.title or None, origin=e.origin, fields=e.fields, author=e.author, at=e.at,
        event_at=e.event_at, id=e.id, state=state or None, sync=False,
    )


def reuse(hub: Hub, number: int, *, notes_gone: bool = False) -> bool:
    """Reuse one segment: what in it is not done (open, given up, a tombstone not complete) is copied forward with its state, made
    durable, and only then is the segment zeroed. Each entry is looked at again just before its copy, on what the process knows: the
    journal is read once for the lot, again after the first copy, and before a copy when another writer moved the shared tail or the last
    copy did not start where the one before it ended; an entry that a tombstone forgot meanwhile is not copied. A tombstone that is not
    seen in time (written between the look and the copy, or by a writer that does not share the hint: no map, another Windows session,
    another `/dev/shm`) lets a copy through after it: any reader of the journal finds it (a copy of an entry already forgotten), and
    before the segment of the original is zeroed a record on the tombstone names it, so that every reader forgets it, also when the
    process that made the copy died before it named it. False when the guard holds it back (what it carries would be over half a
    segment: `status` and `check` say so) or the journal refuses it (a fault)."""
    j = hub.journal
    hub.refresh()
    carried = [e for e in hub.entries.values() if e.seg == number and not e.done and not e.forgotten]
    size = sum(e.cost for e in carried)  # the bytes of the records, text and metadata
    if size > j.segment_size // 2:
        # what is carried would fill the segments again, and the next compaction would carry it again: not now. Nothing is reused behind
        # this segment either (the oldest goes first), so it is said: `status` and `check` show it.
        hub.compact_stopped = (number, size)
        return False
    last, end, stale = None, hub.pos, False
    for e in carried:
        # reading the journal after each of its own copies cost a few ms an entry, seconds for a backlog of hundreds; `end` is where the
        # next copy starts if nobody else writes: where the read stopped, or where the last copy ended
        if stale or (last is not None and j.written_after(last)):
            hub.refresh()
            end, stale = hub.pos, False
        if e.seg == number and not e.done and not e.forgotten:
            last = carry(hub, e, notes_gone=notes_gone)
            stale = (last.seg, last.off) != end
            end = (last.seg, last.end)
    if last is not None:
        j.sync(last)
        hub.refresh()
    # the copies let through whose original is in this segment, made now or by a process that died before it named them: the journal
    # says which (the hub marks a copy of an entry already forgotten), so a crash between the copies and this record loses nothing
    let_through = [e for e in hub.let_through.values() if e.seg <= number]
    named = None
    for tomb in hub.forgets() if let_through else ():
        kh = tomb.forget.get("kh") if tomb.forget is not None else None
        ids = [e.id for e in let_through if key_hash(e.key) == kh]
        if ids:
            named = j.append(J.T_FILED, {"id": tomb.id, "forgot": ids}, sync=False)
    if named is not None:
        j.sync(named)
        hub.refresh()
    return j.recycle(number)


def close_forget(hub: Hub, tomb: Entry, *, complete: bool = True) -> bool:
    """Complete the purge of a tombstone: seal the segment in use, reuse every segment before the next one, then `O`. The caller holds
    the lease. The number of the next segment (`upto`) is made durable first, in a copy of the tombstone, so a purge cut half way and
    taken up again reuses up to the same segment and seals nothing new. False when the guard of the compaction held it back.
    `complete` false: something outside the journal still holds the text (the index, while a reader holds its WAL): the segments are
    reused all the same, and `O` waits for the filer's next look."""
    j = hub.journal
    assert tomb.forget is not None
    j.finish_recycling()  # a reuse that a crash cut half way: its segment is no longer listed, and its old bytes are still there
    upto = tomb.forget.get("upto")
    if not isinstance(upto, int):
        upto = tomb.forget["upto"] = j.seal()
        j.sync(carry(hub, tomb))
        hub.refresh()
    hub.compact_stopped = None
    for number, _path, _header in j.segments():
        if number < upto and not reuse(hub, number, notes_gone=True):
            return False
    if complete:
        j.append(J.T_CLEANED, {"id": tomb.id})
        hub.refresh()
    return True


@dataclass
class Filing:
    """What the filing of one entry needs to tell the journal."""

    entry: Entry
    hub: Hub
    t0: float = field(default_factory=time.perf_counter)

    def put(self, record: dict[str, Any]) -> None:
        """The `F` record, durable when this returns."""
        self.hub.journal.append(J.T_FILED, {"id": self.entry.id, **record})

    def check(self) -> None:
        """Inside the section that files the entry, before it writes: `Forgotten` if a purge forgot the entry meanwhile."""
        self.hub.refresh()
        e = self.hub.entries.get(self.entry.id)
        if e is not None and e.forgotten:
            raise Forgotten(f"entry {self.entry.id} was forgotten while it was being filed")


def frecord(result: IngestResult, filing: Filing, related: list[str]) -> dict[str, Any]:
    """The body of the `F` record: what `Receipt.wait()` returns, and what a process that recovers the section writes. `related` are the
    ids of the notes it links to, never their titles or paths: what a purge takes out of the wiki is not left in the records of others."""
    record: dict[str, Any] = {
        "action": result.action,
        "note": result.note,
        "folder": result.folder,
        "title": result.title,
        "related": related,
        "created_folders": list(result.created_folders),
        "deviations": [{"step": d.step, "decider": d.decider, "choice": d.choice} for d in result.decisions if d.fallback],
        "ms": int((time.perf_counter() - filing.t0) * 1000),
    }
    if result.overflow:
        record["overflow"] = dict(result.overflow)
    if filing.entry.event_at:
        record["event_at"] = filing.entry.event_at
    return record


def _lock_free(root: Path) -> bool:
    from llmw2.bundle.store import WikiStore

    with contextlib.suppress(Exception):
        return WikiStore(root, actor="status").lock_is_free()
    return False


def status(hub: Hub, root: Path) -> dict[str, Any]:
    """`Wiki.status`: the queue and the filer, from what the process has read of the journal and a few cheap looks (a directory
    listing, a stat of the segments, the free space, one read of 64 bytes for the undo log). Never `check()`, never the append lock."""
    hub.refresh()
    open_ = hub.open_entries()
    f = hub.filer
    warnings: list[str] = []
    if f is not None and f.is_alive() and not f.stopping:
        state = "running" if hub.filing else ("error" if f.state == "error" else "idle")
    elif open_ and not hub.nobody_files():
        state = "other"  # another process holds the lease
    else:
        state = "none"
    oldest: float | None = None
    if open_:
        with contextlib.suppress(ValueError):
            oldest = max(0.0, round((datetime.now(UTC) - datetime.fromisoformat(open_[0].at)).total_seconds(), 1))
    size = 0
    journal_dir = root / J.JOURNAL_DIR
    with contextlib.suppress(OSError), os.scandir(journal_dir) as it:
        for e in it:
            if e.name.endswith(".wal"):
                with contextlib.suppress(OSError):
                    size += e.stat().st_size
            elif e.name.endswith(J.QUARANTINE):
                warnings.append(f"journal file {e.name} was kept aside (its header is not valid): look at it with check()")
    if hub.journal.stuck is not None:
        seg, at = hub.journal.stuck
        warnings.append(f"the journal is not read past byte {at} of segment {seg} (a record being written, or a fault): see check()")
    if txn.pending(root) and _lock_free(root):  # a live section has its log too: only with the lock free is it an orphan
        warnings.append("an interrupted filing is waiting to be undone: opening the wiki for writing restores it")
    if state == "none" and open_:
        warnings.append(f"{len(open_)} entries are queued and no filer is running: drain() starts one")
    if state == "error" and hub.last_error:
        warnings.append(f"the filer failed and will try again: {hub.last_error}")
    if oldest is not None and oldest > 300:
        warnings.append(f"the oldest entry has waited {int(oldest)} s")
    failed = hub.failed_count()
    if failed:
        what = (
            "remember now refuses new entries (FailedBacklog); there is no call yet that clears them"
            if failed >= QUEUE_LIMIT
            else "at that many remember refuses new entries; there is no call yet that clears them"
        )
        warnings.append(f"{failed} entries given up (text kept in the journal) fill {failed} of {QUEUE_LIMIT} places of the queue: {what}")
    if forgets := len(hub.forgets()):
        warnings.append(f"{forgets} purges are not complete in the journal yet: the process that holds the lease finishes them")
    if hub.compact_stopped is not None:
        seg, size = hub.compact_stopped
        warnings.append(
            f"the compaction is stopped: the oldest segment ({seg}) holds {size} bytes of entries not done, over half a segment; no "
            "segment is reused behind it and the journal grows meanwhile"
        )
    free = hub.disk_free()
    if free < DISK_RESERVE:
        warnings.append(f"less than {DISK_RESERVE >> 20} MiB free on the disk of the wiki: new entries are refused")
    return {
        "queue": len(open_),
        "queue_limit": QUEUE_LIMIT,
        "filing": hub.filing,
        "failed": failed,
        "oldest_s": oldest,
        "filer": state,
        "last_error": hub.last_error,
        "journal_bytes": size,
        "disk_free": free if free < (1 << 62) else 0,
        "warnings": warnings,
    }
