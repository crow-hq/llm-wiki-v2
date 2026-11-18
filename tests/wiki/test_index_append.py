# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A folder's `index.md` gets the entry of a new note appended; any other change rewrites it, as ever."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
from pathlib import Path

import pytest

from llmw2.bundle import txn
from llmw2.bundle.check import check
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note
from tests.wiki.problems import errors


@pytest.fixture
def store(tmp_path: Path) -> WikiStore:
    s = WikiStore(tmp_path / "w", actor="test")
    s.init()
    return s


def add(store: WikiStore, folder: Folder, title: str, summary: str = "") -> Note:
    return store.write_note(
        folder, title=title, summary=summary or f"About {title}.", body="# Overview\n\nFacts.", tags=[], source={"resource": "/raw/x.md"}
    )


def index(folder: Folder) -> str:
    return (folder.path / "index.md").read_text(encoding="utf-8")


def entry_lines(folder: Folder) -> list[str]:
    return [line for line in index(folder).splitlines() if line.startswith("* ")]


def rewritten(folder: Folder, store: WikiStore) -> str:
    """What a full write of the folder's index says now."""
    path = folder.path / "index.md"
    kept = path.read_bytes()
    store.write_index(Folder(folder.path, folder.rel, folder.description, folder.subfolders, sorted(folder.notes, key=lambda n: n.slug)))
    full = path.read_text(encoding="utf-8")
    path.write_bytes(kept)
    return full


def test_a_new_note_is_appended_to_the_index_and_nothing_is_rewritten(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    for title in ("Alpha", "Bravo", "Charlie"):
        add(store, folder, title)
    path = folder.path / "index.md"
    before, ino = path.read_bytes(), os.stat(path).st_ino
    add(store, folder, "Aardvark")  # sorts first, goes last
    after = path.read_bytes()
    assert after.startswith(before) and after[len(before) :] == b"* [Aardvark](aardvark.md) - About Aardvark.\n"
    assert os.stat(path).st_ino == ino  # the file was appended to, not replaced
    assert entry_lines(folder)[-1] == "* [Aardvark](aardvark.md) - About Aardvark."


def test_the_entries_are_those_of_a_full_write_in_any_order(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    for title in ("Delta", "Alpha", "Charlie", "Bravo"):
        add(store, folder, title)
    assert sorted(entry_lines(folder)) == sorted(line for line in rewritten(folder, store).splitlines() if line.startswith("* "))


def test_a_changed_entry_rewrites_the_index(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    notes = [add(store, folder, t) for t in ("Delta", "Alpha", "Charlie")]
    store.update_note(
        notes[0], title="Delta renamed", summary="New summary.", body="# Overview\n\nMore.", tags=[], source={"resource": "/raw/y.md"}
    )
    assert len(entry_lines(folder)) == 3
    assert "* [Delta renamed](delta.md) - New summary." in entry_lines(folder)
    assert "* [Delta](delta.md) - About Delta." not in entry_lines(folder)


def test_a_note_leaving_the_folder_rewrites_its_index(store: WikiStore) -> None:
    root = store.load()
    sales, _ = store.create_folder(root, "sales", "Sales.")
    other, _ = store.create_folder(root, "ops", "Operations.")
    notes = [add(store, sales, t) for t in ("Alpha", "Bravo")]
    store.move_note(notes[0].rel, other.rel)
    assert [line.split("]")[0] for line in entry_lines(store.load().find("sales"))] == ["* [Bravo"]  # type: ignore[union-attr]
    assert [line.split("]")[0] for line in entry_lines(store.load().find("ops"))] == ["* [Alpha"]  # type: ignore[union-attr]


def test_a_new_subfolder_rewrites_the_index(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    add(store, folder, "Alpha")
    sub, _ = store.create_folder(folder, "emea", "Europe.")
    text = index(store.load().find("sales"))  # type: ignore[arg-type]
    assert text.index("# Subdirectories") < text.index("# Notes") and "* [emea](emea/index.md) - Europe." in text
    add(store, store.load().find("sales"), "Bravo")  # type: ignore[arg-type]
    assert index(store.load().find("sales")).endswith("* [Bravo](bravo.md) - About Bravo.\n")  # type: ignore[arg-type]
    assert sub.rel == "sales/emea"


def test_nothing_is_written_when_the_index_is_as_wanted(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    for title in ("Alpha", "Bravo"):
        add(store, folder, title)
    path = folder.path / "index.md"
    stamp = (os.stat(path).st_mtime_ns, os.stat(path).st_ino)
    store.write_index(folder)
    store.write_index(folder)
    assert (os.stat(path).st_mtime_ns, os.stat(path).st_ino) == stamp


def test_a_hand_edited_index_is_rewritten_not_appended_to(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    add(store, folder, "Alpha")
    path = folder.path / "index.md"
    path.write_text(path.read_text(encoding="utf-8") + "A line somebody wrote.\n", encoding="utf-8")
    add(store, folder, "Bravo")
    assert "A line somebody wrote." not in index(folder)
    assert entry_lines(folder) == ["* [Alpha](alpha.md) - About Alpha.", "* [Bravo](bravo.md) - About Bravo."]


def test_an_index_without_a_final_newline_is_appended_to_on_a_new_line(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    add(store, folder, "Alpha")
    path = folder.path / "index.md"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    add(store, folder, "Bravo")
    assert entry_lines(folder) == ["* [Alpha](alpha.md) - About Alpha.", "* [Bravo](bravo.md) - About Bravo."]


def test_the_root_index_keeps_its_front_matter(store: WikiStore) -> None:
    root = store.load()
    add(store, root, "Loose")
    text = index(store.load())
    assert text.startswith('---\nokf_version: "0.2"\n---\n') and text.endswith("* [Loose](loose.md) - About Loose.\n")


def test_an_empty_folder_gets_its_first_entry_after_the_header(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    assert index(folder).endswith("# Notes\n\n")
    add(store, folder, "Alpha")
    assert index(folder).endswith("# Notes\n\n* [Alpha](alpha.md) - About Alpha.\n")
    assert errors(check(store.root)) == []


def test_a_section_that_fails_takes_the_appended_entry_back(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    add(store, folder, "Alpha")
    before = (folder.path / "index.md").read_bytes()
    with pytest.raises(RuntimeError), store.lock():
        add(store, store.load().find("sales"), "Bravo")  # type: ignore[arg-type]
        raise RuntimeError("boom")
    assert (folder.path / "index.md").read_bytes() == before and not txn.pending(store.root)
    assert errors(check(store.root)) == []


def test_a_batch_session_sees_the_new_notes_of_an_appended_folder(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    with store.session():
        store.load()
        for title in ("Alpha", "Bravo", "Charlie"):
            add(store, store.load().find("sales"), title)  # type: ignore[arg-type]
            assert [n.title for n in store.load().find("sales").notes][-1:] != []  # type: ignore[union-attr]
        assert sorted(n.title for n in store.load().find("sales").notes) == ["Alpha", "Bravo", "Charlie"]  # type: ignore[union-attr]
    assert folder.rel == "sales"
