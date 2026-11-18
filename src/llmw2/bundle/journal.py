# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The journal: what `remember` keeps the moment it is asked, in segments that are written once and then reused.

A segment is a file of `SEGMENT_SIZE` bytes in `.llmw2/journal/`, filled with zeros that were really written (not made with
`fallocate`), so that an append is one write inside blocks that exist, and `fdatasync` has no size or block map to record: one
device block per ack. The first 64 bytes are the header: `magic`, the format version `v`, the number of the segment in the
journal, a `salt` (the generation: new each time the segment is reused), the id of the node, and a CRC of its own. What follows
is a chain of records, each 8-byte aligned:

    len u32 | crc u32 | seq u64 | synced u64 | type u8 | flags u8 | reserved u16 | reserved u32 | body (len bytes)

The CRC covers the salt of the segment, the head and the body: a record of an earlier generation of the segment is never taken
for a new one, and a record is whole or it is nothing. There is no chain of CRCs: each record stands on its own and is never rewritten
(compaction copies forward, see `Filer._compact`). `seq` counts the records of the segment from 1; `synced` is the offset of the
segment that the writer knew to be durable when it wrote the record. A body is `meta_len u32`, the metadata (JSON) and the text.

Types: `E` an entry (the text is in its own field of this record and nowhere else), or the tombstone of a purge (an `E` with no text,
key or title: only the hash of the purged key, see `Wiki.purge`); `Z` the seal of a segment; `F`, `D`, `A`, `X` carry the progress of
the filing (written by the filer, an opaque JSON each); `O` says that the purge of a tombstone is complete.
Reserved and not implemented: `I` (images) and `S` (a section of several entries).

Reading stops at the first record that is not valid, it never skips one. Two things can follow such a record. After a crash with
no fault, whatever was written and not yet flushed is a torn tail, and a writer goes on after the last valid record. A record not
valid *before* a `synced` limit that a valid record after it declares is a fault: the bytes were durable and are not whole.
`check` reports it, and nothing is appended to that segment (a new one is opened). A record is never rewritten, so a reader has no
exception to make: the first record that is not valid is where it stops.

Appends are made under `append.lock`, at an offset that a process keeps in memory, moves only after the write, and checks against
the segment each time it takes the lock (a record of somebody else at the tail is followed). Nothing is written for the offset.
The ack is `fdatasync` (`os.fsync` on Windows) of the segment and nothing else; processes that wait for the same flush share it
(group commit): a process that is not covered tries the lock of the flush, and the one that gets it flushes for every record
written so far. Where the processes can share a few bytes that never reach the disk (`/dev/shm`, a named mapping on Windows),
the end of the last flush is published there as a hint, together with the salt of the segment, and a process the hint covers
does not flush; where they cannot, each flushes in its turn and a flush with nothing dirty costs next to nothing. The hint is
only ever a hint: without a valid answer a process does its own flush.

A segment is reused (`recycle`) only from the oldest one, after the entries it holds that are not done (open, or given up) were
copied forward, with their state, to the tail. The rule keeps one invariant: a closure (`D`, `X`) is never in an older segment than the
record `E` of its entry, so the `E` goes first and a reader never sees a closure lose its entry's closing.
Reusing writes zeros over the part of the segment that was used (once per fill, as many bytes as the segment held, not the whole
segment again), so that what was in it is gone, and a segment is zeros after its tail whatever its history. A purge is built on
this: its tombstone forgets the entries of its key, the segment in use is sealed (`seal`), and every segment before the next one is
reused, its entries that are not done copied forward first; then `O`.
The next segment is made ahead of time, off the ack.
Out of scope: a process that lives through the loss of the page cache (a power cut that takes what was only there, which a real cut
does to every process at once, and a test harness such as LazyFS does not). Its reader (`read_from`) checks the last record it read
(seq, length and CRC) and starts again if it is not there, as a defence; its writer (`_catch_up`) does not, and writes after a hole.
Whoever simulates a cut restarts every process on the bundle. In scope: a backup put back while every process is off (a flush hint
that outlived the processes is checked against the tail when a process attaches, see `_attach`).
Versions: every version known is read, only the newest is written and only in a new segment; a journal with a newer `v` than
this code knows is not written and gives `JournalVersionError`.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import contextlib
import hashlib
import json
import mmap
import os
import secrets
import struct
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from llmw2.bundle.filelock import FileLock
from llmw2.bundle.origin import format_at
from llmw2.errors import WikiError

SEGMENT_SIZE = 16 << 20  # a constant, not a setting
JOURNAL_DIR = ".llmw2/journal"
MAGIC = b"LLMWJRN1"
VERSION = 1  # the format this code writes
KNOWN_VERSIONS = (1,)  # the formats it reads
HEADER_SIZE = 64
_HEADER = struct.Struct("<8sHHQQ16sI")  # magic, v, flags, number, salt, node, crc of the 44 bytes before it
_HEAD = struct.Struct("<IIQQBBHI")  # len, crc, seq, synced, type, flags, reserved, reserved
HEAD_SIZE = _HEAD.size  # 32
ALIGN = 8
SEAL_RESERVE = 64  # the last bytes of a segment are for the seal: a record never takes them
ZERO_CHUNK = 1 << 20
POLL_S = 0.0003  # a process not covered by a flush looks again after this long
# Segments waiting to be reused beyond which `check` says the compaction seems stopped. The compaction reuses one segment for each entry
# the filer closes, and a segment is sealed only when it fills: in step, at most one or two wait (the one just sealed, and one the
# last call has not reached). Four is twice that, a margin and not a measurement; the message says "seems".
COMPACT_STALL_SEGMENTS = 4
FLAG_RECYCLING = 1  # in the header of a segment: `recycle` began and the zeroing is not finished (`prepare` finishes it)
SCAN_CHUNK = 256 << 10  # a process that opens a journal reads this much of the segment first, and more only if its records go on
HINT_SIZE = 64
_HINT = struct.Struct("<QQQ")
_HINT_MASK = 0x9E3779B97F4A7C15
_TAIL = struct.Struct("<QQQQQ")  # salt, tail offset, tail seq, offset of the last record, check: a second hint, at byte 24
_BINARY = getattr(os, "O_BINARY", 0)
_FLUSH = getattr(os, "fdatasync", os.fsync)  # Windows: FlushFileBuffers on a handle open for writing

T_ENTRY, T_SEAL = ord("E"), ord("Z")
T_FILED, T_DONE, T_ATTEMPT, T_ABANDON = ord("F"), ord("D"), ord("A"), ord("X")
T_CLEANED = ord("O")  # the purge of a tombstone is complete: the segments that held what it forgot are reused
T_UNDO = ord("U")  # a record of the undo log of a section (`llmw2.bundle.txn`), which uses this format for its file
T_RESERVED = (ord("I"), ord("S"))  # images, a section of several entries: not implemented
KNOWN_TYPES = (T_ENTRY, T_SEAL, T_FILED, T_DONE, T_ATTEMPT, T_ABANDON, T_CLEANED, T_UNDO)
QUARANTINE = ".quarantine"  # a segment file with a header that is not valid, kept aside under this suffix (see `check`)


class JournalError(WikiError):
    """The journal cannot be used as asked."""


class JournalVersionError(JournalError):
    """The journal was written by a newer version of llmw2: this one does not write it."""


@dataclass(frozen=True)
class Header:
    version: int
    number: int  # 0: a free segment (all its records are filed, it waits to be used again)
    salt: int
    node: str
    flags: int = 0  # FLAG_RECYCLING: the segment is being zeroed to be used again; what it holds is dead, and not yet gone

    def pack(self) -> bytes:
        node = self.node.encode("ascii")[:16].ljust(16, b"\0")
        body = _HEADER.pack(MAGIC, self.version, self.flags, self.number, self.salt, node, 0)[:44]
        return (body + struct.pack("<I", zlib.crc32(body))).ljust(HEADER_SIZE, b"\0")


def parse_header(data: bytes) -> Header | None:
    if len(data) < HEADER_SIZE:
        return None
    magic, version, flags, number, salt, node, crc = _HEADER.unpack_from(data, 0)
    if magic != MAGIC or crc != zlib.crc32(data[:44]):
        return None
    return Header(version, number, salt, node.rstrip(b"\0").decode("ascii", "replace"), flags)


@dataclass
class Record:
    seg: int  # number of the segment
    off: int
    size: int  # on disk, aligned
    length: int  # of the body
    seq: int
    synced: int
    type: int
    flags: int
    meta: dict[str, Any]
    text: bytes
    salt: int = 0
    crc: int = 0

    @property
    def kind(self) -> str:
        return chr(self.type)

    @property
    def end(self) -> int:
        return self.off + self.size


@dataclass(frozen=True)
class Ref:
    """Where a record was written, and the identity of the segment it is in."""

    seg: int
    salt: int
    off: int
    seq: int
    end: int
    id: str = ""


@dataclass
class SegmentView:
    path: Path
    header: Header
    size: int
    records: list[Record] = field(default_factory=list)
    end: int = HEADER_SIZE  # after the last valid record
    next_seq: int = 1
    synced: int = 0  # the greatest limit a valid record declares
    sealed: bool = False
    invalid_at: int | None = None  # the first record that is not valid, if any (a torn tail or a fault)
    fault: bool = False  # a valid record after it declares a durable limit beyond it

    @property
    def number(self) -> int:
        return self.header.number


@dataclass
class Problem:
    severity: Literal["error", "warning"]
    text: str


@dataclass
class View:
    segments: list[SegmentView]
    problems: list[Problem]

    @property
    def records(self) -> list[Record]:
        return [r for s in self.segments for r in s.records]

    def entries(self) -> list[Record]:
        """The entries (`E`), in the order of the journal."""
        return [r for r in self.records if r.type == T_ENTRY]


def entry_meta(
    ident: str,
    key: str,
    hash: str,
    title: str | None,
    origin: dict[str, Any] | None,
    fields: dict[str, Any] | None,
    author: str | None,
    at: str | None,
    event_at: str | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    """The metadata of the record of an entry: the one place that makes it, so that the size an admission counts is the size written."""
    meta: dict[str, Any] = {
        "id": ident,
        "key": key,
        "hash": hash,
        "title": title or "",
        "origin": origin,
        "fields": fields or {},
        "author": author or "",
        "at": at or format_at(datetime.now(UTC)),
    }
    if event_at:
        meta["event_at"] = event_at
    if state:
        meta["state"] = state
    return meta


def entry_size(text: str, **fields: Any) -> int:
    """The bytes the record of an entry takes in a segment (head, metadata and text, aligned), for the `fields` of `entry_meta` (all but
    the id, with a placeholder for it)."""
    meta = entry_meta(fields.pop("ident", "0" * 16), fields.pop("key", ""), fields.pop("hash", ""), fields.pop("title", None),
                      fields.pop("origin", None), fields.pop("fields", None), fields.pop("author", None), fields.pop("at", None),
                      fields.pop("event_at", None), fields.pop("state", None))
    return _align(HEAD_SIZE + len(encode_body(meta, text.encode("utf-8", errors="surrogatepass"))))


# -- the encoding ---------------------------------------------------------------------------------------------------------


def _align(n: int) -> int:
    return (n + ALIGN - 1) & ~(ALIGN - 1)


def encode_body(meta: dict[str, Any], text: bytes = b"") -> bytes:
    raw = json.dumps(meta, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(raw)) + raw + text


def _decode_body(body: bytes) -> tuple[dict[str, Any], bytes] | None:
    if len(body) < 4:
        return None
    (n,) = struct.unpack_from("<I", body, 0)
    if 4 + n > len(body):
        return None
    try:
        meta = json.loads(body[4 : 4 + n].decode("utf-8"))
    except ValueError:
        return None
    return (meta, body[4 + n :]) if isinstance(meta, dict) else None


def _crc(salt: int, head_without_crc: bytes, body: bytes) -> int:
    return zlib.crc32(body, zlib.crc32(head_without_crc, zlib.crc32(struct.pack("<Q", salt)))) & 0xFFFFFFFF


def pack_record(salt: int, seq: int, synced: int, rtype: int, flags: int, body: bytes) -> bytes:
    head = _HEAD.pack(len(body), 0, seq, synced, rtype, flags, 0, 0)
    crc = _crc(salt, head, body)
    head = _HEAD.pack(len(body), crc, seq, synced, rtype, flags, 0, 0)
    return (head + body).ljust(_align(HEAD_SIZE + len(body)), b"\0")


def _parse_at(data: bytes | memoryview, off: int, salt: int, *, limit: int | None = None) -> tuple[tuple[int, ...], bytes] | None:
    """The head and the body of the valid record at `off` (under `salt`), or None."""
    end = len(data) if limit is None else min(limit, len(data))
    if off < 0 or off + HEAD_SIZE > end:
        return None
    length, crc, seq, synced, rtype, flags, _r1, _r2 = _HEAD.unpack_from(data, off)
    if length < 6 or off + HEAD_SIZE + length > end or seq == 0:
        return None
    body = bytes(data[off + HEAD_SIZE : off + HEAD_SIZE + length])
    if _crc(salt, _HEAD.pack(length, 0, seq, synced, rtype, flags, 0, 0), body) != crc:
        return None
    return (length, crc, seq, synced, rtype, flags), body


def _record_from(seg: int, salt: int, off: int, head: tuple[int, ...], body: bytes) -> Record | None:
    length, crc, seq, synced, rtype, flags = head
    if rtype in T_RESERVED or rtype not in KNOWN_TYPES:
        meta, text = {}, body
    else:
        decoded = _decode_body(body)
        if decoded is None:
            return None
        meta, text = decoded
    return Record(seg, off, _align(HEAD_SIZE + length), length, seq, synced, rtype, flags, meta, text, salt, crc)


def scan_segment(data: bytes, path: Path, header: Header) -> SegmentView:
    """The valid records of a segment, from the first to the first that is not valid (see the module's notes).

    Nothing is stepped over: a record is never rewritten."""
    view = SegmentView(path, header, len(data))
    off, seq = HEADER_SIZE, 1
    while off + HEAD_SIZE <= len(data):
        parsed = _parse_at(data, off, header.salt)
        if parsed is not None and parsed[0][2] == seq:
            record = _record_from(header.number, header.salt, off, parsed[0], parsed[1])
            if record is not None:
                view.records.append(record)
                view.synced = max(view.synced, record.synced)
                off, seq = off + record.size, seq + 1
                if record.type == T_SEAL:
                    view.sealed = True
                    break
                continue
        if not any(data[off : off + HEAD_SIZE]):  # zeros: the end of what was written
            break
        fault, _found = _look_after(data, off, seq, header.salt)
        view.invalid_at, view.fault = off, fault
        break
    view.end, view.next_seq = off, seq
    return view


def _look_after(data: bytes, start: int, seq: int, salt: int, base: int = 0) -> tuple[bool, bool]:
    """A record is not valid at `start`. Looks for the valid records after it, to the end of what was written (a record is 8-byte
    aligned, and the writer went on after it): whether one declares a flushed limit beyond `start` (a fault: those bytes were
    durable), and whether there is any valid record after it. `data` is the segment from byte `base` (offsets are the segment's)."""
    last = len(data.rstrip(b"\0"))
    off = start - base + ALIGN
    while off + HEAD_SIZE <= last:
        length = int.from_bytes(data[off : off + 4], "little")
        if length >= 6 and off + HEAD_SIZE + length <= len(data):
            parsed = _parse_at(data, off, salt)
            if parsed is not None and parsed[0][2] >= seq:
                synced = 0
                while parsed is not None:  # follow the chain from here: the limits only grow
                    synced = max(synced, parsed[0][3])
                    off += _align(HEAD_SIZE + parsed[0][0])
                    parsed = _parse_at(data, off, salt)
                return synced > start, True
        off += ALIGN
    return False, False


# -- the hint shared by the processes --------------------------------------------------------------------------------------


class _Hint:
    """A few bytes that processes share and that never reach the disk: the salt of a segment and the offset the last flush covered.

    `/dev/shm` on POSIX, a named mapping on Windows (a process of another session does not see it, and nothing is lost: it
    flushes for itself). Not `multiprocessing.shared_memory`, whose resource tracker would delete it under Python 3.11-3.12.

    Two hints live here, the flush offset (`get`/`put`) and the tail (`get_tail`/`put_tail`). Both are accelerators, never
    the truth, and both have two limits that are known and not closed:
    - the tail hint skips the stretch between two records on the word of ONE record (its CRC, number and end), so a copy of
      the journal that diverged under the same salt (a synchronizer, a file put back by hand) can pass that check;
    - on POSIX `/dev/shm` outlives the processes: a backup restored while every process is off leaves a hint that is ahead
      of, or different from, the file. The tail hint refuses what it can check (the last record must be whole and in its
      place); the flush hint is only a durability shortcut (`_covered` may skip an fsync) and has no such check.
    Restore a backup of a journal with the machine's `/dev/shm/llmw2-j-*` files removed."""

    def __init__(self, journal_dir: Path) -> None:
        self.mm: mmap.mmap | None = None
        name = "llmw2-j-" + hashlib.sha1(os.path.normcase(os.path.realpath(journal_dir)).encode()).hexdigest()[:20]
        try:
            if sys.platform == "win32":
                self.mm = mmap.mmap(-1, HINT_SIZE, tagname=name)
            else:
                fd = os.open(f"/dev/shm/{name}", os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    if os.fstat(fd).st_size < HINT_SIZE:
                        os.ftruncate(fd, HINT_SIZE)
                    self.mm = mmap.mmap(fd, HINT_SIZE)
                finally:
                    os.close(fd)
        except (OSError, ValueError, AttributeError, OverflowError):
            self.mm = None

    @property
    def available(self) -> bool:
        return self.mm is not None

    def get(self, salt: int) -> int | None:
        if self.mm is None:
            return None
        try:
            got, off, chk = _HINT.unpack_from(self.mm, 0)
        except (ValueError, struct.error):
            return None
        if got != salt or chk != (got ^ off ^ _HINT_MASK):
            return None
        return off

    def put(self, salt: int, off: int) -> None:
        if self.mm is None:
            return
        have = self.get(salt)
        if have is not None and have >= off:
            return
        with contextlib.suppress(ValueError):
            self.mm[0:24] = _HINT.pack(salt, off, salt ^ off ^ _HINT_MASK)

    def reset(self, salt: int, off: int) -> None:
        """The flush hint says what is not true of this file (a backup was put back while no process ran: the hint outlived them): it is
        written again, lower, whatever it held."""
        if self.mm is None:
            return
        with contextlib.suppress(ValueError):
            self.mm[0:24] = _HINT.pack(salt, off, salt ^ off ^ _HINT_MASK)

    def get_tail(self, salt: int) -> tuple[int, int, int] | None:
        """(tail offset, tail seq, offset of the last record) as the last writer left them: an accelerator, never the truth (a process
        with no hint writes too, and the file can change under it). The reader checks the last record before it believes the rest."""
        if self.mm is None:
            return None
        try:
            got, off, seq, prev, chk = _TAIL.unpack_from(self.mm, 24)
        except (ValueError, struct.error):
            return None
        if got != salt or chk != (got ^ off ^ seq ^ prev ^ _HINT_MASK):
            return None
        return off, seq, prev

    def put_tail(self, salt: int, off: int, seq: int, prev: int) -> None:
        """Always the last writer's word: it holds the append lock and has caught up, so what it wrote is the tail, even when the hint it
        found was ahead of the file (a hint that kept a larger number would keep a wrong one)."""
        if self.mm is None:
            return
        with contextlib.suppress(ValueError):
            self.mm[24 : 24 + _TAIL.size] = _TAIL.pack(salt, off, seq, prev, salt ^ off ^ seq ^ prev ^ _HINT_MASK)

    def close(self) -> None:
        if self.mm is not None:
            with contextlib.suppress(Exception):
                self.mm.close()
            self.mm = None


# -- the journal -----------------------------------------------------------------------------------------------------------

_IO = threading.Lock()  # where there is no pread: seek and read on a shared descriptor must not interleave


def _pread(fd: int, n: int, off: int) -> bytes:
    if hasattr(os, "pread"):
        return os.pread(fd, n, off)
    with _IO:
        os.lseek(fd, off, os.SEEK_SET)
        return os.read(fd, n)


def _pread_path(path: Path, n: int, off: int = 0) -> bytes:
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(n)
    except OSError:
        return b""


def _pwrite(fd: int, data: bytes, off: int) -> None:
    view = memoryview(data)
    if hasattr(os, "pwrite"):
        while view:
            n = os.pwrite(fd, view, off)
            view, off = view[n:], off + n
        return
    with _IO:
        os.lseek(fd, off, os.SEEK_SET)
        while view:
            view = view[os.write(fd, view) :]


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":
        return  # no way to flush a folder from Python: NTFS records its metadata itself
    fd = os.open(path, os.O_RDONLY)
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def _zero_used(path: Path) -> None:
    """The bytes a segment used are zeroed and flushed (a segment is zeros after its
    tail whatever its history). Only the used part is written: as much as the segment held, once per fill. The header is not
    touched: the segment stays what it was until `_set_header`."""
    fd = os.open(path, os.O_RDWR | _BINARY)
    try:
        used = len(path.read_bytes().rstrip(b"\0"))
        if used > HEADER_SIZE:
            for start in range(HEADER_SIZE, used, ZERO_CHUNK):
                _pwrite(fd, bytes(min(ZERO_CHUNK, used - start)), start)
            os.fsync(fd)
    finally:
        os.close(fd)


def _set_header(path: Path, header: Header) -> None:
    fd = os.open(path, os.O_RDWR | _BINARY)
    try:
        _pwrite(fd, header.pack(), 0)
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class _Attached:
    path: Path
    fd: int
    number: int
    salt: int
    size: int
    version: int
    off: int  # where the next record goes
    seq: int  # its number


class Journal:
    """The journal of one bundle. Each process makes its own; they share the files and the locks."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        node: str | None = None,
        shared_hint: bool = True,
        segment_size: int = SEGMENT_SIZE,
        writable: bool = True,
    ) -> None:
        self.root = Path(root)
        self.dir = self.root / JOURNAL_DIR
        self.writable = writable
        self.segment_size = segment_size
        self._node = node
        self._local = threading.local()  # a lock object belongs to one thread; the threads of a process exclude each other anyway
        self._hint: _Hint | None = _Hint(self.dir) if shared_hint else None
        self._act: _Attached | None = None
        self._fd_guard = threading.Lock()  # a rotation does not close the segment under a flush
        self._synced = (0, 0)  # (salt, offset): what this process knows to be flushed
        self._preparer: threading.Thread | None = None
        self._first = threading.Event()  # the first segment of the bundle exists
        self._first.set()
        self._closed = False
        self.syncs = 0  # flushes made by this process
        self.restarted = False  # the last `read_from` found another journal than the one its position was in, and read it from its start
        self._last: tuple[int, int, int, int, int] | None = None  # (segment, offset, seq, end, crc) of the last record `read_from` read
        self._pos_salt: int | None = None  # the salt of the segment the last `read_from` ended in
        self.stuck: tuple[int, int] | None = None  # where the last `read_from` stopped at bytes that are not a valid record
        self.rotations = 0

    # -- files ------------------------------------------------------------------------------------------------------------

    def _flock(self, name: str) -> FileLock:
        locks: dict[str, FileLock] = self._local.__dict__.setdefault("locks", {})
        if name not in locks:
            locks[name] = FileLock(self.dir / name, keep_open=True)  # tried many times a second: its file stays open
        return locks[name]

    def _headers(self) -> list[tuple[Path, Header | None]]:
        out: list[tuple[Path, Header | None]] = []
        try:
            with os.scandir(self.dir) as it:
                names = sorted(Path(e.path) for e in it if e.name.endswith(".wal") and e.is_file())
        except FileNotFoundError:
            return out
        for path in names:
            try:
                with open(path, "rb") as f:
                    out.append((path, parse_header(f.read(HEADER_SIZE))))
            except OSError:
                continue
        return out

    @staticmethod
    def _live(headers: list[tuple[Path, Header | None]]) -> list[tuple[Header, Path]]:
        return sorted(
            ((h, p) for p, h in headers if h is not None and h.number > 0 and not h.flags & FLAG_RECYCLING), key=lambda hp: hp[0].number
        )

    def node_id(self) -> str:
        if self._node is None:
            path = self.dir / "node"
            try:
                self._node = path.read_text(encoding="ascii").strip()[:16]
            except OSError:
                self._node = secrets.token_hex(8)
                with contextlib.suppress(OSError):
                    self.dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(self._node, encoding="ascii")
        return self._node or "0" * 16

    @staticmethod
    def _check_version(headers: list[tuple[Path, Header | None]]) -> None:
        for _path, h in headers:
            if h is not None and h.version > VERSION:
                raise JournalVersionError(
                    f"the journal of this wiki was written in format {h.version}, newer than the {VERSION} this version of llmw2 "
                    "knows: update llmw2, this version does not write it"
                )

    def _make_segment(self, number: int, size: int, headers: list[tuple[Path, Header | None]]) -> Path:
        """A new segment file: zeros written and flushed, the header last. The `prepare.lock` is held."""
        from llmw2.bundle import txn

        self.dir.mkdir(parents=True, exist_ok=True)
        txn.ensure_journal_dir(self.root)
        for path, h in headers:  # a file with no valid header: a creation that was cut (zeros only: it goes), or damage (kept aside)
            if h is None:
                self._set_aside(path)
        taken = [int(p.stem) for p, _ in headers if p.stem.isdigit()]
        path = self.dir / f"{(max(taken, default=0) + 1):06d}.wal"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _BINARY)
        try:
            zeros = bytes(min(ZERO_CHUNK, size))
            written = 0
            while written < size:
                n = min(len(zeros), size - written)
                os.write(fd, zeros if n == len(zeros) else zeros[:n])
                written += n
            os.fsync(fd)
            _pwrite(fd, Header(VERSION, number, secrets.randbits(64), self.node_id()).pack(), 0)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(self.dir)
        return path

    def _set_aside(self, path: Path) -> None:
        """A `.wal` file whose header is not valid. All zeros: a creation cut before the header, nothing in it, it goes. Anything
        else may hold entries that were acknowledged: it is renamed (`QUARANTINE`), never deleted, and `check` reports it."""
        try:
            empty = True
            with open(path, "rb") as f:
                while chunk := f.read(ZERO_CHUNK):
                    if any(chunk):
                        empty = False
                        break
            if empty:
                path.unlink()
                return
            target = path.with_suffix(QUARANTINE)
            n = 0
            while target.exists():
                n += 1
                target = path.with_name(f"{path.stem}-{n}{QUARANTINE}")
            path.rename(target)
            _fsync_dir(self.dir)
        except OSError:
            pass  # still there under its name: the next `prepare` looks at it again

    def _claim_or_make(self, headers: list[tuple[Path, Header | None]], number: int) -> None:
        """The prepare lock is held: a free (recycled) segment is numbered and used before a new one is written."""
        for path, h in headers:
            if h is not None and h.number == 0 and h.version == VERSION:
                fd = os.open(path, os.O_RDWR | _BINARY)
                try:
                    _pwrite(fd, Header(VERSION, number, h.salt, self.node_id()).pack(), 0)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return
        self._make_segment(number, self.segment_size, headers)

    def _used_number(self, live: list[tuple[Header, Path]]) -> int:
        """The number of the segment writers use: the last one with a record in it (the ones after it are spares)."""
        best = 0
        for header, path in live:
            try:
                with open(path, "rb") as f:
                    f.seek(HEADER_SIZE)
                    first = f.read(HEAD_SIZE)
            except OSError:
                continue
            if any(first):
                best = header.number
        return best or (live[0][0].number if live else 0)

    def prepare(self) -> None:
        """Make sure the journal has a segment to write and one after it (the first of a bundle, or the next). It takes as long as
        writing the zeros: the callers keep it off the ack (a thread: `prepare_async`, or the rotation)."""
        if not self.writable:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        from llmw2.bundle import txn

        txn.ensure_journal_dir(self.root)
        with self._flock("prepare.lock"):
            self.finish_recycling()
            headers = self._headers()
            self._check_version(headers)
            live = self._live(headers)
            if not live:
                self._claim_or_make(headers, 1)
                self._first.set()
                headers = self._headers()
                live = self._live(headers)
            used = self._used_number(live)
            if not any(h.number > used for h, _p in live):
                self._claim_or_make(headers, 1 + max(h.number for h, _p in live))

    def prepare_async(self) -> threading.Thread:
        """`prepare` in a thread. The first ack waits for the first segment only if it is not made yet."""
        if self._preparer is not None and self._preparer.is_alive():
            return self._preparer
        if not self._live(self._headers()):
            self._first.clear()

        def run() -> None:
            try:
                with contextlib.suppress(Exception):  # the append that needs the segment makes it, and says why it cannot
                    self.prepare()
            finally:
                self._first.set()

        self._preparer = threading.Thread(target=run, name="llmw2-journal-prepare", daemon=True)
        self._preparer.start()
        return self._preparer

    def _kick_spare(self) -> None:
        if self._preparer is not None and self._preparer.is_alive():
            return
        self._preparer = threading.Thread(target=self._prepare_quiet, name="llmw2-journal-spare", daemon=True)
        self._preparer.start()

    def _prepare_quiet(self) -> None:
        with contextlib.suppress(Exception):  # the rotation makes the segment in the ack, and says why it cannot
            self.prepare()

    # -- the tail -----------------------------------------------------------------------------------------------------------

    def _scan(self, path: Path, header: Header) -> SegmentView:
        """A segment as it is."""
        size = path.stat().st_size
        have = min(size, SCAN_CHUNK)
        while True:
            with open(path, "rb") as f:
                data = f.read(have)
            view = scan_segment(data, path, header)
            # a segment is zeros after its tail: what was read is enough when the scan ended inside it (at zeros, with room to see a
            # fault after a record that is not valid); otherwise it ran out of what was read, and more is read
            ran_out = view.invalid_at is None and not view.sealed and view.end + HEAD_SIZE > have
            near = view.invalid_at is not None and any(data[-HEAD_SIZE:])  # what was read ends inside the records: more is read
            if have >= size or not (ran_out or near):
                return view
            have = min(size, have * 4)

    def _attach(self) -> None:
        """Find the segment to write and the offset in it. The append lock is held."""
        self._drop()
        headers = self._headers()
        self._check_version(headers)
        live = self._live(headers)
        if not live:
            if not self._first.is_set():
                self._first.wait(60)
            else:
                with self._flock("prepare.lock"):
                    headers = self._headers()
                    if not self._live(headers):
                        self.dir.mkdir(parents=True, exist_ok=True)
                        self._claim_or_make(headers, 1)
            live = self._live(self._headers())
            if not live:
                raise JournalError("the journal has no segment and none could be made")
        used = self._used_number(live)
        index = next((i for i, (h, _p) in enumerate(live) if h.number == used), 0)
        header, path = live[index]
        view = self._scan(path, header)
        if view.sealed or view.fault or header.version < VERSION or view.end + SEAL_RESERVE >= view.size:
            if index + 1 < len(live):
                header, path = live[index + 1]
            else:
                header, path = self._next_segment(header.number + 1)
            view = self._scan(path, header)
        fd = os.open(path, os.O_RDWR | _BINARY)
        self._act = _Attached(path, fd, header.number, header.salt, os.fstat(fd).st_size, header.version, view.end, view.next_seq)
        self._note_synced(header.salt, max(view.synced, HEADER_SIZE))
        if self._hint is not None:
            # One look, when a process attaches (never in an ack): a flush hint that says more is covered than the segment holds is of a
            # file that was put back from a backup while the processes were off. Left as it is it would let the next acks skip their flush.
            got = self._hint.get(header.salt)
            if got is not None and got > view.end:
                self._hint.reset(header.salt, view.end)
        if index + 1 >= len(live) or header.number == live[-1][0].number:
            self._kick_spare()

    def _next_segment(self, number: int) -> tuple[Header, Path]:
        """A segment to go to when there is no spare: made here, in the ack (the cost that `prepare` keeps away)."""
        with self._flock("prepare.lock"):
            headers = self._headers()
            live = self._live(headers)
            ahead = [(h, p) for h, p in live if h.number >= number]
            if not ahead:
                top = max((h.number for p, h in headers if h is not None), default=0)
                self._claim_or_make(headers, max(number, top + 1))
                ahead = [(h, p) for h, p in self._live(self._headers()) if h.number >= number]
        return ahead[0]

    def _drop(self) -> None:
        with self._fd_guard:
            if self._act is not None:
                with contextlib.suppress(OSError):
                    os.close(self._act.fd)
                self._act = None

    def _note_synced(self, salt: int, off: int) -> None:
        if self._synced[0] != salt or self._synced[1] < off:
            self._synced = (salt, off)

    def _known_synced(self, act: _Attached) -> int:
        best = self._synced[1] if self._synced[0] == act.salt else 0
        if self._hint is not None:
            got = self._hint.get(act.salt)
            if got is not None:
                best = max(best, got)
        return best

    @staticmethod
    def _record_at(fd: int, off: int, size: int, salt: int) -> tuple[tuple[int, ...], bytes] | None:
        """The valid record at `off` of a segment, read with at most two reads; None for zeros and for what is not a record."""
        chunk = _pread(fd, HEAD_SIZE + 4096, off)
        if len(chunk) < HEAD_SIZE or not any(chunk[:HEAD_SIZE]):
            return None
        length = int.from_bytes(chunk[:4], "little")
        if length < 6 or off + HEAD_SIZE + length > size:
            return None
        if HEAD_SIZE + length > len(chunk):
            chunk = _pread(fd, HEAD_SIZE + length, off)
        return _parse_at(chunk, 0, salt)

    def _catch_up(self) -> None:
        """The append lock is held: bring the tail in memory to what the segment holds (somebody else may have written)."""
        act = self._act
        if act is None:
            self._attach()
            return
        head = parse_header(_pread(act.fd, HEADER_SIZE, 0))
        if head is None or head.salt != act.salt or head.number != act.number:
            self._attach()  # the segment was used again under us: look at the journal anew
            return
        if self._hint is not None:
            tail = self._hint.get_tail(act.salt)
            if tail is not None and tail[1] > act.seq and tail[0] > act.off and tail[2] >= act.off:
                # the records between were written by processes that share the hint: not read again. Only the last of them is,
                # with its CRC, its number and its end: a hint that is wrong, or ahead of what the file holds, is not followed.
                last = self._record_at(act.fd, tail[2], act.size, act.salt)
                whole = last is not None and last[0][4] != T_SEAL and tail[2] + _align(HEAD_SIZE + last[0][0]) == tail[0]
                if last is not None and whole and last[0][2] == tail[1] - 1:
                    act.off, act.seq = tail[0], tail[1]
                    self._note_synced(act.salt, last[0][3])
        while True:
            parsed = self._record_at(act.fd, act.off, act.size, act.salt)
            if parsed is None or parsed[0][2] != act.seq:
                return  # not a record of this generation: the tail, or a torn record that is written over
            (length, _c, _seq, synced, rtype, _flags), _body = parsed
            act.off += _align(HEAD_SIZE + length)
            act.seq += 1
            self._note_synced(act.salt, synced)
            if rtype == T_SEAL:
                self._attach()
                return

    def _rotate(self) -> None:
        """The segment in use cannot take the record: seal it and go to the next (the spare, or one made now)."""
        act = self._act
        assert act is not None
        if act.size - act.off >= SEAL_RESERVE:
            seal = pack_record(act.salt, act.seq, self._known_synced(act), T_SEAL, 0, encode_body({}))
            _pwrite(act.fd, seal, act.off)  # not flushed: a seal that is lost leaves a segment that looks open and has no room
        live = self._live(self._headers())
        ahead = [(h, p) for h, p in live if h.number > act.number]
        header, path = ahead[0] if ahead else self._next_segment(act.number + 1)
        self._drop()
        view = self._scan(path, header)
        fd = os.open(path, os.O_RDWR | _BINARY)
        self._act = _Attached(path, fd, header.number, header.salt, os.fstat(fd).st_size, header.version, view.end, view.next_seq)
        self._note_synced(header.salt, HEADER_SIZE)
        self.rotations += 1
        self._kick_spare()

    def written_after(self, ref: Ref) -> bool:
        """Whether a record was appended after `ref`, by this process or another: the tail the shared hint holds, read with no system call.
        True when the hint cannot say (none, another segment). A writer with no hint is not seen: the caller only saves a read."""
        if self._hint is None:
            return True
        tail = self._hint.get_tail(ref.salt)
        return tail is None or tail[0] != ref.end

    def seal(self) -> int:
        """Close the segment in use, so that what is written from now on goes to the next one; the number of that one. A segment with
        no record yet is not sealed: it is the one in use. Everything before the number returned can then be reused (`recycle`)."""
        if not self.writable:
            raise JournalError("this journal was opened to be read")
        with self._flock("append.lock"):
            self._catch_up()
            act = self._act
            assert act is not None
            if act.off > HEADER_SIZE:
                self._rotate()
            assert self._act is not None
            return self._act.number

    def _big_segment(self, need: int) -> None:
        """A record larger than a segment gets a segment of its own, made here."""
        act = self._act
        assert act is not None
        size = ((HEADER_SIZE + need + SEAL_RESERVE + ZERO_CHUNK) // ZERO_CHUNK) * ZERO_CHUNK
        if act.size - act.off >= SEAL_RESERVE:
            _pwrite(act.fd, pack_record(act.salt, act.seq, self._known_synced(act), T_SEAL, 0, encode_body({})), act.off)
        with self._flock("prepare.lock"):
            headers = self._headers()
            top = max((h.number for p, h in headers if h is not None), default=0)
            path = self._make_segment(max(top, act.number) + 1, size, headers)
            header = next(h for p, h in self._headers() if p == path and h is not None)
        self._drop()
        fd = os.open(path, os.O_RDWR | _BINARY)
        self._act = _Attached(path, fd, header.number, header.salt, os.fstat(fd).st_size, header.version, HEADER_SIZE, 1)
        self._note_synced(header.salt, HEADER_SIZE)

    # -- appending ----------------------------------------------------------------------------------------------------------

    def append(self, rtype: int, meta: dict[str, Any], text: bytes = b"", *, sync: bool = True, flags: int = 0) -> Ref:
        """Write a record at the tail and, unless `sync` is false, return when it is on the disk (the ack)."""
        if not self.writable:
            raise JournalError("this journal was opened to be read")
        if rtype not in KNOWN_TYPES:
            raise JournalError(f"record type {chr(rtype)!r} is reserved and is not written")
        body = encode_body(meta, text)
        with self._flock("append.lock"):
            ref = self._write(rtype, flags, body, str(meta.get("id", "")))
        if sync:
            self._sync_until(ref)
        return ref

    def _write(self, rtype: int, flags: int, body: bytes, ident: str) -> Ref:
        """The append lock is held."""
        size = _align(HEAD_SIZE + len(body))
        self._catch_up()
        for _ in range(4):
            act = self._act
            assert act is not None
            if act.version == VERSION and act.off + size + SEAL_RESERVE + HEAD_SIZE <= act.size:
                break
            if HEADER_SIZE + size + SEAL_RESERVE + HEAD_SIZE > self.segment_size and act.version == VERSION:
                self._big_segment(size)
            else:
                self._rotate()  # an older format is not written; and a full segment is left
        act = self._act
        assert act is not None
        if act.off + size + SEAL_RESERVE + HEAD_SIZE > act.size or act.version != VERSION:
            raise JournalError("no segment has room for this record")
        record = pack_record(act.salt, act.seq, self._known_synced(act), rtype, flags, body)
        _pwrite(act.fd, record, act.off)
        ref = Ref(act.number, act.salt, act.off, act.seq, act.off + size, ident)
        act.off += size  # the tail moves only after the write
        act.seq += 1
        if self._hint is not None:
            self._hint.put_tail(act.salt, act.off, act.seq, ref.off)
        return ref

    def append_entry(
        self,
        text: str,
        *,
        key: str = "",
        hash: str = "",
        title: str | None = None,
        origin: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        author: str | None = None,
        at: str | None = None,
        event_at: str | None = None,
        id: str | None = None,
        state: dict[str, Any] | None = None,
        sync: bool = True,
    ) -> Ref:
        """An entry (`E`): what `remember` is asked to keep. The id is 16 hex characters, new each time. `at` is when it was acknowledged
        and `event_at` (only when given) when the thing it tells of happened; both are written by `format_at`, as the raw copy writes
        `event_at`. `state` (only when the compaction copies an entry forward) is what the filer had written about it (`F`, the attempts,
        `X`): the copy is one record, so that a cut in the middle of it cannot leave an entry without its state."""
        ident = id or secrets.token_hex(8)
        meta = entry_meta(ident, key, hash, title, origin, fields, author, at, event_at, state)
        return self.append(T_ENTRY, meta, text.encode("utf-8"), sync=sync)

    # -- the flush (group commit) ------------------------------------------------------------------------------------------

    def _covered(self, salt: int, end: int) -> bool:
        if self._synced[0] == salt and self._synced[1] >= end:
            return True
        if self._hint is not None:
            got = self._hint.get(salt)
            if got is not None and got >= end:
                self._note_synced(salt, got)
                return True
        return False

    def _peek_end(self, ref: Ref) -> int:
        """The end of the valid records of the segment as a read sees it now: all are in the page cache, so a flush that starts
        after this look covers them."""
        end = ref.end
        with self._fd_guard:
            act = self._act
            if act is None or act.salt != ref.salt:
                return end
            off, seq = max(act.off, ref.end), act.seq if act.off >= ref.end else ref.seq + 1
            while True:
                parsed = self._record_at(act.fd, off, act.size, ref.salt)
                if parsed is None or parsed[0][2] != seq or parsed[0][4] == T_SEAL:
                    return max(end, off)
                off += _align(HEAD_SIZE + parsed[0][0])
                seq += 1

    def _flush(self, ref: Ref) -> None:
        with self._fd_guard:
            act = self._act
            if act is not None and act.salt == ref.salt:
                _FLUSH(act.fd)
                return
        path = next((p for p, h in self._headers() if h is not None and h.salt == ref.salt), None)
        if path is None:
            raise JournalError("the segment of the record is gone")
        fd = os.open(path, os.O_RDWR | _BINARY)
        try:
            _FLUSH(fd)
        finally:
            os.close(fd)

    def _sync_until(self, ref: Ref) -> None:
        """Return when the record is flushed: by the flush of a process that was ahead, or by one of ours. A process the hint does not
        cover looks again every `POLL_S` and tries the lock of the flush without waiting for it (a waiter of the kernel is not served in
        order, and a process that comes back at once can keep it from the others): the one that gets it flushes for every record written
        so far. Measured against a ticket for the oldest record (that process first, the others after a grace): no better."""
        while True:
            if self._covered(ref.salt, ref.end):
                return
            if (lock := self._flock("sync.lock")).try_acquire():
                try:
                    if not self._covered(ref.salt, ref.end):
                        cover = self._peek_end(ref)
                        self._flush(ref)
                        self.syncs += 1
                        self._note_synced(ref.salt, cover)
                        if self._hint is not None:
                            self._hint.put(ref.salt, cover)
                finally:
                    lock.release()
                return
            time.sleep(POLL_S)

    def sync(self, ref: Ref) -> None:
        """Make a record written with `sync=False` durable."""
        self._sync_until(ref)

    # -- reading ------------------------------------------------------------------------------------------------------------

    def read(self) -> View:
        """Every valid record of every segment, in order, and what is wrong with the journal (see `check`)."""
        headers = self._headers()
        self._check_version(headers)
        live = self._live(headers)
        datas = {p: p.read_bytes() for _h, p in live}
        views = [scan_segment(datas[p], p, h) for h, p in live]
        problems: list[Problem] = []
        for view in views:
            if view.fault:
                problems.append(
                    Problem(
                        "error",
                        f"journal segment {view.number}: a record at byte {view.invalid_at} is not valid but was flushed "
                        "(a later record says so); nothing more is written to that segment",
                    )
                )
        return View(views, problems)

    def read_from(self, pos: tuple[int, int] | None = None) -> tuple[list[Record], tuple[int, int]]:
        """The valid records written after a position, and the position after them: `(segment number, offset)`, `None` for the
        start. It reads what is new, not the journal: for the filer, the receipts and the status, which come back to it often.

        It stops at the first record that is not valid (a tail being written) and says that position, so the
        next call goes on from there; `check` is what tells a fault from a tail. A segment is left for the next one only when it
        ended at zeros or at its seal and the next one has a record. A position in a segment that was used again goes on from
        the first segment after it (numbers never come back)."""
        headers = self._headers()
        self._check_version(headers)
        live = self._live(headers)
        out: list[Record] = []
        self.stuck = None
        self.restarted = False
        if pos is not None and self._pos_salt is not None:
            there = next((h for h, _p in live if h.number == pos[0]), None)
            if (there is not None and there.salt != self._pos_salt) or (there is None and live and live[-1][0].number < pos[0]):
                pos = None  # the journal is not the one this position was in (the bundle was deleted and made again): from its start
                self.restarted = True
        if pos is not None and self._last is not None and self._last[0] == pos[0] and self._last[3] == pos[1]:
            # the last record this reader read must still be there as it was: a journal that was rolled back (a power cut that took what
            # was only in the page cache) and written again is not the one the position is in, and its records are not read from the middle
            seg, off, seq, _end, crc = self._last
            mine = next(((h, p) for h, p in live if h.number == seg), None)
            ok = False
            if mine is not None:
                with contextlib.suppress(OSError):
                    fd = os.open(mine[1], os.O_RDONLY | _BINARY)
                    try:
                        parsed = self._record_at(fd, off, os.fstat(fd).st_size, mine[0].salt)
                    finally:
                        os.close(fd)
                    ok = (
                        parsed is not None
                        and parsed[0][2] == seq
                        and parsed[0][1] == crc
                        and off + _align(HEAD_SIZE + parsed[0][0]) == pos[1]
                    )
            if not ok:
                pos = None
                self.restarted = True
        seg0, off0 = pos if pos is not None else (0, HEADER_SIZE)
        cur = pos if pos is not None else ((live[0][0].number, HEADER_SIZE) if live else (0, HEADER_SIZE))
        todo = [(h, p) for h, p in live if h.number >= seg0]
        for k, (header, path) in enumerate(todo):
            try:
                fd = os.open(path, os.O_RDONLY | _BINARY)
            except OSError:
                break
            try:
                size = os.fstat(fd).st_size
                off = off0 if header.number == seg0 else HEADER_SIZE
                seq = None if header.number == seg0 and off0 != HEADER_SIZE else 1
                cur = (header.number, off)
                sealed = False
                while True:
                    parsed = self._record_at(fd, off, size, header.salt)
                    record = None
                    if parsed is not None and (seq is None or parsed[0][2] == seq):
                        record = _record_from(header.number, header.salt, off, parsed[0], parsed[1])
                    if record is None:
                        if off + HEAD_SIZE <= size and any(_pread(fd, HEAD_SIZE, off)):
                            self.stuck = (header.number, off)  # a record being written, or a fault: `check` tells which
                        break
                    out.append(record)
                    off, seq = off + record.size, record.seq + 1
                    cur = (header.number, off)
                    if record.type == T_SEAL:
                        sealed = True
                        break
                ended = sealed or off + HEAD_SIZE > size or not any(_pread(fd, HEAD_SIZE, off))
            finally:
                os.close(fd)
            if not ended or k + 1 >= len(todo) or not any(_pread_path(todo[k + 1][1], HEAD_SIZE, HEADER_SIZE)):
                break
            now = parse_header(_pread_path(path, HEADER_SIZE))  # one read of 64 bytes, never in an ack
            if now is None or now.salt != header.salt:
                # The segment was reused while it was read: the zeros that ended it are the front of the zeroing, not its tail, and what
                # followed (closures) was never seen. Start again from the head of the journal.
                again, cur = self.read_from(None)
                self.restarted = True
                return again, cur
        there = next((h for h, _p in live if h.number == cur[0]), None)
        self._pos_salt = there.salt if there is not None else None
        if out:
            self._last = (out[-1].seg, out[-1].off, out[-1].seq, out[-1].end, out[-1].crc)
        elif self.restarted:
            self._last = None
        return out, cur

    def check(self) -> list[Problem]:
        """The problems of the journal: a fault, a format newer than this code, a file kept aside."""
        try:
            problems = self.read().problems
        except JournalVersionError as e:
            return [Problem("error", str(e))]
        waiting = len(self.reusable())
        if waiting >= COMPACT_STALL_SEGMENTS:
            problems.append(
                Problem(
                    "warning",
                    f"{waiting} segments of the journal wait to be reused and none is: the compaction is stopped (the oldest segment holds "
                    "entries not done that are more than half a segment of text, or the filer does not run); the journal grows meanwhile",
                )
            )
        return problems + self._bad_headers() + self._quarantined()

    def _bad_headers(self) -> list[Problem]:
        """Segments `read` does not show: one with a header that is not valid (not kept aside yet: a `prepare` does that), which may hold
        entries that were acknowledged; one that was being reused, whose old text may still be on the disk."""
        out: list[Problem] = []
        try:
            with os.scandir(self.dir) as it:
                names = sorted(Path(e.path) for e in it if e.name.endswith(".wal") and e.is_file())
        except FileNotFoundError:
            return out
        for path in names:
            try:
                with open(path, "rb") as f:
                    head = f.read(HEADER_SIZE)
                    header = parse_header(head)
                    if header is None:
                        f.seek(0)
                        empty = not any(any(chunk) for chunk in iter(lambda: f.read(ZERO_CHUNK), b""))
                    else:
                        empty = True
            except OSError:
                continue
            if header is None and not empty:
                out.append(
                    Problem(
                        "error",
                        f"journal file {path.name} has a header that is not valid, so none of it is read: it may hold entries that were "
                        "acknowledged; opening the journal for writing keeps it aside (quarantine), and it is for you to look at",
                    )
                )
            elif header is not None and header.flags & FLAG_RECYCLING:
                out.append(
                    Problem(
                        "error",
                        f"journal segment {header.number} was being reused when a process died: what it held is not read, but the old "
                        "text may still be on the disk; opening the journal for writing (or forgetting an entry) finishes it",
                    )
                )
        return out

    def _quarantined(self) -> list[Problem]:
        try:
            with os.scandir(self.dir) as it:
                names = sorted(e.name for e in it if e.name.endswith(QUARANTINE))
        except FileNotFoundError:
            return []
        return [
            Problem(
                "error",
                f"journal file {name} has no valid header and was kept aside (quarantine): it may hold entries that were acknowledged; "
                "look at it, and remove it when it is of no use",
            )
            for name in names
        ]

    def segments(self) -> list[tuple[int, Path, Header]]:
        return [(h.number, p, h) for h, p in self._live(self._headers())]

    def reusable(self) -> list[int]:
        """The numbers of the segments before the one writers use: the candidates for `recycle` (it looks again under the lock). Reads the
        directory and 32 bytes of each segment; no lock."""
        live = self._live(self._headers())
        used = self._used_number(live)
        return [h.number for h, _p in live if h.number < used]

    def free_segments(self) -> list[int]:
        return sorted(int(p.stem) for p, h in self._headers() if h is not None and h.number == 0 and p.stem.isdigit())

    # -- reuse --------------------------------------------------------------------------------------------------------------

    def recycle(self, number: int) -> bool:
        """Let a segment be written again, once everything in it is filed (the caller knows that: `filer.reuse` says when).

        Every byte it used is zeroed and flushed, then its header gets the number 0 and a new salt. Refused (False) for the segment
        in use or one after it, for a number that is not there, for a segment with a fault, and for one with a record that is not valid
        followed by valid ones. The caller copies forward what is not done first (`Filer._compact`).

        The `append.lock` is held for short looks only, never while the journal is read or the bytes are zeroed: acks go on. Before
        the zeroing, under the lock, the header gets a new salt (same number): a writer still attached to the segment (it has not
        seen its seal yet) finds the salt changed under the lock, as `_catch_up` always looks, and attaches again; so nobody writes
        into the bytes that are being zeroed. A crash during the zeroing
        leaves a segment with a new salt and old bytes that nothing reads as records; the next `recycle` zeroes it again."""
        if not self.writable:
            raise JournalError("this journal was opened to be read")
        with self._flock("append.lock"):
            self._catch_up()
            live = self._live(self._headers())
            target = next(((h, p) for h, p in live if h.number == number), None)
            used = self._act.number if self._act is not None else self._used_number(live)
            if target is None or number >= used:
                return False
        header, path = target
        view = self.read()
        mine = next((v for v in view.segments if v.number == number), None)
        if mine is None or mine.fault:
            return False  # records after a fault were acknowledged and nobody has read them: not to be zeroed
        if mine.invalid_at is not None and _look_after(path.read_bytes(), mine.invalid_at, mine.next_seq, header.salt)[1]:
            return False  # a record that is not valid with valid ones after it
        with self._flock("append.lock"):
            now = parse_header(_pread_path(path, HEADER_SIZE))
            if now is None or now.salt != header.salt or now.number != number:
                return False  # somebody else used it again
            held = Header(header.version, number, secrets.randbits(64), self.node_id(), FLAG_RECYCLING)
            _set_header(path, held)
        _zero_used(path)
        with self._flock("append.lock"):
            now = parse_header(_pread_path(path, HEADER_SIZE))
            if now is not None and now.number == 0 and not now.flags & FLAG_RECYCLING:
                return True  # a `prepare` found the segment half reused and finished it: it is free
            if now is None or now.salt != held.salt:
                return False
            _set_header(path, Header(header.version, 0, secrets.randbits(64), self.node_id()))
        return True

    def finish_recycling(self) -> int:
        """A process died between the new salt of a segment that was being reused and its zeroing (the header says so, `FLAG_RECYCLING`):
        the old bytes are zeroed now and the segment is free. Nobody writes to such a segment (`_live` leaves it out). Returns how many."""
        if not self.writable:
            return 0
        done = 0
        for path, h in self._headers():
            if h is None or not h.flags & FLAG_RECYCLING:
                continue
            _zero_used(path)
            with self._flock("append.lock"):
                now = parse_header(_pread_path(path, HEADER_SIZE))
                if now is None or now.salt != h.salt or not now.flags & FLAG_RECYCLING:
                    continue  # somebody finished it first
                _set_header(path, Header(now.version, 0, secrets.randbits(64), self.node_id()))
                done += 1
        return done

    # -- end ----------------------------------------------------------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._preparer is not None:
            self._preparer.join(timeout=60)
        self._drop()
        for lock in self._local.__dict__.get("locks", {}).values():
            lock.close()
        if self._hint is not None:
            self._hint.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
