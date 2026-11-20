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
    assert loads == []  # what the other process wrote came by `recent`: its rows alone, no whole read
    fresh = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    assert paths(wiki.search("common", k=20)) == paths(fresh.search("common", k=20))
    for w in (fresh, other, wiki):
        w.close()


def test_a_reader_takes_what_another_process_wrote_from_recent_and_reads_whole_beyond_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notes added, changed and deleted by another process: the reader takes their rows alone (`recent`), and answers as a new
    process that reads the file whole; more rows written than `recent` keeps: it reads the file whole."""
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process
    loads: list[int] = []
    real = SI.SearchIndex._load

    def counted(self: SI.SearchIndex, db: Any) -> None:
        loads.append(1)
        real(self, db)

    monkeypatch.setattr(SI.SearchIndex, "_load", counted)
    first = paths(wiki.search("common", k=20))[0]

    def same_as_a_new_process(query: str) -> None:
        fresh = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
        try:
            assert paths(wiki.search(query, k=20)) == paths(fresh.search(query, k=20)), query
        finally:
            fresh.close()

    loads.clear()
    with other.store.lock():
        other.store.write_note(other.store.load(), title="Okapi", summary="s", body="okapi common common", tags=[], source={})
    with other.store.lock():
        note = other.store.read(first)
        other.store.update_note(note, title=note.title, summary="s", body="changed words, nothing alike", tags=[], source={})
    with other.store.lock():
        other.store.delete_note("note-1.md")
    hits = paths(wiki.search("common", k=20))
    assert loads == [] and "okapi.md" in hits and first not in hits and "note-1.md" not in hits
    same_as_a_new_process("common")
    loads.clear()
    monkeypatch.setattr(SI, "RECENT_IDS", 4)
    for n in range(6):  # more rows written than `recent` keeps
        with other.store.lock():
            other.store.write_note(other.store.load(), title=f"Gnu {n}", summary="s", body=f"gnu{n} common", tags=[], source={})
    assert paths(wiki.search("gnu3", k=3)) == ["gnu-3.md"]
    assert len(loads) == 1  # read whole, once
    same_as_a_new_process("common")
    for w in (other, wiki):
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


def test_a_search_while_another_process_writes_at_every_read_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A writer that writes more often than a search reads (a fast filer, at W5k) made every attempt stale: after six, "the search
    index cannot be rebuilt". The search reads again inside one state of the file, whatever is written meanwhile."""
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process: its own index and connection
    real = SI.SearchIndex._postings
    writes: list[int] = []

    def written_meanwhile(self: SI.SearchIndex, words: Any) -> Any:
        if self is wiki._search.index:  # between the sync of the search and its postings, the other writes a note (and its index)
            writes.append(1)
            with other.store.lock():
                n = len(writes)
                other.store.write_note(other.store.load(), title=f"Extra {n}", summary="s", body=f"common extra{n}", tags=[], source={})
        return real(self, words)

    monkeypatch.setattr(SI.SearchIndex, "_postings", written_meanwhile)
    loads: list[int] = []
    real_load = SI.SearchIndex._load

    def counted(self: SI.SearchIndex, db: Any) -> None:
        loads.append(1)
        real_load(self, db)

    monkeypatch.setattr(SI.SearchIndex, "_load", counted)
    found = paths(wiki.search("common body", k=20))  # no DatabaseError ("body": a word this process never read)
    assert len(writes) >= 2 and len(found) >= 6  # it was stale once, then read one state of the file
    assert loads == []  # after the stale read, what was written since came from `recent`, not a whole read
    monkeypatch.setattr(SI.SearchIndex, "_load", real_load)
    monkeypatch.setattr(SI.SearchIndex, "_postings", real)
    assert set(found) <= set(paths(wiki.search("common body", k=20)))  # and that state was a true one
    for w in (other, wiki):
        w.close()


def test_a_search_reading_one_state_of_a_file_made_again_under_it_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In the read transaction of a search, the file is found made again by another process: the connection is closed under the
    transaction, and the search goes on, on the new file."""
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    index = wiki._search.index
    real = SI.SearchIndex._postings
    calls: list[int] = []

    def written_meanwhile(self: SI.SearchIndex, words: Any) -> Any:
        if self is index:
            calls.append(1)
            if len(calls) == 2:  # inside the read transaction: as `_open` does when it finds another file at the path
                index._close_held()
            with other.store.lock():
                n = len(calls)
                other.store.write_note(other.store.load(), title=f"Extra {n}", summary="s", body=f"common extra{n}", tags=[], source={})
        return real(self, words)

    monkeypatch.setattr(SI.SearchIndex, "_postings", written_meanwhile)
    assert paths(wiki.search("common body", k=20))  # no ProgrammingError
    assert len(calls) >= 3
    for w in (other, wiki):
        w.close()


def test_a_reader_that_listed_a_moment_ago_does_not_list_again_because_another_process_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fast filer writes the index several times a second: a reader reads what it wrote from the file, and lists the wiki's files
    no more than once a window all the same (before, every write of another process made the next search list them all)."""
    monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process
    listed: list[int] = []
    real = S.WikiStore.scan_notes

    def counted(self: S.WikiStore, *a: Any, **k: Any) -> Any:
        listed.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(S.WikiStore, "scan_notes", counted)
    for n in range(3):
        with other.store.lock():  # a note filed by the library: the other process's index has it before its section returns
            other.store.write_note(other.store.load(), title=f"Gnu {n}", summary="s", body=f"gnu{n} common", tags=[], source={})
        assert paths(wiki.search(f"gnu{n}", k=3)) == [f"gnu-{n}.md"]  # read from the file, which another process wrote
    assert listed == []  # listed when built, a moment ago: not again
    for w in (other, wiki):
        w.close()


@pytest.mark.parametrize("other_writes", [False, True])
def test_a_note_gone_while_a_writer_holds_the_index_stays_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other_writes: bool) -> None:
    """A note deleted while another process holds the index's write lock (a purge whose hook failed: "index" in `pending`) is left out
    by a reader (D1); it stays out at the next listing that finds the files as they were, and after another process wrote the index
    (the reader reads its state again): no title nor summary of it comes back."""
    wiki = build(tmp_path)
    gone = paths(wiki.search("common", k=8))[0]
    holder = sqlite3.connect(_index_file(tmp_path), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a writer of another process holds the index all along
    try:
        (tmp_path / "wiki" / gone).unlink()
        first = paths(wiki.search("common", k=8))
        if other_writes:  # another process writes the index (gen + 1): the reader reads its state again, within the window
            monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
            holder.execute("UPDATE meta SET v = v + 1 WHERE k = 'gen'")
            holder.execute("COMMIT")
            holder.execute("BEGIN IMMEDIATE")
        second = paths(wiki.search("common", k=8))  # else a new listing, the same as the last (LISTING_LOOK is 0 in the tests)
        assert gone not in first and gone not in second
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        wiki.close()


def _state(index: SI.SearchIndex) -> tuple[Any, ...]:
    return (dict(index._ids), dict(index._rels), dict(index._len), dict(index._stamps), set(index._bad), index._total, index._gen,
            index._built)


def test_the_state_a_reader_takes_from_recent_is_the_one_a_new_read_of_the_file_gives(tmp_path: Path) -> None:
    """Writes of every kind by another process (a note added, changed, deleted, purged; a file that is not a note): after each, what the
    reader holds (ids, paths, lengths, stamps, files that are not notes, total, generation) is what a whole read of the file gives."""
    wiki = build(tmp_path)
    root = tmp_path / "wiki"
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process
    index = wiki._search.index
    caught: list[int] = []
    real = SI.SearchIndex._caught_up

    def counted(self: SI.SearchIndex, db: Any) -> bool:
        done = real(self, db)
        caught.append(int(done))
        return done

    def check() -> None:
        SI.LISTING_LOOK = 60.0  # the reader does not list the files: what it holds comes from the file alone
        try:
            wiki.search("common", k=20)
        finally:
            SI.LISTING_LOOK = 0.0
        fresh = SI.SearchIndex(index.path)
        try:
            with fresh._mutex:
                fresh._load(fresh._open())
            assert _state(index) == _state(fresh)
        finally:
            fresh.close()

    import llmw2.searchindex as si_module

    si_module.SearchIndex._caught_up = counted  # type: ignore[method-assign]
    try:
        with other.store.lock():
            other.store.write_note(other.store.load(), title="Okapi", summary="s", body="okapi common", tags=[], source={})
        check()
        note = other.store.read("note-2.md")
        with other.store.lock():
            other.store.update_note(note, title=note.title, summary="t", body="common changed, longer than before", tags=[], source={})
        check()
        with other.store.lock():
            other.store.delete_note("note-3.md")
        check()
        other.remember("topic: rare-1\nThe rare common gazelle.", key="gazelle")
        other.drain(60)
        check()
        assert other.purge("gazelle") is not None
        check()
        (root / "not-a-note.md").write_bytes(b"\xff\xfe not text")  # by hand: the other process's next search writes a row for it
        other.search("common", k=3)
        check()
        (root / "not-a-note.md").write_bytes(b"\xff\xfe still not text, longer")  # changed by hand: a new stamp of a row that is no note
        other.search("common", k=3)
        check()
        assert caught and all(caught)  # every time from `recent`
    finally:
        si_module.SearchIndex._caught_up = real  # type: ignore[method-assign]
        for w in (other, wiki):
            w.close()


def test_a_purge_leaves_no_name_of_the_note_in_any_file_with_an_index(tmp_path: Path) -> None:
    """B11: `recent` names rows by id, never by path: the path of a purged note (its title) is in no file of the wiki after the purge."""
    from tests.wiki.test_purge_journal import KEY, TEXT, TITLE, disk
    from tests.wiki.test_remember import make, notes

    bundle = tmp_path / "wiki"
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        w.search("falcon", k=5)  # the index is built
        name = Path(str(notes(bundle)[0])).stem
        report = w.purge(KEY)
        assert report is not None and report.pending == []
        assert name.encode() not in disk(bundle)
    finally:
        w.close()


def test_recent_stays_within_its_bound_whatever_is_written() -> None:
    """Writes of more rows than `recent` holds (a build, a batch) are not kept, writes that name no row count as one: after a
    thousand of each, `recent` holds at most RECENT_IDS generations, a few hundred bytes."""
    import json
    import types

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v)")
    me = types.SimpleNamespace(_ids={})
    for gen in range(1, 1001):
        changes = SI._Changes()
        changes.docs = {f"n{i}": (i, 1, False) for i in range(SI.RECENT_IDS + 1)}
        SI.SearchIndex._note_recent(me, db, gen, changes)  # type: ignore[arg-type]
    for gen in range(1001, 2001):
        SI.SearchIndex._note_recent(me, db, gen, SI._Changes())  # type: ignore[arg-type]
    value = db.execute("SELECT v FROM meta WHERE k = 'recent'").fetchone()[0]
    assert len(json.loads(value)) <= SI.RECENT_IDS and len(value) < 16 * SI.RECENT_IDS
    db.close()


@pytest.mark.parametrize("planted", ['[[{gen}, null]]', '[[{gen}, ["a.md"]]]', "not json at all"])
def test_a_recent_of_another_shape_neither_stops_a_writer_nor_fails_a_search(tmp_path: Path, planted: str) -> None:
    """`recent` written by an earlier form (a None, paths) or not JSON: a writer starts it again from none, a reader reads the file
    whole; no write is stopped for good, no search fails."""
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process
    db = sqlite3.connect(_index_file(tmp_path), isolation_level=None)

    def plant() -> None:
        gen = int(db.execute("SELECT v FROM meta WHERE k = 'gen'").fetchone()[0])
        db.execute("INSERT OR REPLACE INTO meta VALUES ('recent', ?)", (planted.format(gen=gen + 1),))

    try:
        plant()
        with other.store.lock():  # the hook writes the index over it
            other.store.write_note(other.store.load(), title="Ibex", summary="s", body="ibex common", tags=[], source={})
        assert "ibex.md" in indexed(tmp_path)
        plant()  # and what a reader finds after the other process wrote: that shape again
        with db:
            db.execute("UPDATE meta SET v = v + 1 WHERE k = 'gen'")
        assert paths(wiki.search("ibex", k=3)) == ["ibex.md"]
        with other.store.lock():  # a write after it goes through too
            other.store.write_note(other.store.load(), title="Kudu", summary="s", body="kudu common", tags=[], source={})
        assert paths(wiki.search("kudu", k=3)) == ["kudu.md"]
    finally:
        db.close()
        for w in (other, wiki):
            w.close()


def _listings(monkeypatch: pytest.MonkeyPatch) -> list[S.WikiStore]:
    """The stores that list the wiki's files, one item a listing."""
    listed: list[S.WikiStore] = []
    real = S.WikiStore.scan_notes

    def counted(self: S.WikiStore, *a: Any, **k: Any) -> Any:
        listed.append(self)
        return real(self, *a, **k)

    monkeypatch.setattr(S.WikiStore, "scan_notes", counted)
    return listed


def test_the_processes_of_a_machine_list_the_wiki_once_a_window_between_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Processes that search one wiki take the listing one of them made (shared in the machine's memory, no byte on disk) instead of
    listing for themselves, and answer as the one that listed: the same notes, in the same order at the same score (the order of the
    tree, notes before folders, not the order of the paths)."""
    wiki = build(tmp_path)
    root = tmp_path / "wiki"
    (root / "aa").mkdir()
    (root / "aa" / "copy.md").write_bytes((root / "note-0.md").read_bytes())  # by hand: the same text, in a folder
    _age(root)
    first = paths(wiki.search("common", k=8))  # lists (the window is 0 here) and writes the note found
    assert first[0] == "note-0.md" and first[-1] == "aa/copy.md"  # all at one score: in the order of the tree
    monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
    others = [Wiki(wiki.cfg, llm=ContentLLM(UsageTracker())) for _ in range(4)]  # other processes, which never listed
    listed = _listings(monkeypatch)
    try:
        for other in others:
            assert paths(other.search("common", k=8)) == first
        assert listed == []  # the listing of the first stands for theirs
    finally:
        for w in (*others, wiki):
            w.close()


def test_a_hand_edit_after_a_listing_another_process_shared_is_seen_after_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SI, "LISTING_LOOK", 0.5)
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process, which never listed
    time.sleep(0.6)
    target = paths(wiki.search("common", k=1))[0]  # lists: its window is over, and shares the listing
    note = tmp_path / "wiki" / target
    note.write_text(note.read_text(encoding="utf-8").replace("common", "yak"), encoding="utf-8")  # by hand, after that listing
    listed = _listings(monkeypatch)
    try:
        assert other.search("yak", k=3) == []  # within the window of the listing it took: not seen yet
        assert listed == []
        time.sleep(0.6)
        _age(tmp_path / "wiki")
        assert paths(other.search("yak", k=3)) == [target]  # after it: listed, seen
        assert listed == [other.store]
    finally:
        for w in (other, wiki):
            w.close()


def test_a_listing_whose_findings_could_not_be_written_is_not_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A note deleted by hand while another process holds the index's write lock: the process that lists leaves it out but cannot write
    the file, so it shares no listing; another process lists for itself and leaves the note out too."""
    monkeypatch.setattr(SI, "LISTING_LOOK", 0.5)
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    gone = paths(wiki.search("common", k=8))[0]
    time.sleep(0.6)  # the listing shared so far is over
    holder = sqlite3.connect(_index_file(tmp_path), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a writer of another process holds the index all along
    try:
        (tmp_path / "wiki" / gone).unlink()
        assert gone not in paths(wiki.search("common", k=8))  # listed, left out, not written
        assert gone not in paths(other.search("common", k=8))  # within the window of that listing: listed again
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        for w in (other, wiki):
            w.close()


def test_a_note_left_out_does_not_come_back_by_a_listing_older_than_the_one_that_saw_it_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process that saw a note gone and left it out (a writer holds the index) reads the file again after another process wrote it: it
    lists again rather than take the listing another process shared before the note went."""
    wiki = build(tmp_path)
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))
    gone = paths(other.search("common", k=8))[0]  # the other process lists and shares its listing: the note is there
    holder = sqlite3.connect(_index_file(tmp_path), timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        (tmp_path / "wiki" / gone).unlink()
        assert gone not in paths(wiki.search("common", k=8))  # listed after it went: left out
        monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
        holder.execute("UPDATE meta SET v = v + 1 WHERE k = 'gen'")  # another process writes the index: read again from the file
        holder.execute("COMMIT")
        holder.execute("BEGIN IMMEDIATE")
        assert gone not in paths(wiki.search("common", k=8))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        for w in (other, wiki):
            w.close()


def test_a_listing_shared_at_a_later_generation_than_a_reader_holds_is_not_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader inside one state of the file (a read transaction, as a search that another process writes under) holds an earlier
    generation than the one a listing was shared at: what that listing found is not in what it holds, so it lists for itself."""
    wiki = build(tmp_path)
    monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
    reader = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # takes the listing the first one shared when it built the index
    listed = _listings(monkeypatch)
    try:
        assert paths(reader.search("common", k=1)) and listed == []
        index = reader._search.index
        db = index._open()
        db.execute("BEGIN")
        index._disk_gen(db)  # the reader's state of the file starts here
        note = tmp_path / "wiki" / "note-1.md"
        note.write_text(note.read_text(encoding="utf-8").replace("common", "yak"), encoding="utf-8")  # by hand
        wiki._search.index._listed_at = None  # the first one's window is over: it lists, writes the edit, shares its listing
        assert paths(wiki.search("yak", k=1)) == ["note-1.md"] and listed == [wiki.store]
        index.sync(reader.store, reader._search._entry, wait=False)  # in its state of the file the edit is not written
        assert listed == [wiki.store, reader.store]
        db.execute("COMMIT")
    finally:
        for w in (reader, wiki):
            w.close()


def test_the_notes_looked_at_by_meaning_keep_the_order_of_the_tree_as_notes_come_and_go(tmp_path: Path) -> None:
    """The order a process keeps for its notes (`rels`, the candidates by meaning) follows the writes of another process note by note,
    and is the one a process that reads the file anew gives: the order of the tree."""
    wiki = build(tmp_path)
    index = wiki._search.index
    assert index.rels() == [f"note-{i}.md" for i in range(6)]
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process
    try:
        with other.store.lock():
            folder, _ = other.store.create_folder(other.store.load(), "aa", "Kept apart")
            other.store.write_note(folder, title="Zebra", summary="s", body="zebra common", tags=[], source={})
            other.store.write_note(other.store.load(), title="Aardvark", summary="s", body="aardvark common", tags=[], source={})
            other.store.delete_note("note-2.md")
        wiki.search("common", k=3)
        fresh = SI.SearchIndex(index.path)
        try:
            with fresh._mutex:
                fresh._load(fresh._open())
            assert index.rels() == fresh.rels() == sorted(fresh.rels(), key=lambda rel: (S.tree_key(rel), rel))
            assert index.rels()[0] == "aardvark.md" and index.rels()[-1] == "aa/zebra.md" and "note-2.md" not in index.rels()
        finally:
            fresh.close()
    finally:
        for w in (other, wiki):
            w.close()


def test_the_key_of_the_tree_gives_the_order_of_the_listing(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    for rel in ("b.md", "a.md", "C.md", "a/z.md", "a/b/c.md", "a/b/D.md", "ab/x.md", "B/y.md", "a b/w.md", "a-c.md", "a/Zz.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x", encoding="utf-8")
    order, _ = S.WikiStore(root, actor="test").scan_notes({}, 0)
    assert len(order) == 11
    assert sorted(reversed(order), key=S.tree_key) == order


def test_processes_on_different_clocks_do_not_share_a_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The times of a shared listing are read on one monotonic clock: processes that read another one (another time namespace, Linux)
    share no listing and list for themselves."""
    monkeypatch.setattr(SI, "_clock_space", lambda: "one")
    wiki = build(tmp_path)  # lists and shares its listing
    monkeypatch.setattr(SI, "LISTING_LOOK", 60.0)
    monkeypatch.setattr(SI, "_clock_space", lambda: "another")
    other = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # a process on another clock
    listed = _listings(monkeypatch)
    try:
        assert paths(other.search("common", k=8))
        assert listed == [other.store]
    finally:
        for w in (other, wiki):
            w.close()


@pytest.mark.skipif(not os.path.exists("/proc/self/ns/time"), reason="no time namespaces here")
def test_the_clock_of_a_shared_listing_is_the_time_namespace_where_there_is_one() -> None:
    assert SI._clock_space() == str(os.stat("/proc/self/ns/time").st_ino)


def test_the_notes_by_meaning_come_in_the_order_load_gives(tmp_path: Path) -> None:
    """The order `rels` keeps (the candidates by meaning, and the ties) is the order of the tree as `WikiStore.load` gives it: in each
    folder its notes, then its folders, by name, capitals, common prefixes, accents and subfolders included."""
    wiki = build(tmp_path, n=1)
    root = tmp_path / "wiki"
    note = (root / "note-0.md").read_bytes()
    names = ["B.md", "a.md", "ab.md", "a-b.md", "Zeta.md", "été.md", "Äpfel.md", "zürich.md", "a/x.md", "a/B/y.md",
             "a/b2/w.md", "ab/v.md", "A b/u.md", "école/t.md", "Zz/s.md", "a/a.md"]
    for rel in names:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(note)
    _age(root)
    wiki.search("common", k=3)  # lists (the window is 0 here) and writes the notes found
    expected = [n.rel for n in wiki.store.load().all_notes()]
    assert sorted(expected) == sorted([*names, "note-0.md"])
    assert wiki._search.index.rels() == expected
    fresh = Wiki(wiki.cfg, llm=ContentLLM(UsageTracker()))  # another process, which reads the file anew
    try:
        fresh.search("common", k=3)
        assert fresh._search.index.rels() == expected
    finally:
        fresh.close()
        wiki.close()
