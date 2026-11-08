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
CLOSED = ".closed"  # the file that marks a folder closed to automatic filing (hidden, so the OKF layout stays as it is)
NOTE_TYPE, SOURCE_TYPE = "Note", "Source"
SEE_ALSO = "# See also"
OKF_VERSION = "0.2"

_ENTRY = re.compile(r"^[*-]\s+\[(?P<title>[^\]]*)\]\((?P<link>[^)\s]+)\)\s*(?:-\s*(?P<text>.*))?$")
_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<link>[^)\s]+)\)")
_MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<link>[^)\s#:]+\.md(?:#[^)\s]*)?)\)")
_WIKI_LINK = re.compile(r"!?\[\[(?P<name>[^\]|]+)(?:\|(?P<label>[^\]]*))?\]\]")
# a fenced block (``` or ~~~, to its closing fence or the end) or an inline code span (not across a blank line)
CODE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,}).*?(?:^ {0,3}(?P=fence)[`~]*[ \t]*$|\Z)"
    r"|(?P<ticks>`+)(?!`)(?:[^\n]|\n(?!\s*\n))*?(?<!`)(?P=ticks)(?!`)",
    re.MULTILINE | re.DOTALL,
)


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
    # the file as the store last read or wrote it (modification time, size, inode): None for a note not on disk yet.
    # The store writes a note only if its file still has this stamp (see `WikiStore._save`)
    stamp: tuple[int, int, int] | None = field(default=None, repr=False)

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title") or self.slug)

    @property
    def id(self) -> str | None:
        """The stable id a note gets when it is created (never in a link); None for a note written before ids."""
        value = self.frontmatter.get("id")
        return str(value) if value else None

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
    routable: bool = True  # False: the librarian files nothing here by itself (a person or the code still can); see `is_routable`

    @property
    def name(self) -> str:
        return self.path.name if self.rel else "/"

    @property
    def is_routable(self) -> bool:
        """Whether automatic filing may enter this folder: it and every folder above it are open."""
        return all(f.routable for f in self.ancestors())

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


def area_of(rel: str) -> str:
    """The top-level folder of a bundle path ("finance/pricing.md" and "finance/a/b.md" are in "finance"; "" for a path at the root)."""
    return rel.split("/", 1)[0] if "/" in rel else ""


def within_area(a: Note, b: Note) -> bool:
    """Whether two notes may be linked by code: they are in the same top-level folder, and it is not closed."""
    area = a.folder
    while area is not None and area.parent is not None and area.parent.parent is not None:
        area = area.parent
    return area_of(a.rel) == area_of(b.rel) and (area is None or area.routable)


def _held(value: Any, wanted: Any) -> bool:
    """A field holds `wanted` when it is equal to it as text, or, for a list, when one of its items is."""
    if isinstance(value, (list, tuple, set)):
        return any(str(item) == str(wanted) for item in value)
    return str(value) == str(wanted)


def matching(root: Folder, where: dict[str, Any]) -> Folder:
    """A copy of the tree with only the notes whose `fields` hold every key of `where` (compared as strings; a field that
    is a list holds a value when one of its items is equal to it).

    The folders are new and the notes the same objects, so the tree it is made from, which a session shares, is not touched.
    A folder with no such note, itself or below, is left out.
    """

    def keep(note: Note) -> bool:
        fields = note.frontmatter.get("fields")
        return isinstance(fields, dict) and all(k in fields and _held(fields[k], v) for k, v in where.items())

    def copy(folder: Folder, parent: Folder | None) -> Folder:
        out = Folder(folder.path, folder.rel, folder.description, parent=parent, routable=folder.routable)
        out.notes = [n for n in folder.notes if keep(n)]
        out.subfolders = [s for s in (copy(sub, out) for sub in folder.subfolders) if s.notes or s.subfolders]
        return out

    return copy(root, None)


def split_see_also(body: str) -> tuple[str, list[str]]:
    """(body before "# See also", its list lines).

    The entries are the list lines right after the last "# See also" heading (blank lines between them allowed).
    Whatever follows the first other line (a paragraph or section added by hand) goes back into the main part, so
    rewriting the note keeps it, above the one See-also section.
    """
    lines = body.rstrip("\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == SEE_ALSO:
            entries: list[str] = []
            end = len(lines)
            for k in range(i + 1, len(lines)):
                if _ENTRY.match(lines[k].strip()):
                    entries.append(lines[k])
                elif lines[k].strip():
                    end = k
                    break
            main = "\n".join(lines[:i]).rstrip()
            tail = "\n".join(lines[end:]).strip()
            return (f"{main}\n\n{tail}" if main else tail) if tail else main, entries
    return body.rstrip(), []


def see_also_targets(body: str, from_rel: str) -> list[str]:
    """The bundle paths the See-also entries of the document at `from_rel` link to."""
    targets = [link_target(from_rel, m["link"]) for e in split_see_also(body)[1] if (m := _ENTRY.match(e.strip()))]
    return [t for t in targets if t is not None]


def drop_see_also(body: str, from_rel: str, gone: Callable[[str], bool]) -> str:
    """`body` without the See-also entries that link to a path `gone` says; inline links stay."""
    main, entries = split_see_also(body)
    kept = [e for e in entries if not ((m := _ENTRY.match(e.strip())) and (t := link_target(from_rel, m["link"])) is not None and gone(t))]
    return body if kept == entries else join_see_also(main, kept)


def join_see_also(main: str, entries: list[str]) -> str:
    main = main.rstrip()
    if not entries:
        return main + "\n"
    return f"{main}\n\n{SEE_ALSO}\n\n" + "\n".join(entries) + "\n"


def refresh_entries(body: str, from_rel: str, target: str, entry: Callable[[str], str]) -> str:
    """`body` of the document at `from_rel` with each See-also entry that links to `target` replaced by `entry(link)`.

    The rest of the body stays as it is, byte for byte.
    """
    for old in split_see_also(body)[1]:
        m = _ENTRY.match(old.strip())
        if m and link_target(from_rel, m["link"]) == target:
            body = body.replace(old, entry(m["link"]))
    return body


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


def rewrite_links(body: str, from_rel: str, to_rel: str, remap: Callable[[str], str]) -> str:
    """`body` of the document at `from_rel`, which will live at `to_rel`, with its links kept pointing where they did.

    A link to a path that `remap` changes follows it; every relative link is rewritten from the new directory.
    See-also entries and inline links alike; anchors stay. Off-bundle links and anchors alone are left as they are.
    """

    def fix(link: str) -> str:
        target = link_target(from_rel, link)
        if target is None or target.startswith(".."):
            return link
        new = remap(target)
        if new == target and note_dir(from_rel) == note_dir(to_rel):
            return link
        path, sep, anchor = link.partition("#")
        return ("/" + new if path.startswith("/") else rel_link(note_dir(to_rel), new)) + sep + anchor

    main, entries = split_see_also(body)

    def fix_entry(entry: str) -> str:
        m = _ENTRY.match(entry.strip())
        if not m:
            return entry
        line, link = entry.strip(), fix(m["link"])
        return entry if link == m["link"] else line[: m.start("link")] + link + line[m.end("link") :]

    kept = [fix_entry(e) for e in entries]
    text = _LINK.sub(lambda m: m[0][: m.start("link") - m.start()] + fix(m["link"]) + ")", main)
    return body if kept == entries and text == main else join_see_also(text, kept)


def dead_links_to_text(body: str, from_rel: str, exists: Callable[[str], bool]) -> str:
    """`body` of the note at `from_rel` with the links that lead nowhere written as plain text; code is left as it is.

    "[text](x.md)" whose file is not in the bundle (`exists` is asked for a path inside it) becomes "text (x.md)", or
    "x.md" when the text is the same; "[[name]]", "[[name|label]]" and "![[name]]" become the name or the label.
    """

    def md(m: re.Match[str]) -> str:
        target = link_target(from_rel, m["link"])
        if target is not None and not target.startswith("..") and exists(target):
            return m[0]
        return m["link"] if m["text"] == m["link"] else f"{m['text']} ({m['link']})"

    def convert(text: str, pattern: re.Pattern[str], fix: Callable[[re.Match[str]], str]) -> str:
        """Fix the matches of `pattern` unless one lies wholly in code: code inside a link's text does not protect it."""
        spans = [(c.start(), c.end()) for c in CODE.finditer(text)]
        return pattern.sub(lambda m: m[0] if any(a <= m.start() and m.end() <= b for a, b in spans) else fix(m), text)

    body = convert(body, _WIKI_LINK, lambda m: m["label"] or m["name"])
    return convert(body, _MD_LINK, md)


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
