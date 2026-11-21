# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Transactions on the bundle: what a write section changes is undone if the section does not finish.

A section of `WikiStore.lock()` is a transaction. Before the store touches a path for the first time in it, the image the
path had is appended to an undo log (`.llmw2/journal/undo`) and made durable; the change is made only after. When the
section ends, every file it touched is flushed, a `commit` record is made durable and the log is emptied. A log that is not
empty while nobody holds the bundle's lock belongs to a process that died: `recover` puts every path back as the log says,
so the bundle is what it was before the section, never a half of it. The same code rolls back a section that raised.

One record per line, `<crc32 of the json, 8 hex> <json>`, with a version (`v`). Records, each naming a path (`p`, relative to
the bundle):

  new   the path did not exist; `post`: the hash of what is written
  copy  the path held what the file `trash` holds (`pre` is its hash) and goes to `post` (a hash, or null when it is deleted)
  post  a further state the same path goes to (a path is rewritten more than once in a section)
  len   the path was `n` bytes long and gets `adds` appended: pairs of a length and a hash, never the text
  app   a further append to a path with a `len` record (`addn`, `addh`)
  patch bytes of the path are overwritten in place by the same number of bytes (`segs`: offset and length of each; `trash`: a file
        holding the bytes as they were and, after them, as they go to; `h0`, `h1`: their hashes). The image is the bytes of the
        segments only, never the file; the trash file is erased (zeroed and flushed) before it is deleted, so what a purge
        overwrites does not survive in a copy
  dir   the folder did not exist
  rmdir the folder existed and goes
  decl  one record for what the section is about to do to several paths: `new` (paths that do not exist yet), `len` (the
        length each path has now, which it will be appended to). The caller knows them before the first write, and the
        section then pays one flush for all of them instead of one each
  commit  everything is durable; `id` is the caller's own identifier of what the section did, if it gave one

The log holds no text of a note, a log line or a raw copy, only hashes and lengths, and, in the commit record of a section that filed
an entry of the journal, what the section did for it (`r`: the title and path of the note and the ids of its links). A preallocated
log is not zeroed when a section ends, so the paths and that title stay in its bytes until later sections write over them; a purge
zeroes the whole log (`wipe`) after its sections. Besides that, the log holds only hashes and lengths: the image a rewritten
file had is kept as a hard link to it in `.llmw2/journal/trash/` (the file is replaced, so the old one lives on under that name;
where the file system has no hard links, a copy), emptied when the section ends. On POSIX the log is a segment of the journal's
format (preallocated and
filled with zeros that were written, a new salt for each section, a CRC for each record, `fdatasync`, never truncated); on Windows
a file of lines, truncated at the end, as ever: there a preallocated file gains nothing.

Undoing does not overwrite what somebody else wrote after the crash (an older version of the library, a hand edit, git), except for
what a declaration (`decl`) covers: a path is put back only when it is as the log expects (the image before, or one of the states
the section was going to write, or a file a crash left empty); any other content is left alone and written in
`.llmw2/journal/conflicts`, which `check` reports. A path in a `decl` record has no hash of what it was going to hold, so the
undo removes a declared new file and cuts a declared length whatever follows: lines that a process which does not recover (a
library of an older version) appended to `log.md` after the crash, before the first lock of this one, are cut with the section's own.
The recovery runs at the first lock, before any other write of this library.

Not covered, as ever: a folder moved by `os.replace` (`replace_dir` refuses inside a transaction: a section that does it opens
with `lock(undo=False)`), and the caches in `.llmw2/` (a classifier's answers, the vectors).
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import base64
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import secrets
import shutil
import sys
import threading
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmw2.bundle import journal
from llmw2.errors import WikiError

log = logging.getLogger(__name__)

JOURNAL_DIR = ".llmw2/journal"
UNDO_FILE = f"{JOURNAL_DIR}/undo"
CONFLICTS_FILE = f"{JOURNAL_DIR}/conflicts"
TRASH_DIR = f"{JOURNAL_DIR}/trash"
GITIGNORE_FILE = f"{JOURNAL_DIR}/.gitignore"
GITIGNORE_TEXT = "# written by llmw2: transient files of the bundle, never kept in git\n*\n!.gitignore\n"
VERSION = 1
UNDO_SIZE = 1 << 20  # the preallocated log; it grows, once, if a section needs more
PREALLOCATED = sys.platform != "win32"  # tests set it to run the other format
REPLACE_ATTEMPTS = 6  # as for a note: a reader may hold the file for a moment on Windows
_BINARY = getattr(os, "O_BINARY", 0)


class TxnUnsupported(WikiError, RuntimeError):
    """A write the undo log cannot cover was made inside a transaction (a folder moved by `os.replace`)."""


@dataclass
class Restored:
    """What `recover` found and did."""

    rolled_back: bool = False  # an interrupted section was undone
    committed: bool = False  # the section had committed: the log was only emptied
    paths: int = 0  # paths put back
    conflicts: list[str] = field(default_factory=list)  # paths left alone because they changed after the crash
    id: str | None = None  # of the section that had committed: the journal entry it filed
    result: dict[str, Any] | None = None  # what that section said it did (the body of the `F` record the filer owes the journal)


_current: contextvars.ContextVar[Txn | None] = contextvars.ContextVar("llmw2_txn", default=None)


def current() -> Txn | None:
    """The transaction of this thread (or of the copies of its context), if a section is open: one that has ended, whose context
    a thread kept a copy of, is not a transaction any more."""
    section = _current.get()
    return None if section is None or section.closed else section


@contextlib.contextmanager
def active(txn: Txn | None) -> Iterator[None]:
    token = _current.set(txn)
    try:
        yield
    finally:
        _current.reset(token)


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()[:32]


def _torn(data: bytes) -> bool:
    """What a crash leaves of a file that was never flushed: nothing, or zeros."""
    return not data.strip(b"\x00")


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except PermissionError:
        if path.is_dir():  # Windows says so for a folder
            return None
        raise


def _fsync_fd(fd: int, *, full: bool = False) -> None:
    """`fsync`; with `full`, on macOS first `F_FULLFSYNC` (there `fsync` hands the data to the drive, which may hold it in its cache)."""
    if full and sys.platform == "darwin":
        try:
            import fcntl

            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)  # type: ignore[attr-defined]
            return
        except (ImportError, AttributeError, OSError):
            pass  # a file system that does not know it: plain fsync
    os.fsync(fd)


def _fsync_path(path: Path, *, directory: bool = False, full: bool = False) -> None:
    """Flush a file (a handle open for writing, as Windows asks) or, on POSIX, a folder; a path that is gone is skipped."""
    if directory and sys.platform == "win32":
        return  # no way to flush a folder from Python: NTFS records its metadata itself
    flags = os.O_RDONLY if directory else os.O_RDWR
    try:
        fd = os.open(path, flags | _BINARY)
    except PermissionError:
        if directory:
            return
        fd = os.open(path, os.O_RDONLY | _BINARY)
    except (FileNotFoundError, NotADirectoryError):
        return
    try:
        _fsync_fd(fd, full=full)
    except OSError:
        if not directory:
            raise
    finally:
        os.close(fd)


def _write_plain(path: Path, data: bytes) -> None:
    """Replace a file as `write_atomic` does, then flush it (the restore is not itself undone)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.01 * 2**attempt)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_path(path)


def _encode(record: dict[str, Any]) -> bytes:
    body = json.dumps({"v": VERSION, **record}, separators=(",", ":"), ensure_ascii=True)
    return f"{zlib.crc32(body.encode('ascii')):08x} {body}\n".encode("ascii")


def read_records(path: Path) -> list[dict[str, Any]]:
    """The records of an undo log that are whole: a torn last line (the crash came while it was written) ends the list; its
    change was never made, because a record is durable before the change it describes."""
    data = _read(path) or b""
    if data[:8] == journal.MAGIC:
        return _read_segment(data)
    records: list[dict[str, Any]] = []
    for line in data.split(b"\n"):
        if not line:
            continue
        crc, _, body = line.partition(b" ")
        try:
            if int(crc, 16) != zlib.crc32(body):
                break
            records.append(json.loads(body))
        except ValueError:
            break
    return records


def _read_segment(data: bytes) -> list[dict[str, Any]]:
    """The records of a log in the journal's segment format: the chain of this section's salt, to the first record not valid."""
    header = journal.parse_header(data)
    records: list[dict[str, Any]] = []
    if header is None:
        return records
    off, seq = journal.HEADER_SIZE, 1
    while True:
        parsed = journal._parse_at(data, off, header.salt)
        if parsed is None or parsed[0][2] != seq or parsed[0][4] != journal.T_UNDO:
            return records
        decoded = journal._decode_body(parsed[1])
        if decoded is None:
            return records
        records.append(decoded[0])
        off, seq = off + journal._align(journal.HEAD_SIZE + parsed[0][0]), seq + 1


class _SegmentLog:
    """The undo log as a preallocated segment: a new salt for each section in the header, a record is one write and `fdatasync`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.salt = secrets.randbits(64)
        self.off = 0  # 0: the header is not written yet (it goes with the first record, in one write)
        self.seq = 1
        self.size = 0

    def open(self) -> None:
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | _BINARY)
        self.size = os.fstat(self.fd).st_size
        if self.size < UNDO_SIZE:
            self._zeros(UNDO_SIZE - self.size)
            if fresh:
                _fsync_path(self.path.parent, directory=True)

    def _zeros(self, count: int) -> None:
        """Extend the file with zeros that are written (and flushed with its size), not a hole."""
        assert self.fd is not None
        at = self.size
        while count > 0:
            n = min(journal.ZERO_CHUNK, count)
            journal._pwrite(self.fd, bytes(n), at)
            at, count = at + n, count - n
        os.fsync(self.fd)
        self.size = at

    def put(self, record: dict[str, Any], *, flush: bool = True) -> None:
        assert self.fd is not None
        body = journal.encode_body({"v": VERSION, **record})
        data = journal.pack_record(self.salt, self.seq, 0, journal.T_UNDO, 0, body)
        at = self.off
        if at == 0:
            data = journal.Header(journal.VERSION, 1, self.salt, "undo").pack() + data
        if at + len(data) > self.size:
            self._zeros(max(UNDO_SIZE, len(data)))
        journal._pwrite(self.fd, data, at)
        if flush:
            journal._FLUSH(self.fd)
        self.off = (at or 0) + len(data)
        self.seq += 1

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Txn:
    """The undo log of one section of the bundle's lock. Thread-safe: any thread of the section may write."""

    def __init__(self, root: Path, *, id: str | None = None) -> None:
        self.root = Path(os.path.abspath(root))
        self.id = id
        self.undo = self.root / UNDO_FILE
        self._fd: int | None = None
        self._seg: _SegmentLog | None = None
        self._trash = 0  # images kept in the trash by this section
        self._trash_dir: Path | None = None
        self._guard = threading.RLock()
        self._state: dict[str, str] = {}  # rel -> the kind of its first record
        self._lens: dict[str, int] = {}  # rel -> the length a path had before this section appended to it (kinds `dlen`, `len`)
        self._patches: dict[str, list[tuple[int, bytes, bytes]]] = {}  # rel -> (offset, was, is) of what this section overwrote in place
        self._written: dict[str, bytes | None] = {}  # rel -> what it holds now, for a file this section wrote whole
        self._flush: dict[str, Path] = {}  # files to flush at commit
        self._folders: dict[str, Path] = {}  # folders whose entries changed: flushed at commit (POSIX)
        self.committed = False
        self.closed = False  # the section is over (committed and emptied, or rolled back)
        self.result: dict[str, Any] | None = None  # kept in the commit record, with `id`: see `set_result`
        self.told = False  # the `after_commit` hook ran: the `F` record is durable (`finish` marks the log only then)

    # -- the records ------------------------------------------------------------------------------------------

    def touched(self) -> list[str]:
        """The paths inside the bundle (posix) this section wrote, created, cut or deleted."""
        with self._guard:
            return list(self._state)

    def rel(self, path: str | os.PathLike[str]) -> str | None:
        """The path inside the bundle (posix), or None for a path outside it, which a transaction does not cover."""
        full = os.path.abspath(path)
        try:
            inside = os.path.relpath(full, self.root)
        except ValueError:  # another drive
            return None
        if inside == "." or inside.startswith(".."):
            return None
        return inside.replace(os.sep, "/")

    def _open(self) -> int | None:
        if self._fd is None and self._seg is None:
            ensure_journal_dir(self.root)
            if PREALLOCATED:
                self._seg = _SegmentLog(self.undo)
                self._seg.open()
                return None
            fresh = not self.undo.exists()
            self.undo.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.undo, os.O_RDWR | os.O_CREAT | _BINARY)
            os.lseek(self._fd, 0, os.SEEK_END)
            if fresh:
                _fsync_path(self.undo.parent, directory=True)
        return self._fd

    @property
    def opened(self) -> bool:
        return self._fd is not None or self._seg is not None

    def _put(self, record: dict[str, Any]) -> None:
        """Append a record and make it durable: the caller changes the bundle only after this returns."""
        fd = self._open()
        if self._seg is not None:
            self._seg.put(record)
            return
        assert fd is not None
        view = memoryview(_encode(record))
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)

    def _stash(self, path: Path, image: bytes | None = None) -> str:
        """Keep the image `path` has now, as a file in the trash that is not a copy of it where the file system allows: a hard
        link (the file is replaced, so its old content lives on under the link's name). Durable before the record that names it."""
        trash = self.root / TRASH_DIR
        made = not trash.exists()
        trash.mkdir(parents=True, exist_ok=True)
        name = f"{self.salt_name}-{self._trash:05d}" + (".p" if image is not None else "")  # a written copy is erased, a link only deleted
        self._trash += 1
        target = trash / name
        if image is not None:  # an image that is not the file as it is now (a path appended to, then rewritten): a copy of it
            target.write_bytes(image)
            _fsync_path(target)
        else:
            try:
                os.link(path, target)
            except OSError:  # no hard links here (or the file is on another volume): a copy, flushed
                shutil.copyfile(path, target)
                _fsync_path(target)
        if made:
            _fsync_path(trash.parent, directory=True)
        self._trash_dir = trash
        _fsync_path(trash, directory=True)  # the name must be there after a crash for the record to be of use
        return name

    @property
    def salt_name(self) -> str:
        return f"{os.getpid():x}"

    def declare(self, *, new: Sequence[str | os.PathLike[str]] = (), extend: Sequence[str | os.PathLike[str]] = ()) -> None:
        """One record for the paths this section is about to make (`new`) or append to (`extend`): what a path of each kind needs to
        be undone is known before the first write, so the section pays one flush for them all. A path that is not made after all, or
        that is rewritten instead of appended to, costs nothing here (a rewrite gets its own `copy` record then)."""
        with self._guard:
            fresh_new: list[str] = []
            lengths: dict[str, int] = {}
            for path in new:
                rel = self.rel(path)
                if rel is None or rel in self._state or Path(path).exists():
                    continue
                fresh_new.append(rel)
            for path in extend:
                rel = self.rel(path)
                if rel is None or rel in self._state:
                    continue
                try:
                    lengths[rel] = os.stat(path).st_size
                except FileNotFoundError:
                    fresh_new.append(rel)
            if not fresh_new and not lengths:
                return
            record: dict[str, Any] = {"t": "decl"}
            if fresh_new:
                record["new"] = fresh_new
            if lengths:
                record["len"] = lengths
            self._put(record)
            for rel in fresh_new:
                self._state[rel] = "dnew"
                self._flush[rel] = self.root / rel
                self._folders[str((self.root / rel).parent)] = (self.root / rel).parent
            for rel, size in lengths.items():
                self._state[rel] = "dlen"
                self._lens[rel] = size
                self._flush[rel] = self.root / rel

    def _folder_of(self, path: Path) -> None:
        self._folders[str(path.parent)] = path.parent

    def before_write(self, path: str | os.PathLike[str], data: bytes) -> None:
        """A file is about to be replaced (or made) with `data`."""
        self._before(path, digest(data))

    def before_remove(self, path: str | os.PathLike[str]) -> None:
        """A file is about to be deleted."""
        self._before(path, None)

    def _before(self, path: str | os.PathLike[str], post: str | None) -> None:
        rel = self.rel(path)
        if rel is None:
            return
        full = Path(path)
        with self._guard:
            kind = self._state.get(rel)
            if kind in ("dlen", "len"):
                # appended to (declared or not) and rewritten or deleted instead: the image to put back is the first `size` bytes,
                # not the file as it is now (which holds the appended text); the state it has now is one the undo may meet
                now = _read(full)
                if now is not None:
                    size = self._lens[rel]
                    image = self._original(rel, now)[:size]  # bytes overwritten in place in this section are as they were
                    changed = len(now) != size or rel in self._patches
                    if changed:
                        self._put({"t": "post", "p": rel, "post": digest(now)})
                    stash = self._stash(full, image if changed else None)
                    self._put({"t": "copy", "p": rel, "trash": stash, "pre": digest(image), "post": post})
                    self._state[rel] = "copy"
                    self._patches.pop(rel, None)
                else:
                    self._put({"t": "post", "p": rel, "post": post})
            elif kind == "patch":
                # overwritten in place, then rewritten or deleted: the image to put back is the file as it was before the overwriting
                now = _read(full)
                if now is not None:
                    image = self._original(rel, now)
                    self._put({"t": "post", "p": rel, "post": digest(now)})
                    stash = self._stash(full, image)
                    self._put({"t": "copy", "p": rel, "trash": stash, "pre": digest(image), "post": post})
                    self._state[rel] = "copy"
                    self._patches.pop(rel, None)
                else:
                    self._put({"t": "post", "p": rel, "post": post})
            elif kind is None:
                now = _read(full)
                if now is None:
                    if post is None:
                        return  # nothing to delete
                    self._put({"t": "new", "p": rel, "post": post})
                    self._state[rel] = "new"
                else:
                    self._put({"t": "copy", "p": rel, "trash": self._stash(full), "pre": digest(now), "post": post})
                    self._state[rel] = "copy"
            elif kind == "dnew":
                if post is None:  # a path declared new and deleted again: no record needed, the declaration says it did not exist
                    self._flush.pop(rel, None)
                    self._folder_of(full)
                    return
            else:
                self._put({"t": "post", "p": rel, "post": post})
            if post is None:
                self._flush.pop(rel, None)
            else:
                self._flush[rel] = full
            self._folder_of(full)

    def _original(self, rel: str, data: bytes) -> bytes:
        """`data`, a file this section overwrote in place, as it was before the overwriting."""
        buf = bytearray(data)
        for off, was, _ in reversed(self._patches.get(rel, [])):
            if off + len(was) <= len(buf):
                buf[off : off + len(was)] = was
        return bytes(buf)

    def before_patch(self, path: str | os.PathLike[str], edits: Sequence[tuple[int, bytes, bytes]]) -> None:
        """Bytes of a file are about to be overwritten in place: `edits` are (offset, the bytes there now, the bytes of the same length
        that go there). What undoing needs is the bytes of the segments, kept in a file of the trash that is made durable first and
        erased when the section ends; the file itself, however long, is neither copied nor linked."""
        rel = self.rel(path)
        if rel is None or not edits:
            return
        full = Path(path)
        with self._guard:
            kind = self._state.get(rel)
            if kind in ("new", "copy", "dnew"):  # made or rewritten whole in this section: it is undone as a whole
                if kind != "dnew":
                    whole = bytearray(_read(full) or b"")
                    for off, _, new in edits:
                        whole[off : off + len(new)] = new
                    self._put({"t": "post", "p": rel, "post": digest(bytes(whole))})
                self._flush[rel] = full
                return
            was = b"".join(w for _, w, _ in edits)
            now = b"".join(n for _, _, n in edits)
            stash = self._stash(full, was + now)
            self._put(
                {"t": "patch", "p": rel, "segs": [[o, len(w)] for o, w, _ in edits], "h0": digest(was), "h1": digest(now), "trash": stash}
            )
            if kind is None:
                self._state[rel] = "patch"
            self._patches.setdefault(rel, []).extend(edits)
            self._flush[rel] = full

    def before_append(self, path: str | os.PathLike[str], add: bytes) -> None:
        """`add` is about to be appended to a file (made if it is not there)."""
        rel = self.rel(path)
        if rel is None:
            return
        full = Path(path)
        with self._guard:
            kind = self._state.get(rel)
            if kind in ("new", "copy"):  # written whole in this section: the state it goes to is the whole file plus `add`
                self._put({"t": "post", "p": rel, "post": digest((_read(full) or b"") + add)})
            elif kind == "len":
                self._put({"t": "app", "p": rel, "addn": len(add), "addh": digest(add)})
            elif kind in ("dnew", "dlen"):
                pass  # the declaration has what undoing needs
            else:
                try:
                    size = full.stat().st_size
                except FileNotFoundError:
                    self._put({"t": "new", "p": rel, "post": digest(add)})
                    self._state[rel] = "new"
                else:
                    self._put({"t": "len", "p": rel, "n": size, "adds": [[len(add), digest(add)]]})
                    self._state[rel] = "len"
                    self._lens[rel] = size
            self._flush[rel] = full
            self._folder_of(full)

    def before_mkdir(self, path: str | os.PathLike[str]) -> None:
        """A folder, and the folders above it that are not there, are about to be made."""
        missing: list[Path] = []
        here = Path(os.path.abspath(path))
        while not here.exists() and self.rel(here) is not None:
            missing.append(here)
            here = here.parent
        with self._guard:
            for folder in reversed(missing):
                rel = self.rel(folder)
                assert rel is not None
                self._put({"t": "dir", "p": rel})
                self._state.setdefault(rel, "dir")
                self._folder_of(folder)

    def before_rmdir(self, path: str | os.PathLike[str]) -> None:
        rel = self.rel(path)
        if rel is None:
            return
        with self._guard:
            self._put({"t": "rmdir", "p": rel})
            self._state.setdefault(rel, "rmdir")
            self._folder_of(Path(path))

    # -- the end of the section ---------------------------------------------------------------------------------

    def set_result(self, result: dict[str, Any]) -> None:
        """What the section did, for the journal: it goes into the commit record, next to `id`, so that a process that dies after the
        commit and before it has told the journal (the `F` record) leaves what the next process needs to tell it (`recover`). It adds
        bytes to a record that is written anyway, not a flush."""
        self.result = result

    def commit(self) -> None:
        """Make every change durable, then record that it is: from here on the section is done, whatever happens."""
        with self._guard:
            if not self.opened:
                self.committed = True  # nothing was written
                return
            for full in list(self._flush.values()):
                _fsync_path(full)
            for folder in self._folders.values():
                _fsync_path(folder, directory=True)
            record: dict[str, Any] = {"t": "commit"}
            if self.id:
                record["id"] = self.id
                if self.result is not None:
                    record["r"] = self.result
            self._put(record)
            self.committed = True

    def abandon(self) -> None:
        """Close the section and leave the log as it is, commit record included: the next `recover` finds it and does what is owed."""
        with self._guard:
            self.closed = True
            seg, self._seg = self._seg, None
            fd, self._fd = self._fd, None
            if seg is not None:
                seg.close()
            if fd is not None:
                os.close(fd)

    def finish(self) -> None:
        """Empty the log (not flushed: a log that still says `commit` after a crash is only emptied again)."""
        with self._guard:
            self.closed = True
            seg, self._seg = self._seg, None
            fd, self._fd = self._fd, None
            if seg is not None:
                if self.id and self.result is not None and self.told:
                    # the entry's `F` record is durable (the hook ran): not flushed, because a mark that is lost only makes the next
                    # recovery write the `F` record once more, and a section's commit carries no flush for it
                    with contextlib.suppress(OSError):
                        seg.put({"t": "filed"}, flush=False)
                seg.close()  # a preallocated log is never emptied: the next section writes a new salt
            if fd is not None:
                try:
                    os.ftruncate(fd, 0)
                finally:
                    os.close(fd)
            self._empty_trash()

    def _empty_trash(self) -> None:
        if self._trash_dir is not None:
            for entry in os.scandir(self._trash_dir):
                _discard(entry.path)
            self._trash_dir = None

    def rollback(self) -> Restored:
        """Undo the section after an exception."""
        with self._guard:
            self.closed = True
            seg, self._seg = self._seg, None
            fd, self._fd = self._fd, None
            if seg is not None:
                seg.close()
            if fd is not None:
                os.close(fd)
        done = recover(self.root)
        self._empty_trash()
        return done


# -- the primitives of the store -------------------------------------------------------------------------------------


def unlink(path: str | os.PathLike[str], *, missing_ok: bool = False) -> None:
    """Delete a file; on Windows, one another program holds open for a moment (with no FILE_SHARE_DELETE: the library's own reads share
    it, `document.open_note`) is tried again, as a replace is."""
    if (t := current()) is not None:
        t.before_remove(path)
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            Path(path).unlink(missing_ok=missing_ok)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * 2**attempt)


def mkdir(path: str | os.PathLike[str], *, parents: bool = False, exist_ok: bool = False) -> None:
    if (t := current()) is not None:
        t.before_mkdir(path)
    Path(path).mkdir(parents=parents, exist_ok=exist_ok)


def touch(path: str | os.PathLike[str]) -> None:
    """Make an empty file if there is none (a marker); one that is there stays as it is."""
    if (t := current()) is not None and not os.path.exists(path):
        t.before_write(path, b"")
    Path(path).touch()


def append(path: str | os.PathLike[str], text: str) -> None:
    """Append text to a file (made if missing), written in one `os.write` where the file is."""
    data = text.encode("utf-8")
    if (t := current()) is not None:
        t.before_append(path, data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _BINARY)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def patch(path: str | os.PathLike[str], edits: Sequence[tuple[int, bytes, bytes]]) -> None:
    """Overwrite bytes of a file in place: `edits` are (offset, the bytes there now, the bytes that go there, of the same length).
    The file keeps its size and its blocks; what is written is the bytes of the edits, not the file."""
    if any(len(was) != len(new) for _, was, new in edits):
        raise ValueError("an in-place edit keeps the length of what it replaces")
    if (t := current()) is not None:
        t.before_patch(path, edits)
    fd = os.open(path, os.O_WRONLY | _BINARY)
    try:
        for off, _, new in edits:
            journal._pwrite(fd, new, off)
    finally:
        os.close(fd)


def rmtree(path: str | os.PathLike[str], *, ignore_errors: bool = False) -> None:
    """`shutil.rmtree`; inside a transaction every file and folder of the tree is recorded first (for a tree of a few notes)."""
    if (t := current()) is None or t.rel(path) is None:
        shutil.rmtree(path, ignore_errors=ignore_errors)
        return
    top = Path(path)
    if not top.is_dir():
        if not ignore_errors:
            raise FileNotFoundError(str(path))
        return
    for folder, _, files in os.walk(top, topdown=False):
        for name in files:
            t.before_remove(Path(folder) / name)
            (Path(folder) / name).unlink()
        t.before_rmdir(folder)
        os.rmdir(folder)


def replace_dir(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """`os.replace` of a folder: the undo log cannot cover it, so a section that moves one opens with `lock(undo=False)`."""
    if (t := current()) is not None and (t.rel(src) is not None or t.rel(dst) is not None):
        raise TxnUnsupported("a folder cannot be moved inside a transaction: open the section with lock(undo=False)")
    os.replace(src, dst)


# -- recovery ---------------------------------------------------------------------------------------------------------


def ensure_journal_dir(root: Path) -> None:
    """The folder of the transient files, with the `.gitignore` that keeps them out of git."""
    ignore = root / GITIGNORE_FILE
    if ignore.exists():
        return
    ignore.parent.mkdir(parents=True, exist_ok=True)
    tmp = ignore.with_name(f".gitignore.{os.getpid()}.tmp")
    tmp.write_text(GITIGNORE_TEXT, encoding="utf-8", newline="\n")
    os.replace(tmp, ignore)


REBUILD_FILE = f"{JOURNAL_DIR}/rebuild"


def mark_rebuild(root: Path) -> None:
    """A rebuild is starting without an undo log for each document: this mark, durable, says so, and is taken away when it is over.
    A process that dies in between leaves it, and `check` says the rebuild was interrupted and is to be run again."""
    root = Path(root)
    ensure_journal_dir(root)
    path = root / REBUILD_FILE
    with open(path, "wb") as f:
        f.write(b"a rebuild was running: a wiki found with this file is half built; run the rebuild again\n")
        f.flush()
        os.fsync(f.fileno())
    _fsync_path(path.parent, directory=True)


def sync_tree(root: Path) -> None:
    """Make everything under the bundle durable: what a rebuild wrote has no undo log, so no commit flushed it. On Linux `os.sync()`
    (it waits for the write to end: everything the system holds, more than the bundle, and the simplest way to be sure). Elsewhere
    every file of the bundle is flushed, one by one: on macOS `os.sync()` does not wait, so each file gets `F_FULLFSYNC` there;
    on Windows `fsync` of a handle open for writing (`FlushFileBuffers`)."""
    root = Path(root)
    if sys.platform.startswith("linux") and hasattr(os, "sync"):
        os.sync()
        return
    last: Path | None = None
    for folder, _, files in os.walk(root):
        for name in files:
            with contextlib.suppress(OSError):
                _fsync_path(Path(folder) / name)
                last = Path(folder) / name
        _fsync_path(Path(folder), directory=True)
    if last is not None:  # on macOS one F_FULLFSYNC, the last, is what makes everything before it reach the drive
        with contextlib.suppress(OSError):
            _fsync_path(last, full=True)


def clear_rebuild(root: Path) -> None:
    path = Path(root) / REBUILD_FILE
    with contextlib.suppress(OSError):
        path.unlink()
        _fsync_path(path.parent, directory=True)


def rebuild_interrupted(root: Path) -> bool:
    return (Path(root) / REBUILD_FILE).exists()


def _owes_filed(records: list[dict[str, Any]]) -> bool:
    """A section that committed for an entry of the journal, with what it did, and that never marked the `F` record as written."""
    commit = next((r for r in records if r.get("t") == "commit"), None)
    return bool(commit and commit.get("id") and isinstance(commit.get("r"), dict) and not any(r.get("t") == "filed" for r in records))


def pending(root: Path) -> bool:
    """Whether an undo log with something in it is there: the section of a process that is gone, if the lock is free.

    A log in the line format is pending when it is not empty. A segment is always full of zeros: it is pending when its chain has
    records and none is a `commit` (a section that committed is done, and only waits for its salt to be replaced). The answer for
    a segment is remembered by the inode of the file and the salt of its header, so that most calls cost a read of 64 bytes."""
    path = Path(root) / UNDO_FILE
    try:
        with open(path, "rb") as f:
            head = f.read(journal.HEADER_SIZE)
            inode = os.fstat(f.fileno()).st_ino
    except OSError:
        return False
    if not head:
        return False
    header = journal.parse_header(head) if head[:8] == journal.MAGIC else None
    # the identity of a segment log is its inode and the salt its header holds, which a section replaces with a random one: a size or
    # a time stamp would not tell two sections apart on a file system whose clock is coarse (the file is preallocated, its size never
    # changes); the inode tells a file put back from a backup, whose salt may be one this process saw close, from the one it saw
    key = (str(path), inode, header.salt) if header is not None else None
    if key is not None and _clean.get(str(path)) == key:
        return False
    data = _read(path) or b""
    if data[:8] != journal.MAGIC:
        return True
    records = _read_segment(data)
    if not records or any(r.get("t") == "commit" for r in records):
        if _owes_filed(records):
            return True  # committed, and the `F` record of its entry was not written: somebody has to
        if key is not None:
            _clean[str(path)] = key
        return False
    return True


_clean: dict[str, tuple[Any, ...]] = {}  # a segment log known to hold nothing to undo, by the identity it had then


@dataclass
class _Path:
    kind: str
    first: int  # index of the first record of the path: where in the order it is undone
    data: bytes | None = None
    trash: str | None = None
    pre: str | None = None
    posts: set[str | None] = field(default_factory=set)
    size: int = 0
    adds: list[tuple[int, str | None]] = field(default_factory=list)  # (length, hash) of each piece appended
    declared: bool = False  # from a `decl` record: no hash of what it was going to hold
    patches: list[dict[str, Any]] = field(default_factory=list)  # the `patch` records: bytes overwritten in place


def file_committed(root: Path, done: Restored) -> None:
    """The section that committed filed an entry of the journal and the process died before it said so: write the `F` record it owes
    (the id and the result are in the commit record) and make it durable. Twice is harmless (the entry's state is the same)."""
    if not done.id or done.result is None:
        return
    j = journal.Journal(root)
    try:
        j.append(journal.T_FILED, {"id": done.id, **done.result})
    finally:
        j.close()


def recover(
    root: Path,
    *,
    on_conflict: Callable[[str, str], None] | None = None,
    on_committed: Callable[[Restored], None] | None = None,
) -> Restored:
    """Put the bundle back as an undo log says, then empty the log. The caller holds the bundle's lock.

    With a `commit` record in the log the section was done: the log is emptied and nothing else changes, except that a section that
    filed an entry of the journal (its commit carries the entry's id and what it did) has the `F` record written first, and durable,
    by `on_committed` (`file_committed` by default): the log is emptied only after, and if that fails the log stays for the next
    call. Idempotent: if the process dies again in the middle, the next call starts over and finds what is already back in place."""
    root = Path(os.path.abspath(root))
    undo = root / UNDO_FILE
    report = Restored()
    records = read_records(undo)
    if not records:
        if pending(root):  # nothing whole in it: a record torn as it was written, whose change was never made
            _truncate(undo)
        _clear_trash(root)
        return report
    if commit := next((r for r in records if r.get("t") == "commit"), None):
        report.committed = True
        report.id = commit.get("id")
        report.result = commit.get("r") if isinstance(commit.get("r"), dict) else None
        if report.id and report.result is not None and not any(r.get("t") == "filed" for r in records):
            (on_committed or (lambda d: file_committed(root, d)))(report)
        _truncate(undo)
        _clear_trash(root)
        return report
    paths: dict[str, _Path] = {}
    for i, rec in enumerate(records):
        kind = rec.get("t")
        if kind == "decl":
            for name in rec.get("new") or []:
                paths.setdefault(name, _Path("new", i, declared=True))
            for name, n in (rec.get("len") or {}).items():
                paths.setdefault(name, _Path("len", i, size=int(n), declared=True))
            continue
        rel = rec.get("p")
        if not isinstance(rel, str):
            continue
        one = paths.get(rel)
        if kind == "patch":
            if one is None:
                one = paths[rel] = _Path("patch", i)
            one.patches.append(rec)
            continue
        if kind == "copy" and one is not None and one.kind in ("len", "patch"):
            # appended to or patched, then rewritten: the image undoes it
            one.kind, one.declared, one.adds, one.patches = "copy", False, [], []
            one.trash, one.pre = rec.get("trash"), rec.get("pre")
        elif kind == "len" and one is not None and one.kind == "patch":
            one.kind, one.size = "len", int(rec.get("n", 0))  # patched, then appended to
        elif kind in ("new", "copy", "len", "dir", "rmdir") and one is None:
            one = paths[rel] = _Path(str(kind), i)
            if kind == "copy":
                one.trash, one.pre = rec.get("trash"), rec.get("pre")
                if "data" in rec:  # a log of an earlier form held the image itself
                    one.data = base64.b64decode(rec["data"])
            if kind == "len":
                one.size = int(rec.get("n", 0))
        if one is None:
            continue
        if "post" in rec:
            one.posts.add(rec["post"])
        if kind in ("len", "app"):
            if "add" in rec:  # an earlier form held the text
                piece = base64.b64decode(rec["add"])
                one.adds.append((len(piece), digest(piece)))
            for n, h in rec.get("adds") or []:
                one.adds.append((int(n), h))
            if "addn" in rec:
                one.adds.append((int(rec["addn"]), rec.get("addh")))
    conflicts: dict[str, str] = {}
    touched_names = {root / rel for rel in paths}
    for path in touched_names:  # the temporary files of writes the crash cut go first: a folder they are in must be empty to go
        with contextlib.suppress(OSError):
            for tmp in path.parent.glob(f".{path.name}.*.tmp"):
                tmp.unlink(missing_ok=True)
    for rel, one in sorted(paths.items(), key=lambda kv: -kv[1].first):
        try:
            why = _undo_one(root / rel, one, root / TRASH_DIR)
        except OSError as e:
            why = f"could not be put back ({e})"
        if why is None:
            report.paths += 1
        else:
            conflicts[rel] = why
    for folder in {p.parent for p in touched_names}:
        _fsync_path(folder, directory=True)
    report.rolled_back = True
    for rel, why in conflicts.items():
        report.conflicts.append(f"{rel}: {why}")
        if on_conflict is not None:
            on_conflict(rel, why)
    if conflicts:
        _note_conflicts(root, conflicts)
    _truncate(undo)
    _clear_trash(root)
    return report


def _tail_is_ours(tail: bytes, adds: list[tuple[int, str | None]]) -> bool:
    """Whether what follows the length a path had is what the section was appending, as far as a hash can say: every piece that is
    whole must be the one hashed; a last piece that is only part of what was to be written (the crash cut it) is taken as ours;
    anything beyond the pieces is somebody else's."""
    if _torn(tail):
        return True
    pos = 0
    for n, h in adds:
        piece = tail[pos : pos + n]
        if len(piece) == n and h is not None and digest(piece) != h and not _torn(piece):
            return False
        pos += n
        if len(piece) < n:
            return True
    return pos >= len(tail)


def _undo_one(path: Path, one: _Path, trash: Path | None = None) -> str | None:
    """Put one path back; None when done, else why it was left alone."""
    now = _read(path) if one.kind not in ("dir", "rmdir") else None
    if one.kind == "new":
        if now is None:
            return None
        if one.declared or digest(now) in one.posts or _torn(now):
            path.unlink()
            return None
        return "it did not exist before the interrupted filing and now has other content"
    if one.kind == "copy":
        image = one.data
        if image is None and one.trash is not None and trash is not None:
            image = _read(trash / one.trash)
        if image is None or (one.pre is not None and digest(image) != one.pre):
            return "the image it had before the interrupted filing is not kept any more"
        if now is not None and digest(now) == one.pre:
            return None
        if now is None or digest(now) in one.posts or _torn(now):
            _write_plain(path, image)
            return None
        return "it was changed after the interrupted filing"
    if one.kind in ("len", "patch"):
        if one.patches and (why := _unpatch(path, one.patches, trash)) is not None:  # first: a line appended may have been overwritten
            return why
        now = _read(path) if one.patches else now
        if one.kind == "len" and now is not None and len(now) > one.size:
            if not (one.declared or _tail_is_ours(now[one.size :], one.adds)):
                return "it was changed after the interrupted filing"
            with open(path, "r+b") as f:
                f.truncate(one.size)
                f.flush()
                os.fsync(f.fileno())
        return None
    if one.kind == "dir":
        if not path.is_dir():
            return None
        try:
            path.rmdir()
        except OSError:
            return "the folder it made is not empty"
        return None
    if one.kind == "rmdir":
        path.mkdir(parents=True, exist_ok=True)
        return None
    return None


def _unpatch(path: Path, patches: list[dict[str, Any]], trash: Path | None) -> str | None:
    """Put back the bytes a section overwrote in place, the last overwriting first. A segment is put back when it holds the bytes the
    section put there, or the old ones, or what a write cut half way leaves (each byte one of the two, or zero); any other content is
    somebody else's and stays. A segment past the end of the file (the appended tail was cut off) is skipped."""
    if not path.exists():
        return None
    with open(path, "r+b") as f:
        size = os.fstat(f.fileno()).st_size
        restored = False
        for rec in reversed(patches):
            blob = _read(trash / rec["trash"]) if trash is not None and rec.get("trash") else None
            segs = [(int(o), int(n)) for o, n in rec.get("segs") or []]
            total = sum(n for _, n in segs)
            if blob is None or len(blob) != 2 * total or digest(blob[:total]) != rec.get("h0") or digest(blob[total:]) != rec.get("h1"):
                return "the bytes it had before the interrupted filing are not kept any more"
            at = 0
            for off, n in segs:
                was, new = blob[at : at + n], blob[total + at : total + at + n]
                at += n
                if off + n > size:  # past the end: the file was cut shorter than it was
                    continue
                f.seek(off)
                cur = f.read(n)
                if cur == was:
                    continue
                if not all(c in (w, v, 0) for c, w, v in zip(cur, was, new, strict=True)):
                    return "it was changed after the interrupted filing"
                f.seek(off)
                f.write(was)
                restored = True
        if restored:
            f.flush()
            os.fsync(f.fileno())
    return None


def _truncate(undo: Path) -> None:
    """Make the log hold nothing: a line log is emptied; a segment gets a new salt, so that nothing in it is valid."""
    with contextlib.suppress(OSError):
        data = _read(undo) or b""
        if data[:8] == journal.MAGIC:
            header = journal.parse_header(data)
            fd = os.open(undo, os.O_RDWR | _BINARY)
            try:
                node = header.node if header is not None else "undo"
                journal._pwrite(fd, journal.Header(journal.VERSION, 1, secrets.randbits(64), node).pack(), 0)
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        with open(undo, "r+b") as f:
            f.truncate(0)


def wipe(root: Path) -> None:
    """Write zeros over every byte the undo log holds that is not zero, flush them, then give a segment a new salt and cut a file of lines
    to nothing: the paths and titles of the sections before are gone from it, whatever their length was. The caller holds the bundle's
    lock with no section open (`Wiki.purge`, after its sections); the trash is emptied too."""
    undo = Path(root) / UNDO_FILE
    data = _read(undo)
    if data is not None:
        segment = data[:8] == journal.MAGIC
        used = len(data.rstrip(b"\0"))
        start = journal.HEADER_SIZE if segment else 0
        fd = os.open(undo, os.O_RDWR | _BINARY)
        try:
            for at in range(start, used, journal.ZERO_CHUNK):
                journal._pwrite(fd, bytes(min(journal.ZERO_CHUNK, used - at)), at)
            if segment:
                header = journal.parse_header(data)
                node = header.node if header is not None else "undo"
                journal._pwrite(fd, journal.Header(journal.VERSION, 1, secrets.randbits(64), node).pack(), 0)
            os.fsync(fd)
            if not segment:
                os.ftruncate(fd, 0)
                os.fsync(fd)
        finally:
            os.close(fd)
    _clear_trash(root)


def _discard(path: str | os.PathLike[str]) -> None:
    """Delete a file of the trash. One that is a copy made for the purpose (`.p`: the bytes a section overwrote in place, or the image of
    a file it overwrote and then rewrote) is zeroed and flushed first: deleting a file does not erase its blocks, and a file deleted with
    its zeros still in the page cache may never get them written. A hard link to a file that was replaced is only deleted."""
    if str(path).endswith(".p"):
        with contextlib.suppress(OSError):
            size = os.stat(path).st_size
            fd = os.open(path, os.O_RDWR | _BINARY)
            try:
                journal._pwrite(fd, bytes(size), 0)
                os.fsync(fd)
            finally:
                os.close(fd)
    with contextlib.suppress(OSError):
        os.unlink(path)


def trash_left(root: Path) -> bool:
    """Whether the trash holds a file."""
    try:
        with os.scandir(Path(root) / TRASH_DIR) as entries:
            return next(entries, None) is not None
    except OSError:
        return False


def sweep_trash(root: Path) -> None:
    """Erase what the trash holds when no undo log is pending: the images are made before the record that names them, so a process
    that died between the two left files nobody owes (and, for bytes overwritten in place, text nobody should keep). The caller holds
    the bundle's lock."""
    if trash_left(root):
        _clear_trash(root)


def _clear_trash(root: Path) -> None:
    """The images kept for a section that is over (undone, or found done) are not needed."""
    trash = Path(root) / TRASH_DIR
    with contextlib.suppress(OSError):
        for entry in os.scandir(trash):
            _discard(entry.path)


def _note_conflicts(root: Path, conflicts: dict[str, str]) -> None:
    """Add the paths that were left alone to `.llmw2/journal/conflicts`, with the hash of what each held then."""
    ensure_journal_dir(root)
    lines = []
    for rel, why in conflicts.items():
        lines.append(json.dumps({"p": rel, "why": why, "h": digest(_read(root / rel))}, ensure_ascii=True))
    with open(root / CONFLICTS_FILE, "ab") as f:
        f.write(("\n".join(lines) + "\n").encode("ascii"))
        f.flush()
        os.fsync(f.fileno())


def conflicts(root: Path) -> list[tuple[str, str]]:
    """(path, why) for the paths a recovery left alone and nobody has changed since (a path edited after is settled)."""
    root = Path(root)
    found: dict[str, str] = {}
    data = _read(root / CONFLICTS_FILE) or b""
    for line in data.splitlines():
        try:
            rec = json.loads(line)
            rel, why, h = rec["p"], rec["why"], rec["h"]
        except (ValueError, KeyError, TypeError):
            continue
        if digest(_read(root / rel)) == h:
            found[rel] = why
        else:
            found.pop(rel, None)
    return sorted(found.items())
