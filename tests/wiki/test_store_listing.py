# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `WikiStore.scan_notes` keeps of the folders between two calls (`_listed`) holds no object per note but its two strings and
three numbers: every process that searches keeps it for the whole wiki. What it answers is the same."""

from __future__ import annotations

import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

from llmw2.bundle import store as S

PER_FOLDER = 15  # about as many notes a folder as the wikis of the scale bench (50000 notes in about 3500 folders)


def wiki(root: Path, notes: int) -> S.WikiStore:
    store = S.WikiStore(root, actor="test")
    store.init()
    old = time.time_ns() - 3 * S.RACY_NS
    for i in range(notes):
        folder = root / f"area-{i // PER_FOLDER:03d}"
        folder.mkdir(exist_ok=True)
        path = folder / f"a-note-about-something-{i:05d}.md"
        path.write_text("---\ntitle: x\n---\nbody\n", encoding="utf-8")
        os.utime(path, ns=(old, old))
    return store


def test_what_the_listing_keeps_a_note_is_its_strings_and_its_numbers(tmp_path: Path) -> None:
    store = wiki(tmp_path / "w", 600)
    owner = object()
    tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        order, todo = store.scan_notes({}, time.time_ns(), owner)
        known = {rel: stamp for rel, _, stamp in todo}
        strings = sum(sys.getsizeof(rel) + sys.getsizeof(rel.rpartition("/")[2]) for rel in order)
        count = len(order)
        del order, todo
        order, todo = store.scan_notes(known, time.time_ns(), owner)  # nothing to read again: the stamps are kept too
        assert todo == [] and len(order) == count == 600
        del order, todo, known
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    # a tuple of (name, path) and a tuple of three numbers a note, the numbers boxed, and a set of the names: ~270 bytes a note more.
    # About 120 are kept: the limit leaves room for the sizes of other Python versions.
    assert (held - strings) / count < 240, (held - strings) / count


def test_the_listing_answers_as_before(tmp_path: Path) -> None:
    store = wiki(tmp_path / "w", 40)
    owner = object()
    order, todo = store.scan_notes({}, time.time_ns(), owner)
    assert len(order) == len(todo) == 40 and order == sorted(order)
    known = {rel: stamp for rel, _, stamp in todo}
    assert store.scan_notes(known, time.time_ns(), owner) == (order, [])
    assert store.scan_notes(known, time.time_ns(), owner) == (order, [])  # the folders as the owner last had them
    changed = tmp_path / "w" / order[7]
    changed.write_text("---\ntitle: x\n---\nanother body\n", encoding="utf-8")  # in place: the folder's names are the same
    old = time.time_ns() - 3 * S.RACY_NS
    os.utime(changed, ns=(old, old))
    again, todo = store.scan_notes(known, time.time_ns(), owner)
    assert again == order and [rel for rel, _, _ in todo] == [order[7]]
    known[order[7]] = todo[0][2]
    (tmp_path / "w" / order[3]).unlink()
    (tmp_path / "w" / "area-000" / "a-new-one.md").write_text("---\ntitle: y\n---\nbody\n", encoding="utf-8")
    again, todo = store.scan_notes(known, time.time_ns(), owner)
    assert order[3] not in again and "area-000/a-new-one.md" in again and len(again) == 40
    assert [rel for rel, _, _ in todo] == ["area-000/a-new-one.md"]


def test_an_inode_wider_than_64_bits_is_kept_by_its_low_bits() -> None:
    stats = [SimpleNamespace(st_mtime_ns=-5, st_size=10, st_ino=(1 << 100) + 7), SimpleNamespace(st_mtime_ns=1, st_size=0, st_ino=3)]
    times_sizes, inodes = S._kept_stamps(stats)  # type: ignore[arg-type]
    assert list(times_sizes) == [-5, 1, 10, 0] and list(inodes) == [7, 3]
