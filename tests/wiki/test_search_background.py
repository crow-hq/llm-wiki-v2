# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The first build of the search index in the background (`searchindex.Build`): the searches made meanwhile answer at once from what its
lots committed, the notes the process wrote since and the entries not filed, and say they are partial (`Hits.partial`, `Answer.partial`,
the line of the MCP tools); once the file is built the answers are those of the index whole. Within `BUILD_WINDOW` from the start of the
build every search waits for it, in any process, and never past `BUILD_WINDOW` from its own start: a build that ends by then is
answered whole. One process builds, a writer's notes are in the file when it is done, a build cut by a crash goes on from its lots, and
no search reads the notes or waits for the write lock."""

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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
        # another process builds, and its lease does not say when it began: this one does not build, and does not wait
        assert made == [] and w._search._build is None
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 30.0)
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
    si.BUILD_WINDOW = 60.0
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

    monkeypatch.setattr(si, "BUILD_WINDOW", 30.0)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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

    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
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


def test_a_search_that_read_no_tables_yet_does_not_wait_for_the_first_lot_of_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
    wiki = _random_wiki(tmp_path, 30)
    _no_index(tmp_path)
    gate = Gate(monkeypatch)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    _at_gate(w, gate)  # the build made the tables and holds the write lock in its first lot
    stale = [True]
    real = si.SearchIndex._current

    def current(db: sqlite3.Connection) -> bool:
        if threading.current_thread().name != BUILDER and stale:
            stale.pop()
            return False  # as read a moment before the build made the tables
        return real(db)

    monkeypatch.setattr(si.SearchIndex, "_current", staticmethod(current))
    other = Wiki(wiki.cfg, llm=wiki.llm)  # another reader of the file, which sees it for the first time
    out: list[Hits] = []
    t = threading.Thread(target=lambda: out.append(other.search("alpha", 3)))
    t.start()
    t.join(30)
    waited = t.is_alive()
    gate.open.set()
    t.join(30)
    assert not waited and not stale  # it read the tables made meanwhile, and did not wait for the lot
    assert out and out[0].partial == Partial(0, 30)
    other.close()
    w.close()


class Waits:
    """Records the waits of the searches of this process for a build: for their own (`Build.wait`, the most it could wait) and on the
    lease of another's (`wait_build`, the time left to the end of the window, nothing to wait for when not above 0). `ends`: the end of
    each wait on a lease, as given (no clock read); `builds`: the build of each wait for its own, with the most it could wait."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.left: list[float] = []
        self.ends: list[int] = []
        self.builds: list[tuple[si.Build, float]] = []
        self._changed = threading.Condition()
        real_wait, real_lease = si.Build.wait, si.wait_build

        def wait(build: si.Build, timeout: float) -> bool:
            if threading.current_thread() is not threading.main_thread():  # a search's, not the test's
                self.builds.append((build, timeout))
                self._add(timeout)
            return real_wait(build, timeout)

        def lease(path: Path, until: int) -> bool:
            self.ends.append(until)
            self._add((until - time.monotonic_ns()) / 1e9)
            return real_lease(path, until)

        monkeypatch.setattr(si.Build, "wait", wait)
        monkeypatch.setattr(si, "wait_build", lease)

    def _add(self, left: float) -> None:
        with self._changed:
            self.left.append(left)
            self._changed.notify_all()

    @property
    def waiting(self) -> list[float]:
        return [left for left in self.left if left > 0]

    def reach(self, n: int) -> None:
        """Wait until `n` searches wait for a build (they wait for it: the test does not wait for a time)."""
        with self._changed:
            assert self._changed.wait_for(lambda: len(self.waiting) >= n, 60)


def _searching(w: Wiki, out: list[Hits], query: str = "alpha river") -> threading.Thread:
    t = threading.Thread(target=lambda: out.append(w.search(query, 8)), daemon=True)
    t.start()
    return t


def _write_began(lease: Path, began: int, check: int) -> None:
    with open(lease, "r+b") as f:
        f.seek(si._BEGAN_AT)
        f.write(si._BEGAN.pack(began, check))


def test_the_searches_made_while_the_build_runs_wait_for_it_within_the_window_and_answer_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(si, "BUILD_WINDOW", 60.0)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 40)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=15)  # one lot committed; the build holds the write lock in the second
    waits = Waits(monkeypatch)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    first: list[Hits] = []
    later: list[Hits] = []
    t1 = _searching(w, first)  # it starts the build and waits for it
    assert gate.reached.wait(60)
    waits.reach(1)
    t2 = _searching(w, later)  # a later search of the same process: it waits behind the first, which holds the index's look
    build = w._search._build
    assert build is not None and not build.finished and first == later == []
    assert len(waits.waiting) == 1 and waits.waiting[0] <= 60.0
    gate.open.set()
    t1.join(60)
    t2.join(60)
    assert not t1.is_alive() and not t2.is_alive()
    expected = _expected(w, "alpha river", 8)
    for hits in (first[0], later[0]):
        assert hits.partial is None and _paths(hits) == expected
    assert w._search.index.ready()
    w.close()


def test_past_the_window_a_search_answers_at_once_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.0)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 40)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=25)  # two lots committed
    w = Wiki(wiki.cfg, llm=wiki.llm)
    other = Wiki(wiki.cfg, llm=wiki.llm)  # another reader, which does not hold the lease
    _at_gate(w, gate)
    build = w._search._build
    assert build is not None
    monkeypatch.setattr(si, "BUILD_WINDOW", 60.0)
    build.began -= 61 * 10**9  # as a build that began longer ago than the window: so it says in its lease too
    _write_began(si.lease_of(_index_file(tmp_path)), build.began, build.began ^ si._LISTED_MASK)
    waits = Waits(monkeypatch)
    committed = _committed(tmp_path)
    for reader in (w, other):
        hits = reader.search("alpha river", 8)  # the gate is closed: an answer at all is an answer that did not wait for the build
        assert hits.partial == Partial(20, 40) and _paths(hits) == _expected(w, "alpha river", 8, within=committed)
    assert waits.left and waits.waiting == []  # each looked at the window, and found it over
    gate.open.set()
    assert build.wait(60) and not build.failed
    _age(tmp_path / "wiki")
    assert other.search("alpha river", 8).partial is None
    other.close()
    w.close()


WAITER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from llmw2 import Wiki, WikiConfig
    from llmw2 import searchindex as si
    from llmw2.models.usage import UsageTracker
    from tests.wiki.fakes import FakeLLM
    si.BUILD_WINDOW = 60.0
    real = si.wait_build
    def wait_build(path, until):
        print("waiting", flush=True)
        return real(path, until)
    si.wait_build = wait_build
    hits = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=FakeLLM(UsageTracker())).search("alpha river", 8)
    print(hits.partial, flush=True)
    print(",".join(h.path for h in hits), flush=True)
    """
)


def test_a_search_of_a_process_that_does_not_build_waits_within_the_same_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BUILD_WINDOW", 60.0)
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 40)
    _no_index(tmp_path)
    gate = Gate(monkeypatch, after=15)
    waits = Waits(monkeypatch)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    builder: list[Hits] = []
    t1 = _searching(w, builder)
    assert gate.reached.wait(60)
    waits.reach(1)
    other = Wiki(wiki.cfg, llm=wiki.llm)  # another index of this process: the lease is not its own
    reader: list[Hits] = []
    t2 = _searching(other, reader)
    waits.reach(2)
    build = w._search._build
    assert build is not None and si.build_began(_index_file(tmp_path)) == build.began
    # the window runs from the start of the build, not from each search: the reader waits on the lease to the end the builder's has
    # (exact, from the lease's bytes), and not longer than the builder (the clock may give both the same tick)
    assert waits.ends == [build.began + int(60.0 * 1e9)]
    assert waits.waiting[1] <= waits.waiting[0] <= 60.0
    script = tmp_path / "waiter.py"
    script.write_text(WAITER, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path / "wiki")], env=_env(), stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "waiting"  # on the lease, in the window its bytes give
        assert not build.finished and builder == reader == []
        gate.open.set()
        out, _ = proc.communicate(timeout=120)
    finally:
        gate.open.set()
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0
    t1.join(60)
    t2.join(60)
    expected = _expected(w, "alpha river", 8)
    assert out.splitlines() == ["None", ",".join(expected)]
    for hits in (builder[0], reader[0]):
        assert hits.partial is None and _paths(hits) == expected
    assert other._search._build is None  # it waited, and did not build
    other.close()
    w.close()


DYING = textwrap.dedent(
    """
    import os, sys, threading
    from pathlib import Path
    from llmw2 import Wiki, WikiConfig
    from llmw2 import searchindex as si
    from llmw2.models.usage import UsageTracker
    from llmw2.search import NoteSearch
    from tests.wiki.fakes import FakeLLM
    si.BATCH = 10
    si.BUILD_WINDOW = 0.0
    real, reads = NoteSearch._entry, []
    def entry(ns, rel, path, stamp):
        if threading.current_thread().name == "llmw2-index-build":
            reads.append(rel)
            if len(reads) == 15:  # one lot committed; it holds the write lock in the second
                print("building", flush=True)
                sys.stdin.readline()
                os._exit(9)
        return real(ns, rel, path, stamp)
    NoteSearch._entry = entry
    w = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=FakeLLM(UsageTracker()))
    w.search("alpha")
    threading.Event().wait()
    """
)


def test_a_search_that_takes_the_build_over_from_a_dead_process_waits_no_longer_than_its_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee against waits one after the other in a search: one that waited on the lease of a builder that died, and so builds
    the file itself, waits for its own build only to the end of the window it waited in, not for a window of its own."""
    monkeypatch.setattr(si, "BUILD_WINDOW", 60.0)
    wiki = _random_wiki(tmp_path, 40)
    _no_index(tmp_path)
    script = tmp_path / "dying.py"
    script.write_text(DYING, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path / "wiki")], env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "building"
        lease = si.lease_of(_index_file(tmp_path))
        began = time.monotonic_ns() - 30 * 10**9  # its build began 30 s ago: 30 s of the window are left
        _write_began(lease, began, began ^ si._LISTED_MASK)
        waits = Waits(monkeypatch)
        w = Wiki(wiki.cfg, llm=wiki.llm)
        out: list[Hits] = []
        t = _searching(w, out)
        waits.reach(1)  # it waits on the lease of the other process
        assert waits.ends == [began + int(60.0 * 1e9)]
        assert proc.stdin is not None
        proc.stdin.write("die\n")
        proc.stdin.flush()
        assert proc.wait(60) == 9
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(30)
    t.join(60)
    assert not t.is_alive()
    ((build, most),) = waits.builds  # the lease let go of, it took the build over
    assert most <= (waits.ends[0] - build.began) / 1e9 + 1e-6 < 31.0  # within what was left of the first window, not 60 s more
    assert out and out[0].partial is None and _paths(out[0]) == _expected(w, "alpha river", 8)
    w.close()


@pytest.mark.parametrize("began", ["never written", "torn", "to come"])
def test_a_lease_that_does_not_say_when_its_build_began_is_not_waited_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, began: str
) -> None:
    monkeypatch.setattr(si, "BUILD_WINDOW", 60.0)
    wiki = _random_wiki(tmp_path, 20)
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
        now = time.monotonic_ns()
        if began == "torn":
            _write_began(lease, now, now ^ si._LISTED_MASK ^ 1)
        elif began == "to come":  # a lease written before the machine started again
            _write_began(lease, now + 3600 * 10**9, (now + 3600 * 10**9) ^ si._LISTED_MASK)
        assert si.build_began(_index_file(tmp_path)) is None
        waits = Waits(monkeypatch)
        w = Wiki(wiki.cfg, llm=wiki.llm)
        hits = w.search("alpha river", 8)  # the lease is held to the end of the test: an answer at all is one that did not wait
        assert hits.partial is not None and list(hits) == [] and waits.left == []
        w.close()
    finally:
        other.kill()
        other.wait(30)


def test_close_waits_for_a_build_that_marked_the_file_complete_and_still_closes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A search that reads the file complete while the build's thread still closes its connection keeps the build, and `close` waits
    for that thread: no connection of the build stays open after it (on Windows the file could not be deleted)."""
    monkeypatch.setattr(si, "BUILD_WINDOW", 0.05)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    closing_, release, closed = threading.Event(), threading.Event(), threading.Event()
    real_close, real_stop = si._Building.close, si.Build.stop

    def close(index: si._Building) -> None:
        if threading.current_thread().name == BUILDER:  # the file is marked complete: the build holds before it closes its connection
            closing_.set()
            assert release.wait(60)
        real_close(index)
        if threading.current_thread().name == BUILDER:
            closed.set()

    def stop(build: si.Build, wait: float) -> None:
        release.set()  # only a stop lets the build close: one never stopped would leave its connection open
        real_stop(build, wait)

    monkeypatch.setattr(si._Building, "close", close)
    monkeypatch.setattr(si.Build, "stop", stop)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    try:
        w._search._building(w.store)  # starts the build, and waits for it the window only
        assert closing_.wait(60)
        build = w._search._build
        hits = w.search("alpha river", 8)
        assert hits.partial is None and _paths(hits) == _expected(w, "alpha river", 8)  # the file is complete: as before
        assert w._search._build is build and not build.finished  # its thread is alive: the build is kept
        w.close()
        assert closed.is_set() and build.finished  # close waited for the build's connection to close
        _index_file(tmp_path).unlink()  # nothing holds the file
    finally:
        release.set()


def test_a_search_that_began_before_the_build_waits_no_longer_than_the_window_from_its_own_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search that began before the build (while the wiki was opened or listed) waits for it until `BUILD_WINDOW` after its own
    start, not after the build's: it never takes the window on top of what came before. The wait it asks for is recorded, none slept."""
    monkeypatch.setattr(si, "BUILD_WINDOW", 5.0)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    gate = Gate(monkeypatch)
    waits: list[float] = []
    monkeypatch.setattr(si.Build, "wait", lambda build, timeout: waits.append(timeout) or False)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    try:
        began = time.monotonic_ns() - int(4.5e9)  # the search began 4.5 s before the build
        found = w._search.find(w.store, "alpha river", 8, began=began)
        build = w._search._build
        assert build is not None and build.began > began
        assert found.partial is not None and len(waits) == 1
        assert waits[0] <= (began + int(5e9) - build.began) / 1e9 <= 0.5  # 5 s from the search's start, not 5 s from the build's
    finally:
        gate.open.set()
        w.close()


def test_a_search_waits_for_another_process_no_longer_than_the_window_from_its_own_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same when another process builds: the wait on its lease ends `BUILD_WINDOW` after the search began, though the lease says the
    build began later (the clock of the lease is patched, the wait recorded)."""
    monkeypatch.setattr(si, "BUILD_WINDOW", 5.0)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    began = time.monotonic_ns() - int(4e9)  # the search began 4 s before the build of the other process
    ends: list[int] = []
    monkeypatch.setattr(si.Build, "start", lambda build: False)  # another process holds the lease
    monkeypatch.setattr(si, "build_began", lambda path: began + int(4e9))
    monkeypatch.setattr(si, "wait_build", lambda path, until: ends.append(until) or False)
    found = w._search.find(w.store, "alpha river", 8, began=began)
    assert found.partial is not None and w._search._build is None
    assert ends == [began + int(5e9)]  # not the build's start + 5 s
    w.close()


def test_wiki_search_counts_the_wait_from_its_own_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Wiki.search` takes the time it began and passes it on: the wait on another process's build ends `BUILD_WINDOW` after that time,
    not after the moment `NoteSearch.find` was entered (the clock of `Wiki.search` is patched 4 s back, the wait recorded)."""
    import llmw2.wiki as wiki_module

    monkeypatch.setattr(si, "BUILD_WINDOW", 5.0)
    wiki = _random_wiki(tmp_path, 20)
    _no_index(tmp_path)
    w = Wiki(wiki.cfg, llm=wiki.llm)
    began = time.monotonic_ns() - int(4e9)

    class Clock:  # `time` in the module of `Wiki`, but for when `search` began
        def __getattr__(self, name: str) -> Any:
            return getattr(time, name)

        def monotonic_ns(self) -> int:
            return began

    ends: list[int] = []
    monkeypatch.setattr(wiki_module, "time", Clock())
    monkeypatch.setattr(si.Build, "start", lambda build: False)  # another process holds the lease
    monkeypatch.setattr(si, "build_began", lambda path: began + int(4e9))
    monkeypatch.setattr(si, "wait_build", lambda path, until: ends.append(until) or False)
    hits = w.search("alpha river", 8)
    assert hits.partial is not None and ends == [began + int(5e9)]
    w.close()
