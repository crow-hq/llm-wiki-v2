# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The origin index across the sessions of a caller that opens one after the other (`WikiStore.session(keep=True)`, the filer's lots).

What it read stays from one such session to the next and is checked file by file at the first look of each: a file that did not change is
not parsed again, and every change made between two sessions (by hand, by another store) is seen as a query outside a session sees it.
A session without `keep` (a CLI call, a batch) leaves the index empty, as before."""

from __future__ import annotations

import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from llmw2 import Origin, Wiki
from llmw2 import filer as F
from llmw2.bundle import origin_index as OI
from llmw2.bundle import store as S
from llmw2.bundle.store import WikiStore
from tests.wiki.test_origin_index import A, B, C, _folder, _note, _note_text, _put, _raw, _touch, assert_matches_reference
from tests.wiki.test_remember import make

ACTOR = "llmw2/test"


@pytest.fixture
def store(bundle: Path) -> WikiStore:
    wiki = WikiStore(bundle, actor=ACTOR)
    wiki.init()
    return wiki


@pytest.fixture
def parsed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The path of every raw copy and note the origin index parses from now on."""
    seen: list[str] = []
    copy, note = OI._parse_copy, OI._parse_note

    def counted_copy(name: str, entry: object) -> object:
        seen.append(str(entry.path))  # type: ignore[attr-defined]
        return copy(name, entry)

    def counted_note(rel: str, entry: os.DirEntry[str]) -> list[str]:
        seen.append(entry.path)
        return note(rel, entry)

    monkeypatch.setattr(OI, "_parse_copy", counted_copy)
    monkeypatch.setattr(OI, "_parse_note", counted_note)
    return seen


def aged(root: Path) -> None:
    """Every file and folder older than RACY_NS: what a session trusts by its time."""
    old = time.time_ns() - 3 * S.RACY_NS
    for p in [*root.rglob("*"), root]:
        os.utime(p, ns=(old, old))


def _several(store: WikiStore) -> dict[str, str]:
    """Two folders, copies of A (two versions), B and C, and notes citing them; their bundle paths by name."""
    finance, ops = _folder(store), _folder(store, "ops")
    a1 = _raw(store, Origin(A.source, A.account, A.id, version="v1"), "a1")
    a2 = _raw(store, Origin(A.source, A.account, A.id, version="v2"), "a2")
    b1, c1 = _raw(store, B, "b1"), _raw(store, C, "c1")
    return {
        "a1": a1, "a2": a2, "b1": b1, "c1": c1,
        "both": _note(store, finance, "Both versions", a1, a2),
        "first": _note(store, finance, "Only first", a1),
        "b": _note(store, ops, "Cites B", b1),
        "c": _note(store, ops, "Cites C", c1),
    }


def test_a_keep_session_parses_nothing_the_last_one_read(store: WikiStore, bundle: Path, parsed: list[str]) -> None:
    _several(store)
    aged(bundle)  # older than RACY_NS: trusted by their stamps
    with store.session(keep=True):
        assert store.find_origin(A.key) is not None
    assert parsed  # the first look reads them all
    parsed.clear()

    with store.session(keep=True):
        for key in (A.key, B.key, C.key):
            assert store.find_origin(key) is not None

    assert parsed == []


def test_a_session_without_keep_leaves_the_index_empty(store: WikiStore, bundle: Path, parsed: list[str]) -> None:
    files = _several(store)
    aged(bundle)  # trusted by their stamps: only an emptied index parses them again
    with store.session():
        assert store.find_origin(A.key) is not None
    parsed.clear()

    with store.session(keep=True):
        assert store.find_origin(A.key) is not None

    assert {Path(p).name for p in parsed} >= {Path(files[k]).name for k in ("a1", "a2", "both", "first")}


def test_changes_between_two_keep_sessions_are_seen_at_the_first_look(store: WikiStore, bundle: Path) -> None:
    files = _several(store)
    extra = _note(store, store.load().find("ops"), "Also C", files["c1"])  # type: ignore[arg-type]
    aged(bundle)  # the folders as old as a session trusts
    with store.session(keep=True):
        assert_matches_reference(store)

    a2 = bundle / files["a2"].lstrip("/")  # a copy rewritten by hand: raw/ keeps its time
    _touch(a2, a2.read_text(encoding="utf-8").replace("hash: sha256:", "removed: 2026-10-01T00:00:00+00:00\nhash: sha256:"))
    _touch(bundle / files["first"], _note_text("/raw/elsewhere.md", extra="no longer cites a1"))  # edited in place: finance keeps its time
    other = WikiStore(bundle, actor="llmw2/other")
    other.delete_note(files["b"])  # another store deletes a note
    other.create_folder(other.load(), "archive", "Old things.", log_it=False)
    other.move_notes([(extra, "archive")])  # and moves one
    (bundle / files["c1"].lstrip("/")).unlink()  # a copy deleted by hand

    with store.session(keep=True):
        record = store.find_origin(A.key)
        assert record is not None and record.removed and record.notes == [files["both"]]
        assert store.find_origin(B.key) is None
        assert store.find_origin(C.key) is None
        assert_matches_reference(store)


def test_a_note_seen_outside_a_session_and_deleted_is_not_cited_by_the_next_session(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    kept = _note(store, _folder(store), "Kept", raw)
    hand = _put(bundle / "finance" / "by-hand.md", _note_text(raw))
    record = store.find_origin(A.key)  # outside a session
    assert record is not None and record.notes == sorted([kept, "finance/by-hand.md"])

    hand.unlink()

    with store.session(keep=True):
        record = store.find_origin(A.key)
        assert record is not None and record.notes == [kept]
        assert_matches_reference(store)


def _lot(w: Wiki) -> None:
    """File what is queued in a lot of its own: the filer is started, drained and stopped (its session ends)."""
    w._filer_stopped = False
    w.drain(60)
    w._stop_filer()


def test_the_filer_does_not_parse_again_at_each_lot_what_did_not_change(
    bundle: Path, parsed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)
    w, _ = make(bundle)
    w._stop_filer()
    for g in ("alpha", "bravo", "charlie", "delta"):  # a first lot: four memories
        w.remember(f"topic: {g}\nThe first {g} memory.", key=g)
    _lot(w)
    aged(bundle)  # older than RACY_NS: what the next lot parses it trusts by its stamp
    before = {p: p.read_bytes() for p in bundle.rglob("*.md") if ".llmw2" not in p.parts}
    # a second lot: a new key and a new version of one, so the copies and the notes are read
    w.remember("topic: echo\nAn echo memory.", key="echo")
    w.remember("topic: bravo\nThe bravo memory, a second version of it.", key="bravo")
    _lot(w)
    parsed.clear()

    w.remember("topic: alpha\nThe alpha memory, a second version of it.", key="alpha")  # a third lot: another new version
    _lot(w)

    unchanged = {str(p) for p, text in before.items() if p.exists() and p.read_bytes() == text}  # untouched by lots 2 and 3
    again = sorted(unchanged & {str(Path(p)) for p in parsed})
    assert len(unchanged) >= 6 and not again, [Path(p).name for p in again]
    w.close()


def test_outside_a_session_a_note_edited_in_place_in_an_old_folder_is_seen(store: WikiStore, bundle: Path) -> None:
    files = _several(store)
    aged(bundle)
    assert_matches_reference(store)  # outside a session: what it lists, it does not trust by the folders' times

    _touch(bundle / files["first"], _note_text("/raw/elsewhere.md", extra="no longer cites a1"))

    record = store.find_origin(A.key)
    assert record is not None and record.notes == [files["both"]]
    assert_matches_reference(store)


def _same_size_elsewhere(path: Path) -> bytes:
    """The note's bytes citing "/rav/…" for "/raw/…": the same size, no copy cited."""
    data = path.read_bytes()
    assert b"/raw/" in data
    return data.replace(b"/raw/", b"/rav/")


def test_a_note_rewritten_with_the_same_size_in_the_tick_it_was_read_is_read_again(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    rel = _note(store, _folder(store), "Young", raw)
    with store.session(keep=True):  # read while younger than RACY_NS
        assert store.find_origin(A.key).notes == [rel]  # type: ignore[union-attr]
    path = bundle / rel
    st = path.stat()
    with open(path, "r+b") as f:  # in place, the same size; the time put back: what a coarse clock leaves after a write in the same tick
        f.write(_same_size_elsewhere(path))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert path.stat().st_size == st.st_size and path.stat().st_mtime_ns == st.st_mtime_ns

    with store.session(keep=True):
        assert store.find_origin(A.key) is None
        assert_matches_reference(store)


@pytest.mark.skipif(sys.platform == "win32", reason="no change time on Windows (st_ctime is the creation time): the limit of store.py")
def test_an_old_note_edited_in_place_with_its_time_put_back_is_read_again(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    rel = _note(store, _folder(store), "Old", raw)
    aged(bundle)
    with store.session(keep=True):
        assert store.find_origin(A.key).notes == [rel]  # type: ignore[union-attr]
    path = bundle / rel
    st = path.stat()
    with open(path, "r+b") as f:  # same size, same time, same inode: only the change time moves
        f.write(_same_size_elsewhere(path))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    with store.session(keep=True):
        assert store.find_origin(A.key) is None
        assert_matches_reference(store)


def _hand_notes(bundle: Path, n: int) -> None:
    """`n` notes written straight to the files, each citing a raw copy of its own, all old enough to be trusted."""
    for i in range(n):
        _put(bundle / f"area{i % 10}" / f"note-{i:04d}.md", _note_text(f"/raw/20261120-120000-a-copy-of-note-{i:04d}.md"))
    aged(bundle)


def test_what_the_index_keeps_of_a_note_is_small(store: WikiStore, bundle: Path) -> None:
    """What stays from one lot to the next is one object a note (its signature and what it cites), not a tuple of tuples and a list."""
    n = 400
    _hand_notes(bundle, n)
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        with store.session(keep=True):
            assert store._origins.citing(["/raw/none.md"]) == {}
        gc.collect()
        kept = (tracemalloc.get_traced_memory()[0] - before) / n
    finally:
        tracemalloc.stop()
    assert len(store._origins._notes) == n
    assert kept < 330, kept  # bytes a note, with its path and the dict; the layout of tuples and a list kept ~540


def test_a_copy_whose_name_starts_with_another_is_not_taken_for_it(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "area" / "one.md", _note_text("/raw/doc.md-2.md"))
    _put(bundle / "area" / "two.md", _note_text("/raw/doc.md", "/raw/other.md"))
    with store.session(keep=True):
        assert store._origins.citing(["/raw/doc.md"]) == {"area/two.md": 0}
        assert store._origins.citing(["/raw/other.md", "/raw/doc.md-2.md"]) == {"area/one.md": 1, "area/two.md": 0}
