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

"""Deterministic lint of a wiki: OKF conformance, index drift, broken links, log format. No model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from okf_wiki.document import OKFDocument, OKFDocumentError

from okf_wiki.store import INDEX, LOG, RAW, parse_index, subfolder_of

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
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.name == INDEX:
            problems += _check_index(root, path, rel)
        elif path.name == LOG:
            problems += _check_log(path, rel)
        else:
            problems += _check_doc(root, path, rel, links=not rel.startswith(f"{RAW}/"))
    for folder in [root, *sorted(p for p in root.rglob("*") if p.is_dir())]:
        rel_parts = folder.relative_to(root).parts
        if any(p.startswith(".") for p in rel_parts) or (rel_parts and rel_parts[0] == RAW):
            continue
        if not (folder / INDEX).exists():
            problems.append(Problem(folder.relative_to(root).as_posix() or ".", "folder has no index.md"))
    return problems


def _check_doc(root: Path, path: Path, rel: str, *, links: bool = True) -> list[Problem]:
    """Frontmatter and `type`; relative links too, except in raw/ copies, which are never edited."""
    try:
        doc = OKFDocument.parse(path.read_text(encoding="utf-8"))
        doc.validate()
    except OKFDocumentError as e:
        return [Problem(rel, str(e))]
    if not links:
        return []
    return [
        Problem(rel, f"broken link to {link}")
        for link in _LINK.findall(doc.body)
        if "://" not in link and not _resolve(root, path, link).exists()
    ]


def _check_index(root: Path, path: Path, rel: str) -> list[Problem]:
    folder = path.parent
    text = path.read_text(encoding="utf-8")
    problems = []
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
    lines = path.read_text(encoding="utf-8").splitlines()
    days = [m[1] for line in lines if (m := _DAY.match(line))]
    bad = [line for line in lines if line.startswith("## ") and not _DAY.match(line)]
    problems = [Problem(rel, f"log heading is not ## YYYY-MM-DD: {line!r}") for line in bad]
    if days != sorted(days, reverse=True):
        problems.append(Problem(rel, "log days are not newest first"))
    return problems


def _resolve(root: Path, from_file: Path, link: str) -> Path:
    return (root / link.lstrip("/")) if link.startswith("/") else (from_file.parent / link)
