# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which raw copies carry an origin key, and which notes cite them: an index kept in memory.

It is rebuilt from the files and is never the source of truth. Every query first refreshes it by `stat`:
only new or changed files are parsed again (another stamp or change time, or a file written within RACY_NS of when it was
parsed), and the ones that vanished are dropped. Store writes are not
reported to it: the refresh sees them, because `write_atomic` replaces the file and so changes its inode.

In a session (a batch, see `WikiStore.session`) a folder whose modification time is the same, and not recent, is not
listed again: the store writes by replacing files, which changes the time of their folder. A raw copy already known is
not looked at again in a session, whatever happens to its file: the store tells (`touch`) when it rewrites one. Both hold
within one session only: at its start the index trusts no folder (`distrust`), and its first look checks every file by
`stat`, as a query outside a session does; what it parsed before stays, so a caller that opens sessions one after another
(the filer's lots) parses again only what changed.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import struct
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

# a file's signature, packed in 32 bytes: modification time, size, inode and change time (`st_ctime_ns`), as the parse cache of
# `WikiStore.load` compares them; `_YOUNG` for a file written within RACY_NS of when it was parsed (two writes in one clock tick can leave
# the same time and size): it is parsed again the next time. Packed, so that what the filer keeps from one lot to the next is one object
# a note (with what it cites), not a dozen
_SIG = struct.Struct("<qqQq")
_YOUNG = b"\xff" * _SIG.size
_NO_INODE = bytes(8)  # the inode a listing gives on Windows: it says nothing
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


class _Raws(dict[str, tuple[bytes, Copy | None]]):
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

    def __setitem__(self, name: str, value: tuple[bytes, Copy | None]) -> None:
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
        # bundle path -> its signature followed by the raw copies it cites ("/raw/…md", utf-8, each after a NUL)
        self._notes: dict[str, bytes] = {}
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

    def distrust(self) -> None:
        """A session starts: no folder is trusted by its time any more, and raw/ is not taken as listed in it, so the first look
        checks every file by `stat`; what was parsed stays, and is parsed again only for a file that changed."""
        with self._lock:
            self._trusted.clear()
            self._synced.clear()

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

    def hashes(self) -> set[str]:
        """The hashes of the copies saved without an origin."""
        with self._lock:
            self._refresh()
            return {h for h, copies in self._raws.by_hash.items() if copies}

    def citing(self, raws: list[str]) -> dict[str, int]:
        """The notes citing any of `raws` ("/raw/…md"), each with the highest position in `raws` it cites."""
        if not raws:
            return {}
        with self._lock:
            self._refresh(notes=True)
            first: dict[str, int] = {}
            for at, raw in enumerate(raws):
                first.setdefault(raw, at)
            wanted = [b"\0" + raw.encode() for raw in first]
            newest: dict[str, int] = {}
            for rel, packed in self._notes.items():
                if any(packed.find(w, _SIG.size) >= 0 for w in wanted):  # then read exactly: a name may hold another as a prefix
                    cited = packed[_SIG.size + 1 :].decode().split("\0")
                    if positions := [first[r] for r in cited if r in first]:
                        newest[rel] = max(positions)
        return newest

    def _refresh(self, notes: bool = False) -> None:
        raw = self.root / RAW
        if not (self.session and self._unchanged(raw)):
            listed = _listed(raw)
            raws = {e.name: e for e in _scan(raw) if e.name.endswith(".md") and e.is_file()}
            if self.session and str(raw) in self._synced:  # listed in this session: a copy is never edited, but by `touch`, so only
                for name in self._raws.keys() - raws.keys():  # the new ones are read
                    del self._raws[name]
                for name, entry in raws.items():
                    if name not in self._raws:
                        _update(self._raws, name, entry, _parse_copy)
            else:  # outside a session, or its first look: every copy checked by `stat`
                _sync(self._raws, raws, _parse_copy)
            self._remember(raw, listed)
        if notes:  # only `citing` reads the notes: walking the whole tree for the other queries would cost a stat per note
            self._refresh_notes(self.root, "", trust=self.session)  # outside a session every folder is listed (and `_listing` kept)

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

    def _refresh_notes(self, path: Path, rel: str, *, trust: bool) -> None:
        """`_sync` of the notes: with `trust` (a session) the folders whose time changed are listed again and the others are walked as
        listed; without it every folder is listed again. The notes a folder no longer lists go, so `_notes` holds what `_listing` holds."""
        key = str(path)
        if trust and self._unchanged(path):
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
                _update_note(self._notes, name, entry)
            names = list(found)
            self._listing[key] = (subs, names)
            self._remember(path, listed)
        for sub in subs:
            self._refresh_notes(path / sub, f"{rel}/{sub}" if rel else sub, trust=trust)

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


def _sync(state: dict[str, Any], seen: dict[str, os.DirEntry[str]], parse: Callable[[str, os.DirEntry[str]], Any]) -> None:
    """Parse again what is new or changed in `state`, forget what is no longer in `seen`."""
    for name in state.keys() - seen.keys():
        del state[name]
    for name, entry in seen.items():
        _update(state, name, entry, parse)


def _signature(st: os.stat_result) -> bytes:
    return _SIG.pack(st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)


def _kept(st: os.stat_result, sig: bytes) -> bytes:
    """The signature to keep for a file just parsed: `_YOUNG` if it was written within RACY_NS."""
    return sig if time.time_ns() - st.st_mtime_ns > RACY_NS else _YOUNG


def _same(held: bytes, sig: bytes) -> bool:
    """Whether a kept signature is the file's now: the same time, size and change time, and the same inode unless either is 0 (a listing
    on Windows; as `same_stamp` of the store); never for `_YOUNG`."""
    if held[: _SIG.size] == sig:
        return sig != _YOUNG
    return held[:16] == sig[:16] and held[24:32] == sig[24:] and _NO_INODE in (held[16:24], sig[16:24]) and held[:32] != _YOUNG


def _stat(state: dict[str, Any], name: str, entry: Any) -> os.stat_result | None:
    try:
        return entry.stat()
    except OSError:  # vanished since the scan
        state.pop(name, None)
        return None


def _update(state: dict[str, Any], name: str, entry: Any, parse: Callable[[str, Any], Any]) -> None:
    """Parse again one entry of `state` if it is new or changed: another signature (`_same`), or a file too young to be trusted when it
    was parsed."""
    if (st := _stat(state, name, entry)) is None:
        return
    sig = _signature(st)
    if (held := state.get(name)) is not None and _same(held[0], sig):
        return
    try:
        state[name] = (_kept(st, sig), parse(name, entry))
    except OSError:
        state.pop(name, None)


def _update_note(notes: dict[str, bytes], name: str, entry: os.DirEntry[str]) -> None:
    """`_update` for a note: one bytes object, its signature then the copies it cites."""
    if (st := _stat(notes, name, entry)) is None:
        return
    sig = _signature(st)
    if (held := notes.get(name)) is not None and _same(held, sig):
        return
    try:
        cited = _parse_note(name, entry)
    except OSError:
        notes.pop(name, None)
        return
    notes[name] = _kept(st, sig) + b"".join(b"\0" + raw.encode() for raw in cited)


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
