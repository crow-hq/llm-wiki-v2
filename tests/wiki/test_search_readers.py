# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Who writes notes puts them in the search index; who searches never waits for the index's write lock. A section of this process is
in the index before the call returns (only the paths it wrote are looked at); a search that finds the file behind while another
holds its lock answers from what the file holds, without the notes gone from the listing; a hook that fails leaves the section and
the journal as they were."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2 import searchindex as SI
from llmw2.batch import Item
from llmw2.bundle import store as S
from llmw2.models.usage import UsageTracker
from llmw2.search import NoteSearch
from tests.wiki.fakes import ContentLLM, FakeLLM
from tests.wiki.test_search import paths
from tests.wiki.test_search_index import _age, _index_file

ROOT = Path(__file__).resolve().parents[2]


def build(tmp_path: Path, n: int = 6) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=ContentLLM(UsageTracker()))
    wiki.init()
    for i in range(n):
        wiki.store.write_note(
            wiki.store.load(), title=f"Note {i}", summary=f"s{i}", body=f"common word{i} body", tags=[], source={"resource": f"/raw/{i}.md"}
        )
    _age(tmp_path / "wiki")
    assert paths(wiki.search("common", k=8))  # the first search builds the index whole
    return wiki


def indexed(tmp_path: Path) -> set[str]:
    db = sqlite3.connect(_index_file(tmp_path))
    try:
        return {rel for (rel,) in db.execute("SELECT rel FROM docs WHERE bad = 0")}
    finally:
        db.close()


def test_a_search_does_not_wait_for_the_lock_and_leaves_out_a_note_gone_meanwhile(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    rels = paths(wiki.search("common", k=8))
    gone, changed = rels[0], rels[1]
    holder = sqlite3.connect(_index_file(tmp_path), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a writer of another process holds the index
    try:
        (tmp_path / "wiki" / gone).unlink()  # deleted by hand (or purged) meanwhile
        note = tmp_path / "wiki" / changed
        note.write_text(note.read_text(encoding="utf-8").replace("common", "zebra"), encoding="utf-8")
        start = time.monotonic()
        found = paths(wiki.search("common", k=8))
        assert time.monotonic() - start < 5  # a reader that waited would wait BUSY_S
        assert gone not in found  # no title nor summary of a note that is not there any more
        assert changed in found  # what the file holds: the writer that holds the lock puts in the change
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    found = paths(wiki.search("common", k=8))  # the lock is free: the reader writes what changed by hand
    assert gone not in found and changed not in found
    assert paths(wiki.search("zebra", k=8)) == [changed]
    wiki.close()


def test_a_write_of_this_process_is_in_the_index_before_the_call_returns_with_no_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = build(tmp_path)
    listed: list[int] = []
    real = S.WikiStore.scan_notes

    def counted(self: S.WikiStore, *a: Any, **k: Any) -> Any:
        listed.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(S.WikiStore, "scan_notes", counted)
    with wiki.store.lock():  # a section, as every write of the library
        note = wiki.store.write_note(wiki.store.load(), title="Fresh", summary="s", body="quagga", tags=[], source={"resource": "/raw/q"})
    assert note.rel in indexed(tmp_path)  # in the file, with no search made
    assert listed == []  # only the paths the section wrote were looked at
    holder = sqlite3.connect(_index_file(tmp_path), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert paths(wiki.search("quagga", k=3)) == [note.rel]  # read-your-writes while another holds the lock
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    wiki.close()


def test_a_hook_that_fails_leaves_the_section_and_the_journal_as_they_were(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = build(tmp_path)

    def broken(self: SI.SearchIndex, *a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    real = SI.SearchIndex.update
    monkeypatch.setattr(SI.SearchIndex, "update", broken)
    receipt = wiki.remember("A text about the okapi of the forest.", key="okapi")
    receipt.wait(60)
    assert receipt.state == "filed" and receipt.note  # the section committed and the journal was told
    monkeypatch.setattr(SI.SearchIndex, "update", real)
    _age(tmp_path / "wiki")
    assert paths(wiki.search("okapi", k=3)) == [receipt.note]  # the next look puts it in
    wiki.close()


def test_a_batch_tells_the_index_once_at_its_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = build(tmp_path)
    told: list[list[str] | None] = []
    real = NoteSearch.written

    def counted(self: NoteSearch, store: Any, written: list[str] | None) -> None:
        told.append(written)
        real(self, store, written)

    monkeypatch.setattr(NoteSearch, "written", counted)
    wiki.ingest_many([Item(f"b{i}.md", text=f"topic: batch-{i}\nA batch text {i} about the tapir.") for i in range(3)])
    assert told == [None]
    _age(tmp_path / "wiki")
    assert len(paths(wiki.search("tapir", k=8))) == 3
    wiki.close()


def test_a_file_whole_before_the_mark_is_marked_by_its_next_look(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    wiki.close()
    db = sqlite3.connect(_index_file(tmp_path), isolation_level=None)
    db.execute("DELETE FROM meta WHERE k = 'built'")
    db.close()
    again = Wiki(wiki.cfg, llm=wiki.llm)
    assert paths(again.search("common", k=8))
    db = sqlite3.connect(_index_file(tmp_path))
    assert db.execute("SELECT v FROM meta WHERE k = 'built'").fetchone() == (1,)
    db.close()
    again.close()


@pytest.mark.parametrize("query", ["common", '"common word2"'])
def test_a_search_while_another_agent_deletes_a_note_it_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    """The release before failed with FileNotFoundError when a note it had found was deleted before it read it."""
    wiki = build(tmp_path)
    real = SI.SearchIndex.rank

    def then_deleted(self: SI.SearchIndex, *a: Any, **k: Any) -> list[str]:
        found = real(self, *a, **k)
        for rel in found[:3]:
            (tmp_path / "wiki" / rel).unlink(missing_ok=True)
        return found

    monkeypatch.setattr(SI.SearchIndex, "rank", then_deleted)
    wiki.search(query, k=8)  # no error
    wiki.close()


DIES_IN_THE_HOOK = r"""
import os, sys
from pathlib import Path
from llmw2 import Wiki, WikiConfig
from llmw2 import searchindex as SI
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
def die(*a, **k):
    os._exit(3)  # the section committed; the index is not told and the journal has no `F` yet
SI.SearchIndex.update = die
w = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=ContentLLM(UsageTracker()))
print(w.remember("A text about the narwhal of the north.", key="narwhal").id, flush=True)
w.drain(60)
"""


def test_a_process_that_dies_between_the_commit_and_the_index_loses_nothing(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    wiki.close()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    cmd = [sys.executable, "-c", DIES_IN_THE_HOOK, str(tmp_path / "wiki")]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 3, p.stderr[-2000:]
    again = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    again.drain(60)  # the recovery writes the `F` the dead process owed
    hub = again._journal()
    hub.refresh()
    (entry,) = [e for e in hub.entries.values() if e.key == "narwhal"]
    assert entry.filed is not None and entry.closed
    notes = [n for n in again.store.load().all_notes() if "narwhal" in n.body]
    assert len(notes) == 1  # filed once
    _age(tmp_path / "wiki")
    assert paths(again.search("narwhal", k=3)) == [notes[0].rel]
    again.close()


def test_a_reader_lists_the_wiki_at_most_once_a_window_and_sees_a_hand_edit_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SI, "LISTING_LOOK", 0.5)
    wiki = build(tmp_path)
    listed: list[int] = []
    real = S.WikiStore.scan_notes

    def counted(self: S.WikiStore, *a: Any, **k: Any) -> Any:
        listed.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(S.WikiStore, "scan_notes", counted)
    time.sleep(0.6)
    wiki.search("common", k=8)  # lists: the window is over
    note = tmp_path / "wiki" / paths(wiki.search("common", k=1))[0]
    note.write_text(note.read_text(encoding="utf-8").replace("common", "yak"), encoding="utf-8")  # by hand
    assert wiki.search("yak", k=3) == []  # within the window: not listed again, not seen yet
    assert len(listed) == 1
    time.sleep(0.6)
    _age(tmp_path / "wiki")
    assert paths(wiki.search("yak", k=3)) == [note.relative_to(tmp_path / "wiki").as_posix()]  # after it: seen
    assert len(listed) == 2
    wiki.close()


def test_a_section_does_not_wait_for_a_thread_that_holds_the_index(tmp_path: Path) -> None:
    """A reader of this process building or catching up the index holds its lock: a section waits `WRITTEN_WAIT` at most for it (it holds
    the bundle's lock, which every writer of every process waits for), and the note goes in at the next look."""
    import threading

    wiki = build(tmp_path)
    index = wiki._search.index
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with index._mutex:
            held.set()
            release.wait(30)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(10)
    try:
        start = time.monotonic()
        with wiki.store.lock():
            root = wiki.store.load()
            note = wiki.store.write_note(root, title="Waiting", summary="s", body="ibex", tags=[], source={"resource": "/raw/i"})
        assert time.monotonic() - start < SI.WRITTEN_WAIT + 0.25 + 1.0  # the section itself and the bounded wait
        assert note.rel not in indexed(tmp_path)
    finally:
        release.set()
        t.join()
    _age(tmp_path / "wiki")
    assert paths(wiki.search("ibex", k=3)) == [note.rel]
    wiki.close()


def test_a_section_after_another_process_wrote_reads_only_its_own_rows_and_the_next_search_reads_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process: its own connection and memory of the index
    with other.store.lock():
        other.store.write_note(other.store.load(), title="Xenon", summary="s", body="xenon common", tags=[], source={"resource": "/raw/x"})
    loads: list[int] = []
    real = SI.SearchIndex._load

    def counted(self: SI.SearchIndex, db: Any) -> None:
        loads.append(1)
        real(self, db)

    monkeypatch.setattr(SI.SearchIndex, "_load", counted)
    with wiki.store.lock():
        mine = wiki.store.write_note(wiki.store.load(), title="Yttrium", summary="s", body="yttrium common", tags=[], source={})
    assert loads == []  # under the bundle's lock: only its own rows
    assert mine.rel in indexed(tmp_path)
    assert paths(wiki.search("xenon", k=3)) == ["xenon.md"] and paths(wiki.search("yttrium", k=3)) == [mine.rel]
    assert loads  # the search read the rest again
    fresh = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    assert paths(wiki.search("common", k=20)) == paths(fresh.search("common", k=20))
    for w in (fresh, other, wiki):
        w.close()


def _deleted_when_read(monkeypatch: pytest.MonkeyPatch, victim: Path) -> None:
    """Another agent deletes `victim` after its folder was listed and before its file is read."""
    real = S.OKFDocument.read.__func__  # type: ignore[attr-defined]

    def read(cls: type, path: Path) -> Any:
        if Path(path) == victim:
            victim.unlink(missing_ok=True)
        return real(cls, path)

    monkeypatch.setattr(S.OKFDocument, "read", classmethod(read))


@pytest.mark.parametrize("in_session", [False, True])
def test_a_load_while_another_agent_deletes_a_note_leaves_it_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_session: bool) -> None:
    """The release before failed with FileNotFoundError (`recall`, through `ask`) when a note listed was deleted before it was read."""
    wiki = build(tmp_path)
    victim = tmp_path / "wiki" / "note-2.md"
    reader = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process: it parsed nothing yet
    _deleted_when_read(monkeypatch, victim)
    if in_session:
        with reader.store.session():
            rels = {n.rel for n in reader.store.load().all_notes()}
    else:
        rels = {n.rel for n in reader.store.load().all_notes()}
    assert not victim.exists() and "note-2.md" not in rels and len(rels) == 5
    for w in (reader, wiki):
        w.close()


def test_an_answer_while_another_agent_deletes_a_note_leaves_it_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = build(tmp_path)
    victim = tmp_path / "wiki" / "note-2.md"
    llm = FakeLLM(UsageTracker()).add("researcher/navigate", {"select": ["note-2.md", "note-1.md"]})
    reader = Wiki(wiki.cfg, llm=llm.add("researcher/answer", "As [note-1.md] and [note-2.md] say."))  # `recall` asks this
    _deleted_when_read(monkeypatch, victim)
    answer = reader.ask("What does the common word2 body say?")
    assert answer.notes == ["note-1.md"] and answer.citations == ["note-1.md"]
    assert not victim.exists() and "note-2.md" not in answer.notes and "note-2.md" not in answer.citations
    assert "note-2.md" not in paths(reader.search("common word2", k=8))
    for w in (reader, wiki):
        w.close()


def test_a_load_while_another_agent_removes_a_folder_leaves_it_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = build(tmp_path)
    wiki.store.create_folder(wiki.store.load(), "ops", "ops")
    wiki.store.write_note(wiki.store.load().find("ops"), title="Moved", summary="s", body="b", tags=[], source={"resource": "/raw/m.md"})
    folder = tmp_path / "wiki" / "ops"
    real = os.scandir

    def scandir(path: Any) -> Any:
        if Path(path) == folder:
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
        return real(path)

    reader = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    monkeypatch.setattr(S.os, "scandir", scandir)
    root = reader.store.load()
    assert not folder.exists() and root.find("ops") is None and len(root.all_notes()) == 6
    for w in (reader, wiki):
        w.close()
