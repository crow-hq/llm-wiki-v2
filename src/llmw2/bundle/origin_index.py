# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which raw copies carry an origin key, and which notes cite them: an index kept in memory.

It is rebuilt from the files and is never the source of truth. Every query first refreshes it by `stat`:
only new or changed files are parsed again, and the ones that vanished are dropped. Store writes are not
reported to it: the refresh sees them, because `write_atomic` replaces the file and so changes its inode.

In a session (a batch, see `WikiStore.session`) a folder whose modification time is the same, and not recent, is not
listed again: the store writes by replacing files, which changes the time of their folder. A raw copy already known is
not looked at again in a session, whatever happens to its file: the store tells (`touch`) when it rewrites one.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.origin import Origin
from llmw2.bundle.tree import INDEX, LOG, RAW
from llmw2.errors import InputError

Signature = tuple[int, int, int]  # st_mtime_ns, st_size, st_ino
RACY_NS = 2_000_000_000  # a folder changed this recently may change again within the same tick: it is listed again


@dataclass(frozen=True)
class Copy:
    """What the index keeps of a raw copy with an origin."""

    name: str  # file name in raw/
    key: str  # "" for a copy saved without an origin
    seq: int  # save order among the copies of its key (0 for copies saved without one)
    at: str  # generated.at
    hash: str | None
    version: str | None
    removed: bool


class _Raws(dict[str, tuple[Signature, Copy | None]]):
    """The raw copies by file name, with the copies of each origin key and the unclaimed ones of each hash kept alongside, so
    a query does not walk them all. Every change of the dict goes through the methods below."""

    def __init__(self) -> None:
        super().__init__()
        self.by_key: dict[str, dict[str, Copy]] = {}  # origin key -> file name -> copy
        self.by_hash: dict[str, dict[str, Copy]] = {}  # text hash -> file name -> copy saved without an origin

    def _drop(self, name: str) -> None:
        if (old := super().get(name)) is not None and (copy := old[1]) is not None:
            index, at = (self.by_key, copy.key) if copy.key else (self.by_hash, copy.hash or "")
            if (same := index.get(at)) is not None:
                same.pop(name, None)
                if not same:
                    del index[at]

    def __setitem__(self, name: str, value: tuple[Signature, Copy | None]) -> None:
        self._drop(name)
        super().__setitem__(name, value)
        if (copy := value[1]) is not None:
            if copy.key:
                self.by_key.setdefault(copy.key, {})[name] = copy
            elif copy.hash:
                self.by_hash.setdefault(copy.hash, {})[name] = copy

    def __delitem__(self, name: str) -> None:
        self._drop(name)
        super().__delitem__(name)

    def pop(self, name: str, *default: Any) -> Any:
        self._drop(name)
        return super().pop(name, *default)

    def clear(self) -> None:
        super().clear()
        self.by_key.clear()
        self.by_hash.clear()


class OriginIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._raws = _Raws()  # None: not a copy with a valid origin
        self._notes: dict[str, tuple[Signature, list[str]]] = {}  # bundle path -> the raw copies it cites
        self.session = False  # set by the store while it holds a batch
        self._trusted: dict[str, int] = {}  # folder path -> its modification time when it was last listed, if not recent
        self._synced: dict[str, int] = {}  # folder path -> its modification time when it was last listed, recent or not
        self._listing: dict[str, tuple[list[str], list[str]]] = {}  # folder path -> its subfolders and notes (bundle paths)

    def raw_time(self) -> int:
        """The modification time of raw/, for `raw_written`."""
        return _mtime(self.root / RAW)

    def raw_written(self, name: str, before: int) -> None:
        """The store saved the new raw copy `name`; `before` is `raw_time` taken before it did. In a session, if the copies
        known were those of raw/ as it was then, this one is added to them and raw/ is not listed again for it: only a
        change by someone else (a different time of the folder) will be."""
        with self._lock:
            raw = self.root / RAW
            key = str(raw)
            if not self.session or self._synced.get(key) != before:
                return
            _update(self._raws, name, _Found(raw / name), _parse_copy)
            self._synced[key] = self._trusted[key] = _mtime(raw)

    def touch(self, name: str) -> None:
        """A raw copy was rewritten by the store: read it again at the next query (in a session, known copies are not)."""
        with self._lock:
            self._raws.pop(name, None)

    def forget(self) -> None:
        """Drop everything kept: the next query looks at all the files."""
        with self._lock:
            self._raws.clear()
            self._notes.clear()
            self._trusted.clear()
            self._synced.clear()
            self._listing.clear()

    def copies(self, key: str) -> list[Copy]:
        """The copies carrying this origin key, oldest first."""
        with self._lock:
            self._refresh()
            found = list(self._raws.by_key.get(key, {}).values())
        return sorted(found, key=lambda c: (c.seq, c.at, Path(c.name).stem))

    def copies_of(self, keys: Iterable[str]) -> dict[str, list[Copy]]:
        """The copies of each of these origin keys, oldest first: `copies` for many keys with one look at the folder."""
        with self._lock:
            self._refresh()
            found = {k: list(self._raws.by_key.get(k, {}).values()) for k in keys}
        return {k: sorted(v, key=lambda c: (c.seq, c.at, Path(c.name).stem)) for k, v in found.items()}

    def keys(self, prefix: str) -> set[str]:
        """The origin keys starting with `prefix` (the sheets of one spreadsheet: "<key>#")."""
        with self._lock:
            self._refresh()
            return {c.key for _, c in self._raws.values() if c is not None and c.key and c.key.startswith(prefix)}

    def unclaimed(self, hash: str) -> list[Copy]:
        """The copies saved without an origin whose text has this hash, oldest first."""
        with self._lock:
            self._refresh()
            found = list(self._raws.by_hash.get(hash, {}).values())
        return sorted(found, key=lambda c: (c.at, Path(c.name).stem))

    def citing(self, raws: list[str]) -> dict[str, int]:
        """The notes citing any of `raws` ("/raw/…md"), each with the highest position in `raws` it cites."""
        if not raws:
            return {}
        with self._lock:
            self._refresh(notes=True)
            newest: dict[str, int] = {}
            for rel, (_, cited) in self._notes.items():
                if positions := [raws.index(r) for r in cited if r in raws]:
                    newest[rel] = max(positions)
        return newest

    def _refresh(self, notes: bool = False) -> None:
        raw = self.root / RAW
        if not (self.session and self._unchanged(raw)):
            listed = _listed(raw)
            raws = {e.name: e for e in _scan(raw) if e.name.endswith(".md") and e.is_file()}
            if self.session:  # a copy is never edited, but by `touch` (below): only the new ones are read
                for name in self._raws.keys() - raws.keys():
                    del self._raws[name]
                for name, entry in raws.items():
                    if name not in self._raws:
                        _update(self._raws, name, entry, _parse_copy)
            else:
                _sync(self._raws, raws, _parse_copy)
            self._remember(raw, listed)
        if notes:  # only `citing` reads the notes: walking the whole tree for the other queries would cost a stat per note
            if self.session:
                self._refresh_notes(self.root, "")
            else:
                _sync(self._notes, dict(_walk(self.root, "")), _parse_note)

    def _unchanged(self, path: Path) -> bool:
        """Whether the folder is as it was when last listed (and that was long enough after its last change)."""
        kept = self._trusted.get(str(path))
        return kept is not None and kept == _mtime(path)

    def _remember(self, path: Path, listed: tuple[int, int]) -> None:
        """Note that the folder was listed: `listed` is its modification time and the time, taken before the listing."""
        mtime, at = listed
        self._synced[str(path)] = mtime
        if at - mtime > RACY_NS:
            self._trusted[str(path)] = mtime
        else:
            self._trusted.pop(str(path), None)

    def _refresh_notes(self, path: Path, rel: str) -> None:
        """`_sync` of the notes for a session: the folders whose time changed are listed again, the others are walked as listed."""
        key = str(path)
        if self._unchanged(path):
            subs, names = self._listing[key]
        else:
            listed = _listed(path)
            subs, found = [], {}
            for entry in _scan(path):
                if entry.name.startswith(".") or (not rel and entry.name == RAW):
                    continue
                child = f"{rel}/{entry.name}" if rel else entry.name
                if entry.is_dir():
                    subs.append(entry.name)
                elif entry.name.endswith(".md") and entry.name not in (INDEX, LOG):
                    found[child] = entry
            old_subs, old_names = self._listing.get(key, ([], []))
            for name in old_names:
                if name not in found:
                    self._notes.pop(name, None)
            for name in set(old_subs) - set(subs):
                self._drop(path / name, f"{rel}/{name}" if rel else name)
            for name, entry in found.items():
                _update(self._notes, name, entry, _parse_note)
            names = list(found)
            self._listing[key] = (subs, names)
            self._remember(path, listed)
        for sub in subs:
            self._refresh_notes(path / sub, f"{rel}/{sub}" if rel else sub)

    def _drop(self, path: Path, rel: str) -> None:
        """Forget a folder that is gone, and every note and folder under it."""
        for name in [n for n in self._notes if n.startswith(rel + "/")]:
            del self._notes[name]
        prefix = str(path)
        for key in [k for k in self._listing if k == prefix or k.startswith(prefix + os.sep)]:
            del self._listing[key]
            self._trusted.pop(key, None)


def _mtime(path: Path) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:  # missing: nothing there
        return -1


class _Found:
    """A file the store just wrote, where `_update` expects a directory entry."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)

    def stat(self) -> os.stat_result:
        return os.stat(self.path)


def _listed(path: Path) -> tuple[int, int]:
    """(the folder's modification time, now): taken before it is listed, so a change during the listing is seen next time."""
    return _mtime(path), time.time_ns()


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
        _update(state, name, entry, parse)


def _update(state: dict[str, Any], name: str, entry: Any, parse: Callable[[str, Any], Any]) -> None:
    """Parse again one entry of `state` if it is new or changed."""
    try:
        st = entry.stat()
    except OSError:  # vanished since the scan
        state.pop(name, None)
        return
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    if name in state and state[name][0] == sig:
        return
    try:
        state[name] = (sig, parse(name, entry))
    except OSError:
        state.pop(name, None)


def _parse_copy(name: str, entry: Any) -> Copy | None:
    try:
        fm = OKFDocument.read(Path(entry.path)).frontmatter
        origin = Origin.from_dict(fm["origin"]) if isinstance(fm.get("origin"), dict) else None
    except (OKFDocumentError, InputError):
        return None
    if origin is None and "origin" in fm:  # one that is no origin: not a copy to adopt either
        return None
    seq, generated = fm.get("seq"), fm.get("generated")
    return Copy(
        name=name,
        key=origin.key if origin else "",
        seq=seq if isinstance(seq, int) else 0,
        at=str(generated.get("at", "")) if isinstance(generated, dict) else "",
        hash=str(fm["hash"]) if fm.get("hash") else None,
        version=(origin.version or None) if origin else None,
        removed=bool(fm.get("removed")),
    )


def _parse_note(rel: str, entry: os.DirEntry[str]) -> list[str]:
    """The raw copies a note cites ("/raw/…md"); none for a malformed note."""
    try:
        sources = OKFDocument.read(Path(entry.path)).frontmatter.get("sources")
    except OKFDocumentError:
        return []
    return [
        str(s["resource"])
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and str(s.get("resource", "")).startswith(f"/{RAW}/")
    ]
