# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

import logging
import os
import posixpath
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from okf_wiki.document import OKFDocument, OKFDocumentError

log = logging.getLogger(__name__)

INDEX, LOG, RAW = "index.md", "log.md", "raw"
NOTE_TYPE, SOURCE_TYPE = "Note", "Source"
SEE_ALSO = "# See also"
OKF_VERSION = "0.2"

_ENTRY = re.compile(r"^[*-]\s+\[(?P<title>[^\]]*)\]\((?P<link>[^)\s]+)\)\s*(?:-\s*(?P<text>.*))?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _entry(title: str, link: str, text: str) -> str:
    """One OKF index / See-also line: `* [Title](link) - text`."""
    title = " ".join(title.split()).replace("[", "(").replace("]", ")")
    text = " ".join(text.split())
    return f"* [{title}]({link})" + (f" - {text}" if text else "")


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
        chain, folder = [], self
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
        """The note or raw source at a bundle path; ValueError for anything that is not one."""
        root = self.root.resolve()
        path = (root / rel.strip().lstrip("/")).resolve()
        if not path.is_relative_to(root) or path.suffix != ".md" or path.name in (INDEX, LOG):
            raise ValueError(f"not a wiki document: {rel}")
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
        sources = fm.get("sources") if isinstance(fm.get("sources"), list) else []
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
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
        today = f"## {datetime.now(timezone.utc).date().isoformat()}"
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
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
