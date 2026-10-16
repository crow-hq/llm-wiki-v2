# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic lint of a wiki: OKF conformance, index drift, broken links, log format. No model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.tree import INDEX, LOG, RAW, parse_index, subfolder_of

_LINK = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
_DAY = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def check(root: Path) -> list[Problem]:
    root = Path(root)
    problems: list[Problem] = []
    ids: dict[str, str] = {}  # note id -> the first note of the tree that has it
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.name == INDEX:
            problems += _check_index(root, path, rel)
        elif path.name == LOG:
            problems += _check_log(path, rel)
        else:
            problems += _check_doc(root, path, rel, ids, links=not rel.startswith(f"{RAW}/"))
    for folder in [root, *sorted(p for p in root.rglob("*") if p.is_dir())]:
        rel_parts = folder.relative_to(root).parts
        if any(p.startswith(".") for p in rel_parts) or (rel_parts and rel_parts[0] == RAW):
            continue
        if not (folder / INDEX).exists():
            problems.append(Problem(folder.relative_to(root).as_posix() or ".", "folder has no index.md"))
    return problems


def _check_doc(root: Path, path: Path, rel: str, ids: dict[str, str], *, links: bool = True) -> list[Problem]:
    """Frontmatter and `type`; relative links too, except in raw/ copies, which are never edited.

    A note's `id` is optional (older wikis have none), but two notes of the tree may not share one.
    """
    try:
        doc = OKFDocument.read(path)
        doc.validate()
    except OKFDocumentError as e:
        return [Problem(rel, str(e))]
    if not links:
        return []
    problems = [
        Problem(rel, f"broken link to {link}")
        for link in _LINK.findall(doc.body)
        if "://" not in link and not _resolve(root, path, link).exists()
    ]
    if (note_id := doc.frontmatter.get("id")) is not None:
        first = ids.setdefault(str(note_id), rel)
        if first != rel:
            problems.append(Problem(rel, f"duplicate id {note_id} (also in {first})"))
    return problems


def _text(path: Path, rel: str) -> tuple[str, list[Problem]]:
    """The file as text, with a problem when it is not UTF-8 (its bad bytes replaced, so the rest is still checked)."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), []
    except UnicodeDecodeError as e:
        return data.decode("utf-8", errors="replace"), [Problem(rel, f"not UTF-8 text (byte {e.start}): save it as UTF-8")]


def _check_index(root: Path, path: Path, rel: str) -> list[Problem]:
    folder = path.parent
    text, problems = _text(path, rel)
    if folder != root and text.startswith("---"):
        problems.append(Problem(rel, "only the root index.md may carry frontmatter"))
    else:
        try:
            OKFDocument.parse(text)
        except OKFDocumentError as e:
            problems.append(Problem(rel, str(e)))
    listed: set[str] = set()
    for _, link, description in parse_index(text):
        if not _resolve(root, path, link).exists():
            problems.append(Problem(rel, f"lists missing {link}"))
        if name := subfolder_of(link):
            listed.add(name)
            if not description:
                problems.append(Problem(rel, f"subfolder {link} has no description"))
        else:
            listed.add(link)
    if folder == root and (root / RAW).is_dir():
        listed.add(RAW)
    for child in folder.iterdir():
        if child.name.startswith(".") or child.name in (INDEX, LOG):
            continue
        key = child.name if child.is_dir() else child.name if child.suffix == ".md" else None
        if key and key not in listed:
            problems.append(Problem(rel, f"does not list {child.name}"))
    return problems


def _check_log(path: Path, rel: str) -> list[Problem]:
    text, problems = _text(path, rel)
    lines = text.splitlines()
    days = [m[1] for line in lines if (m := _DAY.match(line))]
    bad = [line for line in lines if line.startswith("## ") and not _DAY.match(line)]
    problems += [Problem(rel, f"log heading is not ## YYYY-MM-DD: {line!r}") for line in bad]
    if days != sorted(days, reverse=True):
        problems.append(Problem(rel, "log days are not newest first"))
    return problems


def _resolve(root: Path, from_file: Path, link: str) -> Path:
    return (root / link.lstrip("/")) if link.startswith("/") else (from_file.parent / link)
