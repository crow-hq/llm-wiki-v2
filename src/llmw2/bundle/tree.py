# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The wiki as a tree in memory: notes, folders, their links, and the OKF index lines they are read from.

Pure: nothing here touches the disk, `store.WikiStore` reads and writes it.
"""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmw2.bundle.document import OKFDocument, OKFDocumentError

INDEX, LOG, RAW = "index.md", "log.md", "raw"
NOTE_TYPE, SOURCE_TYPE = "Note", "Source"
SEE_ALSO = "# See also"
OKF_VERSION = "0.2"

_ENTRY = re.compile(r"^[*-]\s+\[(?P<title>[^\]]*)\]\((?P<link>[^)\s]+)\)\s*(?:-\s*(?P<text>.*))?$")
_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<link>[^)\s]+)\)")


def slugify(text: str, *, max_len: int = 60, fallback: str = "note") -> str:
    """Lowercase ASCII words joined by '-', cut at a word boundary."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > max_len:
        cut = slug[:max_len]
        slug = (cut if slug[max_len] == "-" else cut.rsplit("-", 1)[0] or cut).strip("-")
    if slug in {"index", "log"}:
        slug = f"{slug}-{fallback}"
    return slug or fallback


def rel_link(from_dir: str, to_rel: str) -> str:
    """Relative link from a bundle directory ("" = root) to a bundle path."""
    return Path(os.path.relpath(to_rel, from_dir or ".")).as_posix()


def note_dir(rel: str) -> str:
    """Bundle directory of a bundle path ("" = root)."""
    parent = Path(rel).parent.as_posix()
    return "" if parent == "." else parent


@dataclass(eq=False)
class Note:
    path: Path
    rel: str  # bundle-relative, e.g. "finance/revenue-recognition.md"
    frontmatter: dict[str, Any]
    body: str
    folder: Folder | None = field(default=None, repr=False)

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title") or self.slug)

    @property
    def summary(self) -> str:
        return str(self.frontmatter.get("description") or "")

    @property
    def tags(self) -> list[str]:
        tags = self.frontmatter.get("tags") or []
        return [str(t) for t in tags] if isinstance(tags, list) else [str(tags)]

    def main_body(self) -> str:
        """The body without the code-managed See-also section."""
        return split_see_also(self.body)[0]

    def full_text(self) -> str:
        return f"Title: {self.title}\nSummary: {self.summary}\n\n{self.main_body()}".strip()

    def compact(self, max_chars: int) -> str:
        return self.full_text()[:max_chars]


@dataclass(eq=False)
class Folder:
    path: Path
    rel: str  # "" for the root, else e.g. "finance/pricing"
    description: str = ""
    subfolders: list[Folder] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    parent: Folder | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.path.name if self.rel else "/"

    @property
    def label(self) -> str:
        """Display path: "/" or "/finance/pricing"."""
        return f"/{self.rel}"

    @property
    def is_root(self) -> bool:
        return not self.rel

    @property
    def depth(self) -> int:
        return self.rel.count("/") + 1 if self.rel else 0

    def walk(self) -> Iterator[Folder]:
        yield self
        for sub in self.subfolders:
            yield from sub.walk()

    def find(self, rel: str) -> Folder | None:
        rel = rel.strip("/")
        return next((f for f in self.walk() if f.rel == rel), None)

    def subfolder(self, name: str) -> Folder | None:
        return next((f for f in self.subfolders if f.name == name), None)

    def note(self, slug: str) -> Note | None:
        return next((n for n in self.notes if n.slug == slug), None)

    def ancestors(self) -> list[Folder]:
        """Root first, self last."""
        chain: list[Folder] = []
        folder: Folder | None = self
        while folder is not None:
            chain.append(folder)
            folder = folder.parent
        return chain[::-1]

    def all_notes(self) -> list[Note]:
        return [n for f in self.walk() for n in f.notes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.label,
            "description": self.description,
            "notes": [{"path": n.rel, "title": n.title, "summary": n.summary} for n in self.notes],
            "subfolders": [f.to_dict() for f in self.subfolders],
        }


def split_see_also(body: str) -> tuple[str, list[str]]:
    """(body before "# See also", its list lines)."""
    lines = body.rstrip("\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == SEE_ALSO:
            entries = [line for line in lines[i + 1 :] if _ENTRY.match(line.strip())]
            return "\n".join(lines[:i]).rstrip(), entries
    return body.rstrip(), []


def join_see_also(main: str, entries: list[str]) -> str:
    main = main.rstrip()
    if not entries:
        return main + "\n"
    return f"{main}\n\n{SEE_ALSO}\n\n" + "\n".join(entries) + "\n"


def link_target(from_rel: str, link: str) -> str | None:
    """The bundle path a link in the document at `from_rel` points to ("/x.md" from the root); None off the bundle."""
    link = link.split("#")[0]
    if not link or ":" in link:  # a web or mail link, or only an anchor
        return None
    return posixpath.normpath(link.lstrip("/") if link.startswith("/") else posixpath.join(note_dir(from_rel), link))


def drop_links(body: str, from_rel: str, gone: Callable[[str], bool]) -> str:
    """`body` without its links to deleted paths: their See-also entries go, inline links keep only their text."""

    def dead(link: str) -> bool:
        target = link_target(from_rel, link)
        return target is not None and gone(target)

    main, entries = split_see_also(body)
    kept = [e for e in entries if not ((m := _ENTRY.match(e.strip())) and dead(m["link"]))]
    text = _LINK.sub(lambda m: m["text"] if dead(m["link"]) else m[0], main)
    return body if kept == entries and text == main else join_see_also(text, kept)


def graph(root: Folder) -> dict[str, list[dict[str, Any]]]:
    """The wiki as a graph: folders and notes as nodes; containment and See-also links as edges."""
    notes = {n.rel for n in root.all_notes()}
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for folder in root.walk():
        area = folder.rel.split("/")[0]
        nodes.append({
            "id": folder.label, "kind": "folder", "label": folder.name if folder.rel else "brain",
            "description": folder.description, "area": area, "depth": folder.depth, "notes": len(folder.all_notes()),
        })
        if folder.parent is not None:
            links.append({"source": folder.parent.label, "target": folder.label, "kind": "contains"})
        for note in folder.notes:
            nodes.append({"id": note.rel, "kind": "note", "label": note.title, "summary": note.summary, "area": area})
            links.append({"source": folder.label, "target": note.rel, "kind": "contains"})
            for entry in split_see_also(note.body)[1]:
                if not (m := _ENTRY.match(entry.strip())):
                    continue
                target = posixpath.normpath(posixpath.join(note_dir(note.rel), m["link"]))
                pair = (min(note.rel, target), max(note.rel, target))
                if target in notes and target != note.rel and pair not in seen:
                    seen.add(pair)
                    links.append({"source": note.rel, "target": target, "kind": "see_also"})
    return {"nodes": nodes, "links": links}


def parse_index(text: str) -> list[tuple[str, str, str]]:
    """Entries (title, link, text) of an index.md, links normalised ("./a/index.md" → "a/index.md")."""
    try:
        body = OKFDocument.parse(text).body
    except OKFDocumentError:
        body = text  # a broken frontmatter block must not hide the entries below it
    entries = []
    for line in body.splitlines():
        if m := _ENTRY.match(line.strip()):
            link = m["link"]
            if "://" not in link and not link.startswith("/"):
                link = posixpath.normpath(link) + ("/" if link.endswith("/") else "")
            entries.append((m["title"], link, (m["text"] or "").strip()))
    return entries


def subfolder_of(link: str) -> str | None:
    """The subfolder an index entry points to ("sub/index.md" or "sub/"), else None."""
    if link.endswith(f"/{INDEX}") or link.endswith("/"):
        return link.split("/")[0]
    return None
