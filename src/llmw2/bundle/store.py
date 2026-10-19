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

import contextlib
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from llmw2.bundle.document import DELIMITER, OKFDocument, OKFDocumentError
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
    drop_links,
    join_see_also,
    note_dir,
    parse_index,
    rel_link,
    rewrite_links,
    slugify,
    split_see_also,
    subfolder_of,
)
from llmw2.errors import InputError

log = logging.getLogger(__name__)

ARCHIVE_DIR = ".archive"  # superseded versions of notes; hidden from the tree
SYNC_DIR = ".llmw2/sync"  # the cursors of `sync`, one file per connector and account
REPLACE_ATTEMPTS = 6  # about 0.3 s in all: long enough for a reader to close the file it was reading


def body_hash(body: str) -> str:
    """The hash of a note's main body: it tells whether code alone wrote it."""
    return "sha256:" + hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(title: str, link: str, text: str) -> str:
    """One OKF index / See-also line: `* [Title](link) - text`."""
    title = " ".join(title.split()).replace("[", "(").replace("]", ")")
    text = " ".join(text.split())
    return f"* [{title}]({link})" + (f" - {text}" if text else "")


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
        the settings change), then an advisory flock on the folder itself, so no lock file lands in the wiki.
        """
        with _thread_lock(self.root):
            try:
                import fcntl
            except ImportError:  # Windows has no flock: there only the lock within the process holds
                yield
                return
            fd = os.open(self.root, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[attr-defined,unused-ignore]  # stubs hide it on Windows
                yield
            finally:
                os.close(fd)  # closing the descriptor releases the lock

    def load(self) -> Folder:
        if not self.root.is_dir():
            raise FileNotFoundError(f"wiki not found: {self.root} (run `llmwiki2 init`)")
        return self._load_folder(self.root, "", "", None)

    def _load_folder(self, path: Path, rel: str, description: str, parent: Folder | None) -> Folder:
        folder = Folder(path, rel, description, parent=parent)
        descriptions: dict[str, str] = {}
        if (path / INDEX).exists():
            for _, link, text in parse_index((path / INDEX).read_text(encoding="utf-8")):
                if name := subfolder_of(link):
                    descriptions[name] = text
        for child in sorted(path.iterdir()):
            if child.name.startswith(".") or (not rel and child.name == RAW):
                continue
            child_rel = f"{rel}/{child.name}" if rel else child.name
            if child.is_dir():
                folder.subfolders.append(self._load_folder(child, child_rel, descriptions.get(child.name, ""), folder))
            elif child.suffix == ".md" and child.name not in (INDEX, LOG):
                try:
                    doc = OKFDocument.parse(child.read_text(encoding="utf-8"))
                except OKFDocumentError as e:
                    log.warning("skipping %s: %s", child_rel, e)
                    continue
                folder.notes.append(Note(child, child_rel, doc.frontmatter, doc.body, folder))
        return folder

    def read(self, rel: str) -> Note:
        """The note or raw source at a bundle path; InputError for anything that is not one."""
        root = self.root.resolve()
        path = (root / rel.strip().lstrip("/")).resolve()
        if not path.is_relative_to(root) or path.suffix != ".md" or path.name in (INDEX, LOG):
            raise InputError(f"not a wiki document: {rel}")
        if not path.is_file():
            raise FileNotFoundError(rel)
        doc = OKFDocument.parse(path.read_text(encoding="utf-8"))
        return Note(path, path.relative_to(root).as_posix(), doc.frontmatter, doc.body)

    # -- writing --------------------------------------------------------------

    def create_folder(self, parent: Folder, name: str, description: str, mark: str = "") -> tuple[Folder, bool]:
        """Create `parent/name` and list it in the parent's index; (folder, created).

        A sibling with the same slug is reused instead of duplicated. `mark` ends the log line (", by plan").
        """
        name = slugify(name, max_len=40, fallback="topics")
        if parent.is_root and name == RAW:
            name = "raw-topics"
        if existing := parent.subfolder(name):
            return existing, False
        rel = f"{parent.rel}/{name}" if parent.rel else name
        folder = Folder(parent.path / name, rel, " ".join(description.split()), parent=parent)
        folder.path.mkdir(parents=True, exist_ok=True)
        parent.subfolders.append(folder)
        self.write_index(folder)
        self.write_index(parent)
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
    ) -> Note:
        slug = self.unique_slug(folder, slugify(title))
        path = folder.path / f"{slug}.md"
        rel = f"{folder.rel}/{slug}.md" if folder.rel else f"{slug}.md"
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
        note = Note(path, rel, frontmatter, body.rstrip() + "\n", folder)
        self._save(note)
        folder.notes.append(note)
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
        note.body = join_see_also(body, split_see_also(note.body)[1])
        fm["generated"] = {"by": self.actor, "at": now_iso(), "body_hash": body_hash(split_see_also(note.body)[0])}
        self._save(note)
        if note.folder is not None:
            self.write_index(note.folder)
        return note

    def archive_note(self, note: Note) -> str:
        """Keep a copy of a note as it is, in the hidden archive; returns its bundle path ("/.archive/…").

        `load()` skips the folder, so no step sees the copy. No index or log entry is written.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        base = f"{ARCHIVE_DIR}/{note.rel.removesuffix('.md')}--{stamp}"
        rel = next(r for n in range(1, 1000) if not (self.root / (r := f"{base}{f'-{n}' if n > 1 else ''}.md")).exists())
        fm = {**note.frontmatter, "status": "archived", "archived_from": f"/{note.rel}", "archived_at": now_iso()}
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, OKFDocument(fm, note.body).serialize())
        return f"/{rel}"

    def link(self, a: Note, b: Note) -> bool:
        """Add a See-also entry in both notes; False if they were already linked."""
        changed = self._add_see_also(a, b)
        changed = self._add_see_also(b, a) or changed
        return changed

    def _add_see_also(self, note: Note, target: Note) -> bool:
        main, entries = split_see_also(note.body)
        link = rel_link(note_dir(note.rel), target.rel)
        if any(f"]({link})" in e for e in entries) or note is target:
            return False
        entries.append(_entry(target.title, link, target.summary))
        note.body = join_see_also(main, entries)
        self._save(note)
        return True

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
        write_atomic(self.root / RAW / f"{slug}.md", OKFDocument(fm, text).serialize())
        return f"/{RAW}/{slug}.md"

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
                changed = True
        record = self.find_origin(key)
        if record is not None and changed:
            self.append_log("Source removed", "deleted at its source", record.raw.lstrip("/"), key)
        return record

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
        write_atomic(folder.path / INDEX, "\n".join(parts))

    def append_log(self, verb: str, text: str, link: str | None = None, link_title: str | None = None) -> None:
        """Newest first under a `## YYYY-MM-DD` heading (OKF §9)."""
        path = self.root / LOG
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# Wiki log"]
        entry = f"* **{verb}**: " + " - ".join(p for p in (f"[{link_title or link}]({link})" if link else "", text) if p)
        today = f"## {datetime.now(UTC).date().isoformat()}"
        first = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
        if first is not None and lines[first] == today:
            lines.insert(first + 2 if lines[first + 1 : first + 2] == [""] else first + 1, entry)
        else:
            at = first if first is not None else len(lines)
            lines[at:at] = [today, "", entry, ""]
            if at > 0 and lines[at - 1] != "":
                lines.insert(at, "")
        write_atomic(path, "\n".join(lines).rstrip() + "\n")

    # -- moving and describing ------------------------------------------------

    def move_note(self, rel: str, folder_rel: str) -> Note:
        """File a note in another folder, keeping every link to and from it valid; InputError when it cannot be.

        The new file is written first, then the links of the other notes and the archived copies are pointed at it,
        and the old file goes last, so a failure half way never loses the note.
        """
        root = self.load()
        old = rel.strip().lstrip("/")
        note = next((n for n in root.all_notes() if n.rel == old), None)
        if note is None or note.folder is None:
            raise InputError(f"no note at /{old}: only notes of the tree can be moved, not the index, log, sources or archive")
        target = root.find(folder_rel)
        if target is None:
            raise InputError(f"no folder at /{folder_rel.strip().strip('/')}")
        if target is note.folder:
            raise InputError(f"/{old} is already in {target.label}")
        slug = self.unique_slug(target, note.slug)
        new = f"{target.rel}/{slug}.md" if target.rel else f"{slug}.md"

        def remap(path: str) -> str:
            return new if path == old else path

        moved = Note(target.path / f"{slug}.md", new, dict(note.frontmatter), note.body, target)
        self._relink(moved, old, new, remap)
        self._save(moved)  # the new file first
        for other in root.all_notes():
            if other is not note:
                self._relink(other, other.rel, other.rel, remap)
        self._rehome_archive(old, new)
        note.path.unlink()
        note.folder.notes.remove(note)
        target.notes.append(moved)
        self.write_index(note.folder)
        self.write_index(target)
        self.append_log("Moved", f"from /{old} to /{new}", new, moved.title)
        return moved

    def _relink(self, note: Note, from_rel: str, to_rel: str, remap: Callable[[str], str]) -> None:
        """Point the links in a note's body through `remap` (it is written at `to_rel`); saved if they changed."""
        body = rewrite_links(note.body, from_rel, to_rel, remap)
        if body == note.body:
            return
        gen = note.frontmatter.get("generated")
        if isinstance(gen, dict) and gen.get("body_hash") == body_hash(split_see_also(note.body)[0]):
            gen["body_hash"] = body_hash(split_see_also(body)[0])  # still written by code alone: keep it provable
        note.body = body
        if note.path.exists():
            self._save(note)

    def _archived(self) -> Iterator[tuple[Path, OKFDocument, str]]:
        """Every archived copy that can be read: (path, document, the bundle path it was archived from, "" if none)."""
        for path in sorted((self.root / ARCHIVE_DIR).rglob("*.md")):
            try:
                doc = OKFDocument.parse(path.read_text(encoding="utf-8"))
            except (OKFDocumentError, UnicodeDecodeError):
                continue
            was = doc.frontmatter.get("archived_from")
            yield path, doc, was.lstrip("/") if isinstance(was, str) else ""

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

    def clear(self) -> Deleted:
        """Empty the wiki: every note, folder (with all it holds), raw copy and archived version go, and the log starts
        again. The sync cursors go too, so the next `sync` files every document again. Other files of the top level stay.
        """
        root = self.load()
        deleted = self._delete(root, root.all_notes(), list(root.subfolders))
        for raw in sorted((self.root / RAW).glob("*.md")):
            raw.unlink()
            deleted.sources.append(f"/{RAW}/{raw.name}")
        deleted.sources = list(dict.fromkeys(deleted.sources))
        archive = self.root / ARCHIVE_DIR
        deleted.archived += [f"/{p.relative_to(self.root).as_posix()}" for p in sorted(archive.rglob("*.md"))]
        deleted.archived = list(dict.fromkeys(deleted.archived))
        shutil.rmtree(archive, ignore_errors=True)
        shutil.rmtree(self.root / SYNC_DIR, ignore_errors=True)
        write_atomic(self.root / LOG, "# Wiki log\n")
        self.append_log("Emptied the wiki", f"{len(deleted.notes)} notes and {len(deleted.folders)} folders deleted")
        return deleted

    def _delete(self, root: Folder, notes: list[Note], folders: list[Folder]) -> Deleted:
        """Remove `notes` and `folders` (with all they hold), and every link to them from the notes that stay."""
        gone_notes = {n.rel for n in notes}
        gone_dirs = [f.rel for f in folders]

        def gone(target: str) -> bool:
            return target in gone_notes or any(target == d or target.startswith(f"{d}/") for d in gone_dirs)

        kept = [n for n in root.all_notes() if n.rel not in gone_notes]
        deleted = Deleted(notes=[n.rel for n in notes], folders=[f.label for top in folders for f in top.walk()])
        for note in kept:
            body = drop_links(note.body, note.rel, gone)
            if body != note.body:
                note.body = body
                self._save(note)
                deleted.unlinked.append(note.rel)
        touched: list[Folder] = []
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
        for path, _, was in self._archived():  # the archived versions of what went
            if was and gone(was):
                path.unlink()
                deleted.archived.append(f"/{path.relative_to(self.root).as_posix()}")
        cited = {r for n in kept for r in _raw_sources(n)}
        for resource in dict.fromkeys(r for n in notes for r in _raw_sources(n)):
            path = (self.root / resource.lstrip("/")).resolve()
            if resource not in cited and path.parent == (self.root / RAW).resolve() and path.is_file():
                path.unlink()
                deleted.sources.append(resource)
        return deleted

    def unique_slug(self, folder: Folder, slug: str) -> str:
        candidate, n = slug, 2
        while (folder.path / f"{candidate}.md").exists():
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    def _save(self, note: Note) -> None:
        write_atomic(note.path, OKFDocument(note.frontmatter, note.body).serialize())


def _raw_sources(note: Note) -> list[str]:
    """The raw copies a note cites ("/raw/…md")."""
    sources = note.frontmatter.get("sources")
    return [
        str(s["resource"])
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and str(s.get("resource", "")).startswith(f"/{RAW}/")
    ]


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
