# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The wiki on disk, as an OKF v0.2 bundle.

    wiki/
      index.md        root index (frontmatter: okf_version "0.2")
      log.md          newest-first change log
      raw/            immutable copies of every ingested source (type: Source)
      <note>.md       type: Note, title, description (the one-line summary), tags, generated, sources
      <folder>/
        index.md      "* [sub](sub/index.md) - description" and "* [Title](note.md) - summary"

A folder's one-line description lives in its parent's index.md entry, so the
tree is fully described by standard OKF index files. Links are relative.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from okf_wiki.bundle.document import OKFDocument, OKFDocumentError
from okf_wiki.bundle.tree import (
    INDEX,
    LOG,
    NOTE_TYPE,
    OKF_VERSION,
    RAW,
    SOURCE_TYPE,
    Folder,
    Note,
    join_see_also,
    note_dir,
    parse_index,
    rel_link,
    slugify,
    split_see_also,
    subfolder_of,
)
from okf_wiki.errors import InputError

log = logging.getLogger(__name__)

def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(title: str, link: str, text: str) -> str:
    """One OKF index / See-also line: `* [Title](link) - text`."""
    title = " ".join(title.split()).replace("[", "(").replace("]", ")")
    text = " ".join(text.split())
    return f"* [{title}]({link})" + (f" - {text}" if text else "")


class WikiStore:
    """Reads the folder tree and writes notes, folders, links, index.md and log.md."""

    def __init__(self, root: Path, *, actor: str) -> None:
        self.root = Path(root)
        self.actor = actor

    # -- reading --------------------------------------------------------------

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / RAW).mkdir(exist_ok=True)
        if not (self.root / INDEX).exists():
            self.write_index(Folder(self.root, ""))
        if not (self.root / LOG).exists():
            _write(self.root / LOG, "# Wiki log\n")

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the bundle for writing: one writer at a time across processes (the CLI and a server).

        An advisory flock on the bundle folder itself, so no lock file lands in the wiki.
        """
        try:
            import fcntl
        except ImportError:  # solo: Windows has no flock; there only the server's in-process lock holds
            yield
            return
        fd = os.open(self.root, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the lock

    def load(self) -> Folder:
        if not self.root.is_dir():
            raise FileNotFoundError(f"wiki not found: {self.root} (run `okf-wiki init`)")
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

    def create_folder(self, parent: Folder, name: str, description: str) -> tuple[Folder, bool]:
        """Create `parent/name` and list it in the parent's index; (folder, created).

        A sibling with the same slug is reused instead of duplicated.
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
        self.append_log("Created folder", folder.description, f"{rel}/{INDEX}", folder.label)
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
    ) -> Note:
        slug = self.unique_slug(folder, slugify(title))
        path = folder.path / f"{slug}.md"
        rel = f"{folder.rel}/{slug}.md" if folder.rel else f"{slug}.md"
        frontmatter: dict[str, Any] = {
            "type": NOTE_TYPE,
            "title": title.strip(),
            "description": summary.strip(),
            "tags": tags,
            "generated": {"by": self.actor, "at": now_iso()},
            "sources": [{"id": "s1", **source}],
        }
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
    ) -> Note:
        """Rewrite a note in place, keeping unknown frontmatter keys and its See-also links."""
        fm = note.frontmatter
        fm["title"], fm["description"] = title.strip(), summary.strip()
        fm["tags"] = list(dict.fromkeys([*note.tags, *tags]))
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        sources: list[Any] = fm["sources"] if isinstance(fm.get("sources"), list) else []
        used = {str(s.get("id")) for s in sources if isinstance(s, dict)}
        new_id = next(f"s{n}" for n in range(1, len(sources) + 2) if f"s{n}" not in used)
        fm["sources"] = [*sources, {"id": new_id, **source}]
        note.body = join_see_also(body, split_see_also(note.body)[1])
        self._save(note)
        if note.folder is not None:
            self.write_index(note.folder)
        return note

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

    def save_raw(self, text: str, *, title: str, resource: str | None) -> str:
        """Keep an immutable copy of an ingested source; returns its bundle-absolute path ("/raw/…")."""
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = self.unique_slug(Folder(self.root / RAW, RAW), f"{stamp}-{slugify(title, max_len=40)}")
        fm: dict[str, Any] = {"type": SOURCE_TYPE, "title": title}
        if resource:
            fm["resource"] = resource
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        (self.root / RAW).mkdir(exist_ok=True)
        _write(self.root / RAW / f"{slug}.md", OKFDocument(fm, text).serialize())
        return f"/{RAW}/{slug}.md"

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
        _write(folder.path / INDEX, "\n".join(parts))

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
        _write(path, "\n".join(lines).rstrip() + "\n")

    def unique_slug(self, folder: Folder, slug: str) -> str:
        candidate, n = slug, 2
        while (folder.path / f"{candidate}.md").exists():
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    def _save(self, note: Note) -> None:
        _write(note.path, OKFDocument(note.frontmatter, note.body).serialize())


def _write(path: Path, text: str) -> None:
    """Write atomically, so a concurrent reader never sees half a file."""
    # One temporary name per process and thread: two writers never share, and so never clobber, a temp file.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
