# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WikiStore.load() reads a note once: a note whose file is unchanged (same modification time and size) is not read or
parsed again, and any change to a file (by the store or by another process) is picked up."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from llmw2.bundle import document
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import Origin
from llmw2.bundle.store import WikiStore


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = yaml.load

    def counting(text: str, Loader: type) -> object:
        if text.startswith("type:"):  # a note, not the root index
            seen.append(text)
        return real(text, Loader=Loader)

    monkeypatch.setattr(document.yaml, "load", counting)
    return seen


def make(tmp_path: Path, n: int = 5) -> WikiStore:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    for i in range(n):
        store.write_note(
            folder, title=f"Note {i}", summary="s", body=f"Body {i}.", tags=["a", "b"], source={"resource": "/raw/x.md"}
        )
    age(store)
    return store


def age(store: WikiStore) -> None:
    """Make every note an hour old: a file written in the last moments is always read again."""
    then = time.time_ns() - 3_600 * 10**9
    for path in store.root.rglob("*.md"):
        os.utime(path, ns=(then, then))


def test_second_load_parses_nothing(tmp_path: Path, parses: list[str]) -> None:
    store = make(tmp_path)
    store.load()
    parses.clear()
    first = store.load()
    assert parses == []
    assert len(first.all_notes()) == 5


def test_edit_in_a_note_does_not_leak_into_the_cache(tmp_path: Path) -> None:
    store = make(tmp_path)
    note = store.load().all_notes()[0]
    note.frontmatter["tags"].append("mutated")
    note.frontmatter["extra"] = 1
    again = {n.rel: n for n in store.load().all_notes()}[note.rel]
    assert again.frontmatter["tags"] == ["a", "b"] and "extra" not in again.frontmatter


def test_external_edit_is_picked_up(tmp_path: Path, parses: list[str]) -> None:
    store = make(tmp_path)
    store.load()
    path = tmp_path / "w" / "area" / "note-2.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Note 2", "Edited"), encoding="utf-8")
    then = time.time_ns() - 7_200 * 10**9
    os.utime(path, ns=(then, then))
    parses.clear()
    notes = {n.rel: n for n in store.load().all_notes()}
    assert notes["area/note-2.md"].title == "Edited"
    assert len(parses) == 1


def test_recent_edit_with_same_size_and_time_is_picked_up(tmp_path: Path) -> None:
    store = make(tmp_path)
    path = tmp_path / "w" / "area" / "note-2.md"
    now = time.time_ns()
    os.utime(path, ns=(now, now))
    store.load()
    stat = path.stat()
    path.write_text(path.read_text(encoding="utf-8").replace("Note 2", "Note 9"), encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    notes = {n.rel: n for n in store.load().all_notes()}
    assert notes["area/note-2.md"].title == "Note 9"


def test_move_delete_and_merge_keep_notes_right(tmp_path: Path) -> None:
    store = make(tmp_path)
    root = store.load()
    folder, _ = store.create_folder(root, "other", "Other", log_it=False)
    store.move_notes([("area/note-0.md", "other")])
    store.delete_note("area/note-1.md")
    notes = {n.rel: n for n in store.load().all_notes()}
    assert notes["other/note-0.md"].title == "Note 0"
    assert "area/note-1.md" not in notes and "area/note-0.md" not in notes
    store.merge_folders("other", "area")
    notes = {n.rel: n for n in store.load().all_notes()}
    assert notes["area/note-0.md"].title == "Note 0"
    assert folder.rel == "other"


def test_loader_matches_the_pure_python_one() -> None:
    text = (
        "---\ntype: Note\ntitle: 'Q: yes'\ncreated: 2026-06-30T14:00:00Z\nday: 2026-06-30\nflag: yes\nn: 012\n"
        "list:\n  - a\n  - {k: v}\nmulti: |\n  line one\n  line two\nuni: ok\n---\n\nBody\n"
    )
    fast = OKFDocument.parse(text)
    slow = yaml.load("\n".join(text.split("\n")[1:-4]), Loader=document._Loader)
    assert fast.frontmatter == slow
    assert isinstance(fast.frontmatter["created"], str) and isinstance(fast.frontmatter["day"], str)


def test_origin_index_sees_another_store_writing(tmp_path: Path) -> None:
    store = make(tmp_path)
    store.save_raw("one", title="one", resource=None, origin=Origin("t", "a", "k1"))
    assert len(store.origin_keys("")) == 1
    other = WikiStore(tmp_path / "w", actor="other")
    other.save_raw("two", title="two", resource=None, origin=Origin("t", "a", "k2"))
    assert len(store.origin_keys("")) == 2
    other.mark_origin_removed(next(iter(other.origin_keys(""))))
    raw = [c for k in store.origin_keys("") for c in store._origins.copies(k)]
    assert sum(c.removed for c in raw) == 1
    other.clear()
    assert store.origin_keys("") == set()
