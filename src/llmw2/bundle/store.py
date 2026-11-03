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

import bisect
import contextlib
import contextvars
import hashlib
import logging
import os
import pickle
import re
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from llmw2.bundle.document import DELIMITER, OKFDocument, OKFDocumentError, read_bytes
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.origin_index import OriginIndex
from llmw2.bundle.tree import (
    INDEX,
    LOG,
    NOTE_TYPE,
    OKF_VERSION,
    RAW,
    SOURCE_TYPE,
    Folder,
    Note,
    dead_links_to_text,
    drop_links,
    join_see_also,
    note_dir,
    parse_index,
    refresh_entries,
    rel_link,
    rewrite_links,
    slugify,
    split_see_also,
    subfolder_of,
)
from llmw2.errors import InputError, NoteChanged

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


_author: contextvars.ContextVar[str | None] = contextvars.ContextVar("llmw2_author", default=None)


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


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(title: str, link: str, text: str) -> str:
    """One OKF index / See-also line: `* [Title](link) - text`."""
    title = " ".join(title.split()).replace("[", "(").replace("]", ")")
    text = " ".join(text.split())
    return f"* [{title}]({link})" + (f" - {text}" if text else "")


RACY_NS = 2_000_000_000  # a file this recent is read again: two writes in one clock tick can leave the same time and size

Stamp = tuple[int, int, int]  # st_mtime_ns, st_size, st_ino
STALE = -2  # the time of a kept folder that no folder has: the next load lists it again


def stamp_of(st: os.stat_result) -> Stamp:
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def same_stamp(a: Stamp, b: Stamp) -> bool:
    """Whether two stamps are of the same file content. A listing gives no inode on Windows (0): it then says nothing."""
    return a[:2] == b[:2] and (a[2] == b[2] or 0 in (a[2], b[2]))


@dataclass(frozen=True)
class _Parsed:
    """A note file as `load` last read it, valid while its modification time, size and inode are the same."""

    stamp: tuple[int, int, int]
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
        self._held: dict[str, Note] = {}  # note path -> the note a kept folder holds
        self._loose: dict[str, list[Note | Folder]] = {}  # folder path -> what a write added at the end of its kept lists
        self._session_guard = threading.RLock()

    # -- reading --------------------------------------------------------------

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / RAW).mkdir(exist_ok=True)
        if not (self.root / INDEX).exists():
            self.write_index(Folder(self.root, ""))
        if not (self.root / LOG).exists():
            write_atomic(self.root / LOG, "# Wiki log\n")

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the bundle for writing: one writer at a time across threads and processes (the CLI and a server).

        A lock per bundle folder within the process, shared by every store on it (the server rebuilds its wiki when
        the settings change), then an advisory flock on the folder itself, so no lock file lands in the wiki. Where
        there is no flock (Windows) it is a byte lock on `.llmw2/lock`, retried until free; the file is hidden like the
        rest of `.llmw2/`. The lock is not reentrant: do not nest it.
        """
        with _thread_lock(self.root):
            try:
                import fcntl
            except ImportError:
                with self._windows_lock():
                    yield
                return
            fd = os.open(self.root, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[attr-defined,unused-ignore]  # stubs hide it on Windows
                yield
            finally:
                os.close(fd)  # closing the descriptor releases the lock

    @contextlib.contextmanager
    def _windows_lock(self) -> Iterator[None]:
        """The lock across processes where there is no flock: an exclusive lock on the first byte of `.llmw2/lock`."""
        try:
            import msvcrt
        except ImportError:  # neither flock nor msvcrt: only the lock within the process holds
            yield
            return
        path = self.root / LOCK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
        try:
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined,unused-ignore]
                    break
                except OSError:  # held by another process
                    time.sleep(0.01)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined,unused-ignore]
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def session(self) -> Iterator[None]:
        """A batch of writes by this store (see `Wiki.ingest_many`): `load` and the origin index only look again at what
        changed. The first `load` reads everything, as ever; after it, a folder whose modification time is the same (and
        not recent) keeps the notes it had, and so does `raw/`: every write of the store replaces a file, which changes its
        folder's time. A note edited in place by another program, which leaves the folder's time as it was, is seen by
        the next batch, and never overwritten meanwhile (see `_save`). Outside a session nothing is kept."""
        with self._session_guard:
            self._depth += 1
            self._origins.session = True
        try:
            yield
        finally:
            with self._session_guard:
                self._depth -= 1
                if not self._depth:
                    self._origins.session = False
                    self.forget()

    def forget(self) -> None:
        """Drop what a session kept, so the next `load` reads the wiki whole (after a failure that left it unknown)."""
        with self._session_guard:
            self._dirs.clear()
            self._recent.clear()
            self._held.clear()
            self._loose.clear()
            self._origins.forget()

    def load(self) -> Folder:
        if not self.root.is_dir():
            raise FileNotFoundError(f"wiki not found: {self.root} (run `llmwiki2 init`)")
        if self._depth:
            with self._session_guard:
                return self._load_folder(self.root, "", "", None, {})
        seen: dict[str, _Parsed] = {}  # what this load met, so the cache never outgrows the wiki
        folder = self._load_folder(self.root, "", "", None, seen)
        self._parsed = seen
        return folder

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
        descriptions: dict[str, str] | None = None  # read from index.md when there is a subfolder to describe
        with os.scandir(path) as listing:  # the entries carry their stat, a call each saved on some systems
            entries = sorted(listing, key=lambda e: os.path.normcase(e.name))
        for entry in entries:
            name = entry.name
            if name.startswith(".") or (not rel and name == RAW):
                continue
            child_rel = f"{rel}/{name}" if rel else name
            try:
                st = entry.stat()
            except OSError:  # gone since the listing
                continue
            if stat.S_ISDIR(st.st_mode):
                if descriptions is None:
                    descriptions = {}
                    if (path / INDEX).exists():
                        for _, link, text in parse_index(read_utf8_or_latin1(path / INDEX)):
                            if listed := subfolder_of(link):
                                descriptions[listed] = text
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
                    try:
                        doc = OKFDocument.read(Path(where))
                    except OKFDocumentError as e:
                        log.warning("skipping %s: %s", child_rel, e)
                        continue
                    if time.time_ns() - st.st_mtime_ns > RACY_NS:
                        self._recent.discard(where)
                    else:
                        self._recent.add(where)
                    fresh = self._held[where] = Note(Path(where), child_rel, doc.frontmatter, doc.body, folder, stamp)
                    folder.notes.append(fresh)
                    continue
                if (hit := self._parsed.get(where)) and hit.stamp == stamp:
                    seen[where] = hit
                    folder.notes.append(Note(Path(where), child_rel, pickle.loads(hit.frontmatter), hit.body, folder, stamp))
                    continue
                try:
                    doc = OKFDocument.read(Path(where))
                except OKFDocumentError as e:
                    log.warning("skipping %s: %s", child_rel, e)
                    continue
                if time.time_ns() - st.st_mtime_ns > RACY_NS:  # a file written this very moment may change again unseen
                    seen[where] = _Parsed(stamp, pickle.dumps(doc.frontmatter), doc.body)
                folder.notes.append(Note(Path(where), child_rel, doc.frontmatter, doc.body, folder, stamp))
        if self._depth:
            # a folder changed within RACY_NS of this listing may change again in the same tick, leaving the time as it is:
            # it is listed again at the next load (its notes, which are not recent, are not read again)
            self._dirs[key] = (mtime, scanned - mtime > RACY_NS, folder)
        return folder

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

    # -- writing --------------------------------------------------------------

    def create_folder(
        self, parent: Folder, name: str, description: str, mark: str = "", *, log_it: bool = True
    ) -> tuple[Folder, bool]:
        """Create `parent/name` and list it in the parent's index; (folder, created).

        A sibling with the same slug is reused instead of duplicated. `mark` ends the log line (", by plan"); with
        `log_it` false the caller writes the log line.
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
            folder.path.mkdir(parents=True, exist_ok=True)
            parent.subfolders.append(folder)
            if held:
                self._loose.setdefault(str(parent.path), []).append(folder)
        self.write_index(folder)
        self.write_index(parent)
        if log_it:
            self.append_log("Created folder", folder.description + mark, f"{rel}/{INDEX}", folder.label)
        return folder, True

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
    ) -> Note:
        """Write a new note in `folder`. `overflow`, when given, marks a note the limit on top-level folders deviated (filed in the
        likeliest existing root instead of a new one)."""
        slug = self.unique_slug(folder, slugify(title))
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
    ) -> Note:
        """Rewrite a note in place, keeping unknown frontmatter keys and its See-also links.

        `fields` are added to the note's own (new keys win). `previous_version`: the archived copy of the body it replaces.
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
            path.parent.mkdir(parents=True, exist_ok=True)
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

    def save_raw(self, text: str, *, title: str, resource: str | None, origin: Origin | None = None) -> str:
        """Keep a copy of an ingested source; returns its bundle-absolute path ("/raw/…").

        The frontmatter records the text's hash and, when given, where the original lives.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = self.unique_slug(Folder(self.root / RAW, RAW), f"{stamp}-{slugify(title, max_len=40)}")
        fm: dict[str, Any] = {"type": SOURCE_TYPE, "title": title}
        if resource:
            fm["resource"] = resource
        fm["hash"] = content_hash(text)
        if origin is not None:
            fm["origin"] = origin.to_dict()
            fm["seq"] = 1 + max((c.seq for c in self._origins.copies(origin.key)), default=0)  # save order per key
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        (self.root / RAW).mkdir(exist_ok=True)
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

    def purge_notes(self, root: Folder, notes: list[Note], raws: list[str], keep: Collection[str] = ()) -> Deleted:
        """Delete `notes` and every raw copy in `raws` (cited or not), with the archived versions of the notes and the ones
        citing a raw copy of `raws`. The raw copies in `keep` stay even if no note cites them. Nothing is written to the log."""
        deleted = self._delete(root, notes, [], keep=keep, drop=raws)
        self.remove_raws(raws, deleted)
        return deleted

    def remove_raws(self, raws: Iterable[str], deleted: Deleted) -> None:
        """Delete these raw copies ("/raw/…") that are still there, and say so in `deleted.sources`."""
        for resource in raws:
            path = self.root / resource.lstrip("/")
            if path.is_file():
                path.unlink()
                deleted.sources.append(resource)
        deleted.sources = list(dict.fromkeys(deleted.sources))

    def scrub_log(self, names: Iterable[str]) -> int:
        """Say `[purged]` in place of the text of every line of log.md that names one of `names`; how many lines changed.

        A line keeps its verb and its author (` · by <author>`), loses the rest: the title, the path, the link. The line is
        found by a name as it is written in it: a path, a raw copy's file name, "[title](" for a link.
        """
        path = self.root / LOG
        wanted = [n for n in dict.fromkeys(names) if n]
        if not wanted or not path.exists():
            return 0
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
        return changed

    def write_index(self, folder: Folder) -> None:
        parts: list[str] = []
        if folder.is_root:
            parts.append(f'---\nokf_version: "{OKF_VERSION}"\n---\n')
        if folder.subfolders:
            lines = [_entry(f.name, f"{f.name}/{INDEX}", f.description) for f in folder.subfolders]
            parts.append("# Subdirectories\n\n" + "\n".join(lines) + "\n")
        if folder.notes or not folder.subfolders:
            lines = [_entry(n.title, f"{n.slug}.md", n.summary) for n in folder.notes]
            parts.append("# Notes\n\n" + "\n".join(lines) + ("\n" if lines else ""))
        with self._writing(folder.path, tracked=self._holds(folder)):
            write_atomic(folder.path / INDEX, "\n".join(parts))

    def append_log(self, verb: str, text: str, link: str | None = None, link_title: str | None = None) -> None:
        """Newest first under a `## YYYY-MM-DD` heading (OKF §9); inside `authored`, the line ends with ` · by <author>`."""
        path = self.root / LOG
        lines = read_utf8_or_latin1(path).splitlines() if path.exists() else ["# Wiki log"]
        entry = f"* **{verb}**: " + " - ".join(p for p in (f"[{link_title or link}]({link})" if link else "", text) if p)
        if (author := _author.get()) is not None:
            entry += f" · by {author}"
        today = f"## {datetime.now(UTC).date().isoformat()}"
        first = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
        if first is not None and lines[first] == today:
            lines.insert(first + 2 if lines[first + 1 : first + 2] == [""] else first + 1, entry)
        else:
            at = first if first is not None else len(lines)
            lines[at:at] = [today, "", entry, ""]
            if at > 0 and lines[at - 1] != "":
                lines.insert(at, "")
        with self._writing(self.root, tracked=True):
            write_atomic(path, "\n".join(lines).rstrip() + "\n")

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
            note.path.unlink()
            self._stale(note.path.parent)
            if note.folder is not None:
                note.folder.notes.remove(note)
                touched[note.folder.rel] = note.folder
            target.notes.append(copy)
            touched[target.rel] = target
        for folder in touched.values():
            self.write_index(folder)
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
        os.replace(folder.path, parent.path / name)
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
        left = [p for p in (self.root / src).rglob("*") if p.is_file() and p.name != INDEX]
        if left:  # something that is not a note: the folder stays
            raise InputError(f"/{src} still holds a file that is not a note ({left[0].name}): move or delete it by hand")
        self._stale()
        shutil.rmtree(self.root / src)
        parent = self.load().find(src.rpartition("/")[0])
        if parent is not None:
            self.write_index(parent)
        return len(moves)

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

    def delete_note(self, rel: str) -> Deleted:
        """Delete one note; FileNotFoundError when no note of the tree is at `rel`."""
        root = self.load()
        note = next((n for n in root.all_notes() if n.rel == rel.strip().lstrip("/")), None)
        if note is None:
            raise FileNotFoundError(rel)
        deleted = self._delete(root, [note], [])
        self.append_log("Deleted note", f"{note.title} ({note.rel})")
        return deleted

    def delete_folder(self, rel: str) -> Deleted:
        """Delete a folder with its notes and subfolders; the root is the wiki itself (see `clear`)."""
        root = self.load()
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
            raw.unlink()
            deleted.sources.append(f"/{RAW}/{raw.name}")
        deleted.sources = list(dict.fromkeys(deleted.sources))
        archive = self.root / ARCHIVE_DIR
        deleted.archived += [f"/{p.relative_to(self.root).as_posix()}" for p in sorted(archive.rglob("*.md"))]
        deleted.archived = list(dict.fromkeys(deleted.archived))
        shutil.rmtree(archive, ignore_errors=True)
        shutil.rmtree(self.root / SYNC_DIR, ignore_errors=True)
        if not keep_folders:
            shutil.rmtree((self.root / CACHE_FILE).parent, ignore_errors=True)
        (self.root / PROPOSALS_FILE).unlink(missing_ok=True)  # the notes it names are gone
        write_atomic(self.root / LOG, "# Wiki log\n")
        self.append_log("Emptied the wiki", f"{len(deleted.notes)} notes and {len(deleted.folders)} folders deleted")
        return deleted

    def _delete(
        self, root: Folder, notes: list[Note], folders: list[Folder], keep: Collection[str] = (), drop: Collection[str] = ()
    ) -> Deleted:
        """Remove `notes` and `folders` (with all they hold), and every link to them from the notes that stay.

        `keep`: raw copies that stay even if no note cites them any more. `drop`: raw copies being purged, whose archived
        versions go too, whichever note they were archived from."""
        gone_notes = {n.rel for n in notes}
        gone_dirs = [f.rel for f in folders]

        def gone(target: str) -> bool:
            return target in gone_notes or any(target == d or target.startswith(f"{d}/") for d in gone_dirs)

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
            shutil.rmtree(folder.path)
            if folder.parent is not None:
                folder.parent.subfolders.remove(folder)
                touched.append(folder.parent)
        for note in notes:
            note.path.unlink(missing_ok=True)  # a note inside a deleted folder is gone already
            if note.folder is not None:
                note.folder.notes.remove(note)
                touched.append(note.folder)
        for folder in dict.fromkeys(touched):
            if folder.path.is_dir():
                self.write_index(folder)
        for path in self.archived_of(gone, drop):  # the archived versions of what went
            path.unlink()
            deleted.archived.append(f"/{path.relative_to(self.root).as_posix()}")
        cited = {r for n in kept for r in _raw_sources(n)} | set(keep)
        for resource in dict.fromkeys(r for n in notes for r in _raw_sources(n)):
            path = (self.root / resource.lstrip("/")).resolve()
            if resource not in cited and path.parent == (self.root / RAW).resolve() and path.is_file():
                path.unlink()
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
