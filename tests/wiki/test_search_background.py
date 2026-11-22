# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The first build of the search index in the background (`searchindex.Build`): the searches made meanwhile answer at once from what its
lots committed, the notes the process wrote since and the entries not filed, and say they are partial (`Hits.partial`, `Answer.partial`,
the line of the MCP tools); once the file is built the answers are those of the index whole. One process builds, a writer's notes are in
the file when it is done, a build cut by a crash goes on from its lots, and no search reads the notes or waits for the write lock."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import llmw2
from llmw2 import Wiki
from llmw2 import searchindex as si
from llmw2.agents.researcher import NO_ANSWER, PARTIAL_HINT
from llmw2.bundle.filelock import FileLock
from llmw2.memory import answered, listed
from llmw2.search import Hit, Hits, NoteSearch, Partial
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_memory import call
from tests.wiki.test_researcher_crow import ANSWER, QUESTION, make_wiki
from tests.wiki.test_search_index import _age, _expected, _fresh_rows, _index_file, _random_wiki, _rows

BUILDER = "llmw2-index-build"
QUERIES: list[tuple[str, dict[str, Any] | None]] = [
    ("alpha river", None),
    ("word3 word7 tiger", None),
    ("garden market delta", {"team": "ops"}),
    ("word11 gamma", {"team": "hr"}),
]


class Gate:
    """Holds the build in the background at its read number `after` + 1 (inside a lot: it holds the file's write lock), until opened;
    counts the reads of notes made by every other thread (`others`)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, after: int = 0) -> None:
        self.open = threading.Event()
        self.reached = threading.Event()
        self.reads = 0
        self.others = 0
        real = NoteSearch._entry

        def entry(ns: NoteSearch, rel: str, path: str, stamp: tuple[int, int, int]) -> tuple[str, str, str, str] | None:
            if threading.current_thread().name == BUILDER:
                self.reads += 1
                if self.reads > after:
                    self.reached.set()
                    assert self.open.wait(60)
            else:
                self.others += 1
            return real(ns, rel, path, stamp)

        monkeypatch.setattr(NoteSearch, "_entry", entry)


def _at_gate(w: Wiki, gate: Gate) -> None:
    """Start the build in the background, as the first search does, and wait until it holds at the gate: a test that searches then
    finds the same on a slow machine, where the first search could answer before the build gets there."""
    w._search._building(w.store)
    assert gate.reached.wait(60)


def _paths(hits: list[Hit]) -> list[str]:
    return [h.path for h in hits]


def _no_index(tmp_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_index_file(tmp_path)}{suffix}").unlink(missing_ok=True)


def _committed(tmp_path: Path) -> list[str]:
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        return [rel for (rel,) in db.execute("SELECT rel FROM docs WHERE bad = 0")]


def test_searches_while_the_file_is_built_are_partial_and_whole_once_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 60)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=25)  # two lots committed; the build holds the write lock in the third
    w = Wiki(wiki.cfg, llm=wiki.llm)

    _at_gate(w, gate)
    first = w.search("alpha river", 8)
    build = w._search._build
    assert build is not None and build._index is not None and build._index._held is not None
    assert build._index._held.in_transaction  # the search above did not wait for the write lock
    assert first.partial == Partial(20, 60)
    committed = _committed(tmp_path)
    assert len(committed) == 20
    for q, where in QUERIES:  # what the two lots hold, ranked as an index of just them
        within = committed if where is None else [rel for rel in committed if rel.startswith(where["team"] + "/")]
        hits = w.search(q, 8, where)
        assert hits.partial is not None and _paths(hits) == _expected(w, q, 8, within=within)
    said = w.status()["warnings"]
    assert any("being built in the background (" in s for s in said), said

    # meanwhile: a note written by the library, one it deletes, one deleted by hand
    with w.store.lock():
        late = w.store.write_note(
            next(f for f in w.store.load().subfolders if f.rel == "ops"), title="Late note", summary="zqlate", body="zqlate alpha",
            tags=[], source={"resource": "/raw/y.md"}, fields={"team": "ops"},
        )  # fmt: skip
    found = w.search("zqlate", 3)
    assert _paths(found) == [late.rel] and found.partial is not None
    assert _paths(w.search("zqlate", 3, {"team": "hr"})) == []
    changed = next(rel for rel in sorted(committed) if "alpha" in (tmp_path / "wiki" / rel).read_text(encoding="utf-8"))
    path = tmp_path / "wiki" / changed
    path.write_text(path.read_text(encoding="utf-8").replace("alpha", "zqchanged"), encoding="utf-8")
    w._search.written(w.store, [changed])  # as the hook of a section that rewrote it
    assert changed not in _paths(w.search("alpha", 50))  # the file's row of its old text is not served
    assert _paths(w.search("zqchanged", 3)) == [changed]
    gone_by_library, gone_by_hand = [rel for rel in sorted(committed) if rel != changed][:2]
    w.delete_note(gone_by_library)
    (tmp_path / "wiki" / gone_by_hand).unlink()
    for q, where in QUERIES:
        hits = _paths(w.search(q, 50, where))
        assert gone_by_library not in hits and gone_by_hand not in hits
    assert gate.others == 2  # no search read a note (no scan of the wiki): the hook read the late note and the changed one, once each

    gate.open.set()
    assert build.wait(30) and not build.failed
    _age(tmp_path / "wiki")
    assert all(w.search(q, 8, where).partial is None for q, where in QUERIES)
    assert _paths(w.search("alpha river", 8)) == _expected(w, "alpha river", 8)
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)
    assert not any("search index" in s for s in w.status()["warnings"])


def test_a_note_written_while_the_build_runs_is_in_the_file_when_it_is_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    wiki = _random_wiki(tmp_path, 30)
    _no_index(tmp_path)
    gate = Gate(monkeypatch)  # the build listed the wiki and holds at its first read
    w = Wiki(wiki.cfg, llm=wiki.llm)
    _at_gate(w, gate)
    assert w.search("alpha").partial == Partial(0, 30)
    with w.store.lock():  # a section, as the library writes: its hook tells the index, which sees no file built whole and leaves it
        note = w.store.write_note(
            w.store.load(), title="Written meanwhile", summary="zqwritten", body="zqwritten alpha", tags=[], source={"resource": "/z.md"}
        )
    assert _paths(w.search("zqwritten", 3)) == [note.rel]  # the search has it at once, from the hook
    gate.open.set()
    build = w._search._build
    assert build is not None and build.wait(30) and not build.failed

    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        assert db.execute("SELECT COUNT(*) FROM docs WHERE rel = ?", (note.rel,)).fetchone()[0] == 1
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)
    hits = w.search("zqwritten", 3)
    assert _paths(hits) == [note.rel] and hits.partial is None


HOLDER = textwrap.dedent(
    """
    import sys
    from llmw2.bundle.filelock import FileLock
    lease = FileLock(sys.argv[1])
    assert lease.try_acquire()
    print("held", flush=True)
    sys.stdin.readline()
    """
)


def _env() -> dict[str, str]:
    parts = [str(Path(llmw2.__file__).parents[1]), str(Path(__file__).parents[2]), os.environ.get("PYTHONPATH", "")]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(parts))


def test_one_process_builds_the_others_answer_partial_then_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _random_wiki(tmp_path, 40)
    _no_index(tmp_path)
    lease = si.lease_of(_index_file(tmp_path))
    lease.parent.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "holder.py"
    script.write_text(HOLDER, encoding="utf-8")
    other = subprocess.Popen(
        [sys.executable, str(script), str(lease)], env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert other.stdout is not None and other.stdout.readline().strip() == "held"
        made: list[object] = []
        real = si._Building.__init__

        def counted(self: si._Building, *args: Any) -> None:
            made.append(self)
            real(self, *args)

        monkeypatch.setattr(si._Building, "__init__", counted)
        w = Wiki(wiki.cfg, llm=wiki.llm)
        during = w.search("alpha river", 8)
        assert made == [] and w._search._build is None  # another process builds: this one does not, and does not wait
        assert during.partial is not None and list(during) == []  # its lots hold nothing yet
        assert any("being built" in s for s in w.status()["warnings"])
    finally:
        other.kill()  # it dies with the lease: the kernel lets go of it
        other.wait(30)
    probe = FileLock(lease)
    end = time.monotonic() + 30
    while not probe.try_acquire():  # Windows lets go of a dead process's locks a moment after it ended
        assert time.monotonic() < end
        time.sleep(0.05)
    probe.release()
    monkeypatch.setattr(si, "BUILD_WAIT", 30.0)
    after = w.search("alpha river", 8)  # the next search takes the lease and builds, and waits for so small a build
    assert len(made) == 1 and after.partial is None and w._search.index.ready()
    assert _paths(after) == _expected(w, "alpha river", 8)
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)


CRASHER = textwrap.dedent(
    """
    import os, sys, threading
    from pathlib import Path
    from llmw2 import Wiki, WikiConfig
    from llmw2 import searchindex as si
    from llmw2.models.usage import UsageTracker
    from llmw2.search import NoteSearch
    from tests.wiki.fakes import FakeLLM
    si.BATCH = 10
    si.BUILD_WAIT = 60.0
    real, reads = NoteSearch._entry, []
    def entry(ns, rel, path, stamp):
        if threading.current_thread().name == "llmw2-index-build":
            reads.append(rel)
            if len(reads) == 33:
                os._exit(9)  # the process dies in its fourth lot
        return real(ns, rel, path, stamp)
    NoteSearch._entry = entry
    Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=FakeLLM(UsageTracker())).search("alpha")
    """
)


def test_a_build_cut_by_a_crash_goes_on_from_its_lots_at_the_next_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _random_wiki(tmp_path, 45)
    _no_index(tmp_path)
    script = tmp_path / "crasher.py"
    script.write_text(CRASHER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(script), str(tmp_path / "wiki")], env=_env(), capture_output=True, text=True, timeout=120)
    assert done.returncode == 9, done.stderr[-1500:]
    assert len(_committed(tmp_path)) == 30  # three lots committed, the fourth rolled back
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        assert db.execute("SELECT 1 FROM meta WHERE k = 'built'").fetchone() is None
    assert "not complete" in (si.build_state(_index_file(tmp_path)) or "")

    monkeypatch.setattr(si, "BUILD_WAIT", 30.0)
    reads: list[str] = []
    real = NoteSearch._entry

    def entry(ns: NoteSearch, rel: str, path: str, stamp: tuple[int, int, int]) -> tuple[str, str, str, str] | None:
        if threading.current_thread().name == BUILDER:
            reads.append(rel)
        return real(ns, rel, path, stamp)

    monkeypatch.setattr(NoteSearch, "_entry", entry)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    hits = w.search("alpha river", 8)
    assert _paths(hits) == _expected(w, "alpha river", 8) and hits.partial is None
    assert w._search.index.ready() and len(reads) == 15  # only the notes the lots did not commit
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)
    assert si.build_state(_index_file(tmp_path)) is None


def test_a_purge_reads_again_the_notes_kept_while_the_file_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    gate = Gate(monkeypatch)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    _at_gate(w, gate)
    w.search("alpha")
    with w.store.lock():
        note = w.store.write_note(w.store.load(), title="Gone", summary="zqpurged", body="zqpurged", tags=[], source={"resource": "/p.md"})
    assert w._search._recent is not None and w._search._recent[note.rel] is not None
    (tmp_path / "wiki" / note.rel).unlink()  # as a purge takes it out
    w._search.forget(w.store, 0.2)  # the build holds the write lock: the file is left to later, the text in memory goes now
    assert w._search._recent[note.rel] is None
    assert _paths(w.search("zqpurged", 3)) == []
    gate.open.set()
    w.close()


def test_a_note_purged_from_a_lot_not_marked_yet_leaves_the_file_when_the_build_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 30)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=15)  # one lot committed, the file not marked; the build holds the write lock in the second
    w = Wiki(wiki.cfg, llm=wiki.llm)
    _at_gate(w, gate)
    w.search("alpha")
    secret = sorted(_committed(tmp_path))[0]
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        (doc,) = db.execute("SELECT id FROM docs WHERE rel = ?", (secret,)).fetchone()
    (tmp_path / "wiki" / secret).unlink()  # the purge took its files out
    assert w._search.forget(w.store, 0.2) is False  # the index's lock is held by the build: the purge says "index" pending
    assert secret in _committed(tmp_path)

    gate.open.set()
    build = w._search._build
    assert build is not None and build.wait(30) and not build.failed
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:  # the build's last listing took it out, rows zeroed
        assert db.execute("SELECT COUNT(*) FROM docs WHERE rel = ?", (secret,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM docbody WHERE id = ?", (doc,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM postings WHERE doc = ?", (doc,)).fetchone()[0] == 0
    assert w._search.forget(w.store, None) is True  # the filer's later look completes the purge: the WAL is cut to nothing
    wal = Path(f"{_index_file(tmp_path)}-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)
    w.close()


def test_a_build_that_fails_in_the_background_is_made_by_the_searches_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)

    def broken(self: si._Building, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no build here")

    monkeypatch.setattr(si._Building, "sync", broken)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    hits = w.search("alpha river", 8)
    assert _paths(hits) == _expected(w, "alpha river", 8) and hits.partial is None
    assert w._search._background is False and w._search.index._built
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)


def test_an_answer_found_while_the_file_is_built_says_its_notes_may_be_incomplete(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    monkeypatch.setattr(si, "BATCH", 1)
    wiki = make_wiki(bundle, llm, classifier)
    gate = Gate(monkeypatch, after=3)  # three of the four notes committed
    reply = "Revenue is booked monthly [finance/revenue-policy.md]."
    llm.add(ANSWER, reply, reply)
    _at_gate(wiki, gate)

    during = wiki.ask(QUESTION)
    assert during.partial is not None and during.text.endswith(PARTIAL_HINT)
    assert answered(during)["partial"] == during.partial and answered(during)["answer"].endswith(PARTIAL_HINT)

    gate.open.set()
    build = wiki._search._build
    assert build is not None and build.wait(30)
    after = wiki.ask(QUESTION)
    assert after.partial is None and after.text == reply and "partial" not in answered(after)
    wiki.close()


def test_an_answer_whose_search_finds_nothing_while_the_file_is_built_does_not_walk_the_folders(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    monkeypatch.setattr(si, "BATCH", 1)
    wiki = make_wiki(bundle, llm, classifier)
    gate = Gate(monkeypatch)
    loads = []
    real = wiki.store.load
    monkeypatch.setattr(wiki.store, "load", lambda *a, **k: loads.append(1) or real(*a, **k))
    _at_gate(wiki, gate)

    during = wiki.ask("zebra giraffe")  # no note has these words: with the index whole, the LLM walks the folders
    researcher = wiki.researcher()
    assert researcher.ask("zebra giraffe").partial is not None

    assert during.notes == [] and during.text == f"{NO_ANSWER}\n\n{PARTIAL_HINT}" and during.partial is not None
    assert llm.ops == [] and loads == []  # no walk, and no load of the tree for one
    assert [(d.step, d.decider, d.fallback) for d in during.decisions] == [("select", "code", True)]
    gate.open.set()
    build = wiki._search._build
    assert build is not None and build.wait(30)
    llm.add("researcher/navigate", {"reasoning": "Nothing here is about zebras."}, {"reasoning": "Nothing about zebras."})
    after = wiki.ask("zebra giraffe")
    assert after.partial is None and after.text == NO_ANSWER and llm.ops == ["researcher/navigate"]
    again = researcher.ask("zebra giraffe")  # a researcher asked again: the build it saw is over
    assert again.partial is None and again.text == NO_ANSWER and llm.ops == ["researcher/navigate"] * 2
    wiki.close()


def test_the_mcp_search_says_partial_in_a_line_and_in_its_structured_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hits = Hits([Hit("a.md", "A", "about a", 1)])
    assert listed(hits) == [{"path": "a.md", "title": "A", "summary": "about a", "rank": 1}]  # not partial: as before

    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 30)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=15)  # one lot committed
    w = Wiki(wiki.cfg, llm=wiki.llm)
    _at_gate(w, gate)
    out = call(w, "search", {"query": "alpha river", "k": 8})  # through the SDK: its client and its check of the structured content
    assert not out.is_error
    texts = [getattr(c, "text", "") for c in out.content]
    expected = _expected(w, "alpha river", 8, within=_committed(tmp_path))
    assert [h["path"] for h in out.structured_content["result"]] == expected and len(texts) == len(expected) + 1
    assert texts[-1] == "search index being built (10 of 30 notes): the results may be partial"
    assert out.structured_content["partial"] == {"indexed": 10, "notes": 30, "message": texts[-1]}
    gate.open.set()
    build = w._search._build
    assert build is not None and build.wait(30)
    whole = call(w, "search", {"query": "alpha river", "k": 8})
    assert "partial" not in whole.structured_content and len(whole.content) == len(whole.structured_content["result"])


def test_the_file_is_read_whole_only_once_the_notes_left_to_the_build_are_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window after the mark "built": a note another process wrote while it saw no file built whole (it left the note to the build)
    is put in by the build's last listing, which waits for a writer that holds the lock; until then a search is still partial."""
    monkeypatch.setattr(si, "BUILD_WAIT", 0.05)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    window, searched = threading.Event(), threading.Event()
    late = tmp_path / "wiki" / "ops" / "zq-window.md"
    real = si._Building.sync
    calls: list[int] = []

    def holder() -> None:  # a writer of another process holds the write lock
        with closing(sqlite3.connect(_index_file(tmp_path), timeout=30, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            window.set()
            searched.wait(30)
            db.execute("COMMIT")

    def sync(self: si._Building, store: Any, read: Any, **kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 2:  # the file is marked built; the build lists the wiki once more
            source = next((tmp_path / "wiki" / "ops").glob("*.md"))
            late.write_text(source.read_text(encoding="utf-8") + "\nzqwindow\n", encoding="utf-8")  # left to the build by its writer
            threading.Thread(target=holder, daemon=True).start()
            assert window.wait(30)  # the writer holds the lock before the build lists again
        real(self, store, read, **kwargs)

    monkeypatch.setattr(si._Building, "sync", sync)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    w.search("alpha")
    assert window.wait(30)
    during = w.search("zqwindow", 3)
    assert during.partial is not None  # built, not complete: still partial
    searched.set()
    build = w._search._build
    assert build is not None and build.wait(30) and not build.failed
    assert "ops/zq-window.md" in _committed(tmp_path)  # put in by the build itself, before any search could
    hits = w.search("zqwindow", 3)
    assert hits.partial is None and _paths(hits) == ["ops/zq-window.md"]
    assert _rows(_index_file(tmp_path)) == _fresh_rows(w)
