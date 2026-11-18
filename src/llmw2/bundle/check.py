# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deterministic lint of a wiki: OKF conformance, index drift, broken links, log format. No model calls."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import itertools
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmw2.bundle import journal, txn
from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.logfile import TITLE
from llmw2.bundle.tree import CODE, INDEX, LOG, RAW, SEE_ALSO, parse_index, see_also_targets, subfolder_of

_LINK = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
_DAY = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")
_SEE_ALSO_HEADING = re.compile(r"^#{2,6}[ \t]+see also[ \t]*$", re.IGNORECASE | re.MULTILINE)
_LOG_LINK = re.compile(r"\]\(([^)\s]+)\)")


ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Problem:
    """What `check` found. An `error` is a wiki that is wrong; a `warning` is a mark of its history (a clock that jumped, two copies
    merged): `llmwiki2 check` exits with 1 for errors only."""

    path: str
    message: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def check(root: Path) -> list[Problem]:
    """Every problem of the bundle, one pass over its documents. Errors: a wrong wiki (broken links, index drift, a raw copy no note
    cites, a file an interrupted write left, what a restore left alone). Warnings: what a wiki's history leaves (a note with no
    line in the log, a See-also link written on one side only, log days out of order, which a clock that jumped or two merged
    copies make): they are reported, and `llmwiki2 check` does not fail for them."""
    root = Path(root)
    problems: list[Problem] = []
    ids: dict[str, str] = {}  # note id -> the first note of the tree that has it
    notes: dict[str, OKFDocument] = {}  # the notes of the tree, by path
    raws: list[str] = []
    unreadable = False
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.name == INDEX:
            problems += _check_index(root, path, rel)
        elif path.name == LOG:
            problems += _check_log(path, rel)
        else:
            is_raw = rel.startswith(f"{RAW}/")
            found, doc = _check_doc(root, path, rel, ids, links=not is_raw)
            problems += found
            if is_raw:
                raws.append(rel)
            elif doc is not None:
                notes[rel] = doc
            else:
                unreadable = True
    problems += _check_raws(root, raws, notes) if not unreadable else []  # a note that cannot be read may cite any raw copy
    problems += _check_see_also(notes)
    problems += _check_logged(root, notes)
    problems += _check_leftovers(root)
    for folder in [root, *sorted(p for p in root.rglob("*") if p.is_dir())]:
        rel_parts = folder.relative_to(root).parts
        if any(p.startswith(".") for p in rel_parts) or (rel_parts and rel_parts[0] == RAW):
            continue
        if not (folder / INDEX).exists():
            problems.append(Problem(folder.relative_to(root).as_posix() or ".", "folder has no index.md"))
    for rel, why in txn.conflicts(root):  # what the restore after an interrupted filing left alone: someone changed it meanwhile
        problems.append(Problem(rel, f"left as it is by the restore after an interrupted filing, check it by hand: {why}"))
    if txn.rebuild_interrupted(root):
        problems.append(Problem(txn.REBUILD_FILE, "a rebuild did not finish (it keeps no undo log for each document): run it again"))
    if (root / txn.JOURNAL_DIR).is_dir():
        problems += [Problem(txn.JOURNAL_DIR, p.text, p.severity) for p in journal.Journal(root, writable=False, shared_hint=False).check()]
    return problems


def _check_doc(root: Path, path: Path, rel: str, ids: dict[str, str], *, links: bool = True) -> tuple[list[Problem], OKFDocument | None]:
    """Frontmatter and `type`; relative links too, except in raw/ copies, which are never edited.

    A note's `id` is optional (older wikis have none), but two notes of the tree may not share one.
    """
    try:
        doc = OKFDocument.read(path)
        doc.validate()
    except OKFDocumentError as e:
        return [Problem(rel, str(e))], None
    if not links:
        return [], doc
    problems = [
        Problem(rel, f"broken link to {link}")
        for link in _LINK.findall(CODE.sub(" ", doc.body))
        if "://" not in link and not _resolve(root, path, link).exists()
    ]
    if (note_id := doc.frontmatter.get("id")) is not None:
        first = ids.setdefault(str(note_id), rel)
        if first != rel:
            problems.append(Problem(rel, f"duplicate id {note_id} (also in {first})"))
    return problems, doc


def _cited(frontmatter: dict[str, Any]) -> list[str]:
    """The raw copies ("raw/x.md") a note, or an archived version of one, cites."""
    sources = frontmatter.get("sources")
    return [
        str(s["resource"]).lstrip("/")
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and str(s.get("resource", "")).startswith(f"/{RAW}/")
    ]


def _check_raws(root: Path, raws: list[str], notes: dict[str, OKFDocument]) -> list[Problem]:
    """A raw copy that no note, and no archived version of a note, cites: a filing that was interrupted after saving it."""
    if not raws:
        return []
    cited = {r for doc in notes.values() for r in _cited(doc.frontmatter)}
    archive = root / ".archive"
    if archive.is_dir():
        for path in archive.rglob("*.md"):
            try:
                cited.update(_cited(OKFDocument.read(path).frontmatter))
            except OKFDocumentError:
                continue
    return [Problem(rel, "no note cites this raw copy") for rel in raws if rel not in cited]


def _check_see_also(notes: dict[str, OKFDocument]) -> list[Problem]:
    """A See-also entry that the note it points to does not have back. The code writes both sides, so a wiki that has one-sided
    entries was written another way (an older version, by hand): a warning. A section a person wrote under a heading of another
    level ("## See also") counts as the section the code writes ("# See also")."""
    targets = {rel: set(see_also_targets(_SEE_ALSO_HEADING.sub(SEE_ALSO, doc.body), rel)) for rel, doc in notes.items()}
    return [
        Problem(rel, f"See also lists {other}, which does not list it back", WARNING)
        for rel, found in targets.items()
        for other in sorted(found)
        if other in notes and other != rel and rel not in targets[other]
    ]


def _check_logged(root: Path, notes: dict[str, OKFDocument]) -> list[Problem]:
    """A note whose file name no link of `log.md` names (the name, not the path: a move keeps it): a note written by hand, or
    before the log. A warning: it says something about the history, not about the wiki. A log that names no note at all is not
    compared."""
    path = root / LOG
    if not path.is_file():
        return []
    text = path.read_bytes().decode("utf-8", errors="replace")
    named = {link.rpartition("/")[2] for link in _LOG_LINK.findall(text) if link.endswith(".md")}
    if not named:  # a log that names no note at all says nothing about these: no filing was ever logged here
        return []
    return [Problem(rel, "no log line names it", WARNING) for rel in notes if rel.rpartition("/")[2] not in named]


def _check_leftovers(root: Path) -> list[Problem]:
    """The temporary file of a write that was cut (`.<name>.<pid>.<thread>.tmp`), anywhere in the bundle."""
    found: list[Problem] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        found += [
            Problem((Path(folder) / name).relative_to(root).as_posix(), "left by an interrupted write")
            for name in sorted(files)
            if name.endswith(".tmp")
        ]
    return found


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
    """The headings are dates (an error if not). A log of 1.0.5 or earlier (title "# Wiki log") has the newest day first and is an
    error if it does not. A log in the order of arrival (see `llmw2.bundle.logfile`) has the days of its headings as the clock
    gave them: days that go back, or come twice, are warnings (a clock that jumped, two copies merged), never errors."""
    text, problems = _text(path, rel)
    lines = text.splitlines()
    days = [m[1] for line in lines if (m := _DAY.match(line))]
    bad = [line for line in lines if line.startswith("## ") and not _DAY.match(line)]
    problems += [Problem(rel, f"log heading is not ## YYYY-MM-DD: {line!r}") for line in bad]
    if lines and lines[0].startswith(TITLE):
        back = [(a, b) for a, b in itertools.pairwise(days) if b < a]
        if back:
            first = f"{back[0][1]} comes after {back[0][0]}"
            problems.append(Problem(rel, f"log days are out of order: {first} ({len(back)} time(s))", WARNING))
        again = sorted(d for d, n in Counter(days).items() if n > 1)
        if again:
            problems.append(Problem(rel, f"log has the same day under more than one heading: {', '.join(again[:3])}", WARNING))
    elif days != sorted(days, reverse=True):
        problems.append(Problem(rel, "log days are not newest first"))
    return problems


def _resolve(root: Path, from_file: Path, link: str) -> Path:
    return (root / link.lstrip("/")) if link.startswith("/") else (from_file.parent / link)
