# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which raw copies carry an origin key, and which notes cite them: an index kept in memory.

It is rebuilt from the files and is never the source of truth. Every query first refreshes it by `stat`:
only new or changed files are parsed again, and the ones that vanished are dropped. Store writes are not
reported to it: the refresh sees them, because `write_atomic` replaces the file and so changes its inode.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.origin import Origin
from llmw2.bundle.tree import INDEX, LOG, RAW
from llmw2.errors import InputError

Signature = tuple[int, int, int]  # st_mtime_ns, st_size, st_ino


@dataclass(frozen=True)
class Copy:
    """What the index keeps of a raw copy with an origin."""

    name: str  # file name in raw/
    key: str
    seq: int  # save order among the copies of its key (0 for copies saved without one)
    at: str  # generated.at
    hash: str | None
    version: str | None
    removed: bool


class OriginIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._raws: dict[str, tuple[Signature, Copy | None]] = {}  # None: not a copy with a valid origin
        self._notes: dict[str, tuple[Signature, list[str]]] = {}  # bundle path -> the raw copies it cites

    def copies(self, key: str) -> list[Copy]:
        """The copies carrying this origin key, oldest first."""
        with self._lock:
            self._refresh()
            found = [c for _, c in self._raws.values() if c is not None and c.key == key]
        return sorted(found, key=lambda c: (c.seq, c.at, Path(c.name).stem))

    def citing(self, raws: list[str]) -> dict[str, int]:
        """The notes citing any of `raws` ("/raw/…md"), each with the highest position in `raws` it cites."""
        with self._lock:
            self._refresh()
            newest: dict[str, int] = {}
            for rel, (_, cited) in self._notes.items():
                if positions := [raws.index(r) for r in cited if r in raws]:
                    newest[rel] = max(positions)
        return newest

    def _refresh(self) -> None:
        raws = {e.name: e for e in _scan(self.root / RAW) if e.is_file() and e.name.endswith(".md")}
        _sync(self._raws, raws, _parse_copy)
        _sync(self._notes, dict(_walk(self.root, "")), _parse_note)


def _scan(path: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:  # missing folder: nothing there
        return []


def _walk(path: Path, rel: str) -> list[tuple[str, os.DirEntry[str]]]:
    """The notes under `path` as `WikiStore._load_folder` sees them: (bundle path, entry)."""
    found: list[tuple[str, os.DirEntry[str]]] = []
    for entry in _scan(path):
        if entry.name.startswith(".") or (not rel and entry.name == RAW):
            continue
        child = f"{rel}/{entry.name}" if rel else entry.name
        if entry.is_dir():
            found.extend(_walk(Path(entry.path), child))
        elif entry.name.endswith(".md") and entry.name not in (INDEX, LOG):
            found.append((child, entry))
    return found


def _sync(state: dict[str, Any], seen: dict[str, os.DirEntry[str]], parse: Callable[[str, os.DirEntry[str]], Any]) -> None:
    """Parse again what is new or changed in `state`, forget what is no longer in `seen`."""
    for name in state.keys() - seen.keys():
        del state[name]
    for name, entry in seen.items():
        try:
            st = entry.stat()
        except OSError:  # vanished since the scan
            state.pop(name, None)
            continue
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        if name in state and state[name][0] == sig:
            continue
        try:
            state[name] = (sig, parse(name, entry))
        except OSError:
            state.pop(name, None)


def _parse_copy(name: str, entry: os.DirEntry[str]) -> Copy | None:
    try:
        fm = OKFDocument.parse(Path(entry.path).read_text(encoding="utf-8")).frontmatter
        origin = Origin.from_dict(fm["origin"]) if isinstance(fm.get("origin"), dict) else None
    except (OKFDocumentError, InputError):
        return None
    if origin is None:
        return None
    seq, generated = fm.get("seq"), fm.get("generated")
    return Copy(
        name=name,
        key=origin.key,
        seq=seq if isinstance(seq, int) else 0,
        at=str(generated.get("at", "")) if isinstance(generated, dict) else "",
        hash=str(fm["hash"]) if fm.get("hash") else None,
        version=origin.version or None,
        removed=bool(fm.get("removed")),
    )


def _parse_note(rel: str, entry: os.DirEntry[str]) -> list[str]:
    """The raw copies a note cites ("/raw/…md"); none for a malformed note."""
    try:
        sources = OKFDocument.parse(Path(entry.path).read_text(encoding="utf-8")).frontmatter.get("sources")
    except OKFDocumentError:
        return []
    return [
        str(s["resource"])
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and str(s.get("resource", "")).startswith(f"/{RAW}/")
    ]
