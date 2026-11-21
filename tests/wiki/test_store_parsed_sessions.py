# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `load` parsed outlives the session (`_parsed`, by the stamp of the file): the next session does not read a note
again whose file is as it was, and sees every file another process changed, deleted or added between two sessions."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from llmw2.bundle import document as document_module
from llmw2.bundle.store import WikiStore, same_stamp
from tests.wiki.test_batch_session import aged


def make_store(tmp_path: Path, notes: int = 3) -> WikiStore:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    for i in range(notes):
        store.write_note(folder, title=f"Note {i}", summary="s", body=f"Body number {i}.", tags=[], source={"resource": f"/raw/{i}.md"})
    with store.session():  # the first session of a store keeps nothing it parses (see `test_the_first_session_...`): this one is it
        pass
    return store


def note_files(store: WikiStore) -> list[Path]:
    return sorted(p for p in (store.root / "area").glob("*.md") if p.name not in ("index.md", "log.md"))


def bodies(store: WikiStore) -> set[str]:
    return {n.body.strip() for n in store.load().all_notes()}


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = document_module.OKFDocument.read.__func__

    def counting(cls, path):
        seen.append(Path(path).name)
        return real(cls, path)

    monkeypatch.setattr(document_module.OKFDocument, "read", classmethod(counting))
    return seen


def test_a_second_session_reads_no_note_again(tmp_path: Path, reads: list[str]) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        first = store.load()
    assert len(reads) == 3
    reads.clear()
    with store.session():
        second = store.load()
    assert reads == []
    assert [n.body for n in second.all_notes()] == [n.body for n in first.all_notes()]
    note = second.all_notes()[0]
    note.frontmatter["title"] = "edited by the caller"  # each load hands out its own copy of the front matter
    with store.session():
        assert store.load().all_notes()[0].frontmatter["title"] != "edited by the caller"


def test_a_note_another_process_rewrote_between_sessions_is_read_again(tmp_path: Path, reads: list[str]) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        store.load()
    path = note_files(store)[0]
    path.write_bytes(path.read_bytes().replace(b"Body number", b"Longer text of number"))
    reads.clear()
    with store.session():
        assert any(b.startswith("Longer text of number") for b in bodies(store))
    assert reads == [path.name]


def test_a_note_rewritten_in_the_same_tick_with_the_same_size_is_read_again(tmp_path: Path) -> None:
    """A coarse clock: the file is written again within the tick of the first write, with the same time and size."""
    store = make_store(tmp_path)
    path = note_files(store)[0]
    tick = time.time_ns() // 10**9 * 10**9  # the file systems with a one second time
    os.utime(path, ns=(tick, tick))
    with store.session():
        assert "Body number 0." in bodies(store)
    size = path.stat().st_size
    path.write_bytes(path.read_bytes().replace(b"Body number 0.", b"Body number 7."))
    os.utime(path, ns=(tick, tick))
    assert (path.stat().st_mtime_ns, path.stat().st_size) == (tick, size)
    with store.session():
        assert "Body number 7." in bodies(store)
        assert "Body number 0." not in bodies(store)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows gives no change time of a file: the stamp is all there is")
def test_a_note_rewritten_with_its_old_time_and_size_restored_by_hand_is_read_again(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        assert "Body number 0." in bodies(store)
    path = note_files(store)[0]
    before = path.stat()
    path.write_bytes(path.read_bytes().replace(b"Body number 0.", b"Body number 7."))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # `cp -p`, `rsync -t`, a restore
    assert (path.stat().st_mtime_ns, path.stat().st_size) == (before.st_mtime_ns, before.st_size)
    with store.session():
        assert "Body number 7." in bodies(store)


def test_a_note_deleted_between_sessions_is_gone_and_not_kept(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        store.load()
        assert len(store._parsed) == 3
    gone = note_files(store)[1]
    gone.unlink()
    with store.session():
        assert len(store.load().all_notes()) == 2
        assert str(gone) not in store._parsed
        assert len(store._parsed) == 2  # the cache never outgrows the wiki


def test_a_note_another_process_added_between_sessions_is_seen(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        store.load()
    other = WikiStore(store.root, actor="other")
    folder = other.load().find("area")
    other.write_note(folder, title="Late", summary="s", body="Written by the other.", tags=[], source={"resource": "/raw/late.md"})
    with store.session():
        assert "Written by the other." in bodies(store)


def test_a_session_keeps_what_a_note_written_in_it_changes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        folder = store.load().find("area")
        store.write_note(folder, title="New", summary="s", body="Written in the session.", tags=[], source={"resource": "/raw/n.md"})
        assert "Written in the session." in bodies(store)
    with store.session():
        assert "Written in the session." in bodies(store)


def test_a_note_a_purge_deletes_is_in_no_cache(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        root = store.load()
        gone = root.all_notes()[0]
        store.purge_notes(root, [gone], [])
        assert str(gone.path) not in store._parsed
        assert len(store._parsed) == 2


def test_a_load_outside_a_session_keeps_what_it_parsed_too(tmp_path: Path, reads: list[str]) -> None:
    store = make_store(tmp_path)
    aged(store)
    store.load()
    reads.clear()
    store.load()
    assert reads == []
    with store.session():  # the same cache serves both
        store.load()
    assert reads == []


def test_a_note_renamed_between_sessions_is_read_at_its_new_path_and_the_old_leaves_the_cache(tmp_path: Path, reads: list[str]) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        store.load()
    old = note_files(store)[0]
    new = old.with_name("renamed-by-hand.md")
    os.replace(old, new)
    reads.clear()
    with store.session():
        names = {n.path.name for n in store.load().all_notes()}
        assert "renamed-by-hand.md" in names and old.name not in names
        assert str(old) not in store._parsed
    assert reads == ["renamed-by-hand.md"]  # the other two were not read again (the new one is recent: it is not kept)


def test_a_note_another_process_deleted_leaves_the_cache_though_nothing_was_read(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        store.load()
        assert len(store._parsed) == 3
        gone = note_files(store)[0]
        gone.unlink()
        store._dirs.clear()  # the folder is listed again, the other notes are as they were
        store.load()
        assert str(gone) not in store._parsed and len(store._parsed) == 2


def test_a_section_rolled_back_in_a_session_with_a_warm_cache_shows_the_note_as_it_was(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    with store.session():
        root = store.load()  # the cache is warm
        note = root.all_notes()[0]
        was = note.body.strip()
        with pytest.raises(RuntimeError), store.lock():
            store.update_note(
                note, title=note.title, summary=note.summary, body="Changed in a section that fails.", tags=[],
                source={"resource": "/raw/x.md"},
            )
            raise RuntimeError("boom")
        assert was in bodies(store)
        assert "Changed in a section that fails." not in bodies(store)
    with store.session():
        assert was in bodies(store)


def test_the_first_session_of_a_store_keeps_nothing_and_the_second_fills_the_cache(tmp_path: Path, reads: list[str]) -> None:
    store = WikiStore(make_store(tmp_path).root, actor="fresh")  # a process that has not had a session yet
    aged(store)
    with store.session():
        store.load()
        store.load()  # kept folders: nothing is read again within the session
        assert store._parsed == {}
    assert len(reads) == 3
    with store.session():
        store.load()
        assert len(store._parsed) == 3
    reads.clear()
    with store.session():
        store.load()
    assert reads == []
    assert len(WikiStore(store.root, actor="plain").load().all_notes()) == 3  # outside a session the cache is filled at once



def test_note_at_gives_the_note_a_load_gives_and_parses_it_again_only_when_its_file_changed(tmp_path: Path, reads: list[str]) -> None:
    store = make_store(tmp_path)
    aged(store)
    rel = "area/" + note_files(store)[0].name
    loaded = next(n for n in store.load().all_notes() if n.rel == rel)
    reads.clear()

    first, again = store.note_at(rel), store.note_at(rel)

    assert first is not None and again is not None and reads == []  # the load parsed it: neither parses it again
    assert (first.rel, first.title, first.body, first.frontmatter) == (loaded.rel, loaded.title, loaded.body, loaded.frontmatter)
    assert first.stamp is not None and loaded.stamp is not None and same_stamp(first.stamp, loaded.stamp)
    assert first.folder is None and first.frontmatter is not again.frontmatter  # a copy of the front matter each time
    path = store.root / rel
    path.write_text(path.read_text(encoding="utf-8").replace("Body number", "Body changed, number"), encoding="utf-8")
    changed = store.note_at(rel)
    assert changed is not None and "Body changed" in changed.body and len(reads) == 1


@pytest.mark.parametrize(
    "rel", ["area/missing.md", "area/index.md", "log.md", "_raw/0.md", ".llmw2/x.md", "area", "../outside.md", "C:/x.md", "area//x.md"]
)
def test_note_at_gives_none_where_a_load_finds_no_note(tmp_path: Path, rel: str) -> None:
    store = make_store(tmp_path)
    (tmp_path / "outside.md").write_bytes(note_files(store)[0].read_bytes())  # a note out of the wiki

    assert store.note_at(rel) is None


def test_note_at_drops_what_was_parsed_of_a_file_gone(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    aged(store)
    store.load()  # parses every note and keeps it
    path = note_files(store)[0]
    assert str(path) in store._parsed

    path.unlink()

    assert store.note_at("area/" + path.name) is None and str(path) not in store._parsed
