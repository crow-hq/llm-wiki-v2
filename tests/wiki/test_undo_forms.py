# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The undo log of a section: no text in it, the images in the trash, one declaration for the files a filing makes, the preallocated
segment form, a rebuild that keeps no log for each document, and the bundle's lock."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import base64
import contextvars
import multiprocessing
import os
import shutil
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from llmw2 import Item
from llmw2.bundle import journal, txn
from llmw2.bundle.check import check
from llmw2.bundle.filelock import FileLock
from llmw2.bundle.store import WikiStore, write_atomic
from tests.wiki import txn_child
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.problems import errors
from tests.wiki.test_librarian_crow import add_folder, draft, make_wiki, uncertain_alpha
from tests.wiki.test_txn import digest

SECRET = "ZEBRA-QUINCE-4417 a sentence of the note that nothing but the note may hold"


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    txn_child.setup(root)
    return root


@pytest.fixture(params=["line", "segment"])
def form(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, base: Path) -> str:
    monkeypatch.setattr(txn, "PREALLOCATED", request.param == "segment")
    if request.param == "line":  # the setup made the log in the default form of the system: start from none, as a bundle of that form would
        (base / txn.UNDO_FILE).unlink(missing_ok=True)
    return str(request.param)


def everything(root: Path) -> bytes:
    """Every byte of the log files under `.llmw2/journal`, whole files (so the part of a preallocated log beyond its tail too). The
    trash is the images of files the section is about to replace: it has the old file, never a copy of the text in a record."""
    out = b""
    for path in sorted((root / ".llmw2" / "journal").glob("*")):
        if path.is_file() and path.suffix != ".lock":
            out += path.read_bytes()
    return out


def test_the_log_holds_no_text(base: Path, form: str) -> None:
    note = base / "a" / "note-2.md"
    write_atomic(note, SECRET + "\n")  # the text a rewrite is about to replace is on the disk; the log must not copy it
    section = txn.Txn(base, id=None)
    with txn.active(section):
        write_atomic(note, "something else\n")
        txn.append(base / "log.md", "* **Created**: " + SECRET + "\n")
        txn.append(base / "log.md", "* **Created**: " + SECRET[::-1] + "\n")
    blob = everything(base)
    assert SECRET.encode() not in blob and SECRET[::-1].encode() not in blob
    assert base64.b64encode(SECRET.encode()) not in blob and base64.b64encode(("* **Created**: " + SECRET + "\n").encode()) not in blob
    records = txn.read_records(base / txn.UNDO_FILE)
    assert {"copy", "len"} <= {r["t"] for r in records}
    assert all("data" not in r and "add" not in r for r in records)
    done = txn.recover(base)
    assert done.rolled_back and note.read_text(encoding="utf-8") == SECRET + "\n" and done.conflicts == []
    assert not any((base / txn.TRASH_DIR).iterdir())  # the images go when the log is over


def test_a_rewritten_file_is_kept_as_a_link_and_a_copy_where_there_is_none(base: Path, form: str, monkeypatch: pytest.MonkeyPatch) -> None:
    note = base / "a" / "note-3.md"
    was = note.read_bytes()
    section = txn.Txn(base, id=None)
    with txn.active(section):
        write_atomic(note, "new text\n")
    kept = list((base / txn.TRASH_DIR).iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == was
    assert os.path.samefile(kept[0], kept[0]) and kept[0].stat().st_nlink == 1  # the file was replaced: the link is the only name left
    txn.recover(base)
    assert note.read_bytes() == was
    # no hard links: a copy, flushed, with the same effect
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no links here")))
    section = txn.Txn(base, id=None)
    with txn.active(section):
        write_atomic(note, "new text again\n")
    assert [p.read_bytes() for p in (base / txn.TRASH_DIR).iterdir()] == [was]
    txn.recover(base)
    assert note.read_bytes() == was


def test_the_image_that_is_gone_is_a_conflict_not_a_guess(base: Path, form: str) -> None:
    note = base / "a" / "note-3.md"
    section = txn.Txn(base, id=None)
    with txn.active(section):
        write_atomic(note, "new text\n")
    for p in (base / txn.TRASH_DIR).iterdir():
        p.unlink()
    done = txn.recover(base)
    assert len(done.conflicts) == 1 and "not kept" in done.conflicts[0]
    assert note.read_text(encoding="utf-8") == "new text\n"


def test_a_log_of_an_earlier_form_that_holds_the_text_is_still_read(base: Path) -> None:
    """Records with `data` and `add` (base64), as the first form of the log wrote them, are undone as before."""
    note = base / "a" / "note-3.md"
    was = note.read_bytes()
    log_was = (base / "log.md").read_bytes()
    write_atomic(note, "changed\n")
    with open(base / "log.md", "ab") as f:
        f.write(b"* **Created**: one\n")
    records = [
        {"t": "copy", "p": "a/note-3.md", "data": base64.b64encode(was).decode(), "pre": txn.digest(was), "post": txn.digest(b"changed\n")},
        {"t": "len", "p": "log.md", "n": len(log_was), "add": base64.b64encode(b"* **Created**: one\n").decode()},
    ]
    (base / txn.UNDO_FILE).write_bytes(b"".join(txn._encode(r) for r in records))
    done = txn.recover(base)
    assert done.rolled_back and done.conflicts == [] and note.read_bytes() == was and (base / "log.md").read_bytes() == log_was


def test_one_record_declares_what_a_filing_makes_and_appends_to(base: Path, form: str) -> None:
    store = WikiStore(base, actor="test")
    folder = store.load().find(txn_child.GROUP)
    assert folder is not None

    def flushes(declare: bool) -> int:
        work = base.parent / ("with" if declare else "without")
        import shutil

        shutil.copytree(base, work)
        st = WikiStore(work, actor="test")
        fold = st.load().find(txn_child.GROUP)
        assert fold is not None
        count = 0
        real_flush = journal._FLUSH
        real_fsync = os.fsync

        def counted_flush(fd: int) -> None:
            nonlocal count
            count += 1
            real_flush(fd)

        def counted_fsync(fd: int) -> None:
            nonlocal count
            count += 1
            real_fsync(fd)

        journal._FLUSH, os.fsync = counted_flush, counted_fsync  # type: ignore[assignment]
        try:
            with st.lock():
                raw_slug = note_slug = None
                if declare:
                    raw_slug, note_slug = st.declare_filing(fold, raw_title="New", note_title="New")
                raw = st.save_raw("A new text.", title="New", resource=None, slug=raw_slug)
                body = "# Overview\n\nFacts."
                st.write_note(fold, title="New", summary="s", body=body, tags=[], source={"resource": raw}, slug=note_slug)
                st.append_log("Created", "New")
        finally:
            journal._FLUSH, os.fsync = real_flush, real_fsync
        assert not errors(check(work))
        return count

    plain, declared = flushes(False), flushes(True)
    assert declared < plain, (declared, plain)
    # and what was declared is a single record
    work = base.parent / "one"
    import shutil

    shutil.copytree(base, work)
    st = WikiStore(work, actor="test")
    fold = st.load().find(txn_child.GROUP)
    assert fold is not None
    section = txn.Txn(work, id=None)
    with txn.active(section):
        st.declare_filing(fold, raw_title="New", note_title="New")
    kinds = [r["t"] for r in txn.read_records(work / txn.UNDO_FILE)]
    assert kinds == ["decl"]
    rec = txn.read_records(work / txn.UNDO_FILE)[0]
    assert len(rec["new"]) == 2 and set(rec["len"]) == {"log.md", f"{txn_child.GROUP}/index.md"}
    txn.recover(work)


def test_a_declared_path_that_is_rewritten_is_undone_from_its_image(base: Path, form: str) -> None:
    """Declared for appending, then rewritten whole (an index that changed): the length alone would not put it back."""
    before = digest(base)
    store = WikiStore(base, actor="test")
    section = txn.Txn(base, id=None)
    index = base / txn_child.GROUP / "index.md"
    with txn.active(section):
        section.declare(extend=[index], new=[base / "raw" / "declared.md"])
        write_atomic(index, "# Rewritten\n")
        write_atomic(base / "raw" / "declared.md", "x\n")
    assert txn.recover(base).rolled_back
    assert digest(base) == before and store.load() is not None


def test_a_declared_path_that_is_deleted_is_put_back_from_its_image(base: Path, form: str) -> None:
    before = digest(base)
    section = txn.Txn(base, id=None)
    log = base / "log.md"
    with txn.active(section):
        section.declare(extend=[log])
        txn.unlink(log)
    assert not log.exists() and txn.recover(base).rolled_back and digest(base) == before


def test_a_declared_new_file_is_removed_and_a_declared_length_is_cut(base: Path, form: str) -> None:
    before = digest(base)
    section = txn.Txn(base, id=None)
    log = base / "log.md"
    with txn.active(section):
        section.declare(extend=[log], new=[base / "raw" / "declared.md"])
        write_atomic(base / "raw" / "declared.md", "x\n")
        txn.append(log, "* **Created**: x\n")
        txn.append(log, "* **Created**: y\n")
    assert len(txn.read_records(base / txn.UNDO_FILE)) == 1  # one record for the lot
    assert txn.recover(base).rolled_back and digest(base) == before


def test_a_log_line_somebody_else_added_after_the_crash_is_not_cut(base: Path, form: str) -> None:
    section = txn.Txn(base, id=None)
    log = base / "log.md"
    with txn.active(section):
        txn.append(log, "* **Created**: mine\n")
    with open(log, "ab") as f:
        f.write(b"* **Created**: written by someone else\n")
    done = txn.recover(base)
    assert len(done.conflicts) == 1 and log.read_bytes().endswith(b"someone else\n")


# -- the preallocated segment form -------------------------------------------------------------------------------------------


def test_the_segment_log_is_a_preallocated_file_that_is_never_truncated(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    store = WikiStore(base, actor="test")
    undo = base / txn.UNDO_FILE
    with store.lock():
        txn_child._new(store, 1)
    size = undo.stat().st_size
    assert size >= txn.UNDO_SIZE and undo.read_bytes()[:8] == journal.MAGIC
    assert undo.read_bytes().count(b"\0") > size - 4096  # zeros that were written
    salt = journal.parse_header(undo.read_bytes()[:64]).salt  # type: ignore[union-attr]
    with store.lock():
        txn_child._new(store, 1)
    header = journal.parse_header(undo.read_bytes()[:64])
    assert undo.stat().st_size == size and header is not None and header.salt != salt  # a new generation, the same file
    assert not txn.pending(base)


def test_a_record_of_an_older_section_is_never_taken_for_a_new_one(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    undo = base / txn.UNDO_FILE
    first = txn.Txn(base, id=None)
    with txn.active(first):
        for n in (1, 2, 3):
            write_atomic(base / "a" / f"note-{n}.md", f"first section {n}\n")
    assert len(txn.read_records(undo)) == 3
    first.rollback()  # nothing to do with it any more: its records are still in the file
    second = txn.Txn(base, id=None)
    with txn.active(second):
        write_atomic(base / "a" / "note-4.md", "second section\n")
    records = txn.read_records(undo)
    assert [r["p"] for r in records] == ["a/note-4.md"]  # not the three of the first, which lie after it in the file
    assert txn.recover(base).paths == 1


def test_a_torn_record_and_a_corrupt_one_end_the_chain(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    undo = base / txn.UNDO_FILE
    section = txn.Txn(base, id=None)
    with txn.active(section):
        for n in (1, 2, 3):
            write_atomic(base / "a" / f"note-{n}.md", f"section {n}\n")
    data = bytearray(undo.read_bytes())
    records = txn.read_records(undo)
    assert len(records) == 3
    # find the offset of the third record and tear it
    off = journal.HEADER_SIZE
    header = journal.parse_header(bytes(data[:64]))
    assert header is not None
    for _ in range(2):
        parsed = journal._parse_at(bytes(data), off, header.salt)
        assert parsed is not None
        off += journal._align(journal.HEAD_SIZE + parsed[0][0])
    data[off + journal.HEAD_SIZE + 9] ^= 0xFF
    undo.write_bytes(bytes(data))
    assert len(txn.read_records(undo)) == 2
    assert txn.pending(base) and txn.recover(base).paths == 2


def test_the_segment_log_grows_once_when_a_section_needs_more(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    monkeypatch.setattr(txn, "UNDO_SIZE", 4096)
    before = digest(base)
    section = txn.Txn(base, id=None)
    with txn.active(section):
        for n in range(40):
            write_atomic(base / "a" / f"extra-{n}.md", "x\n")
    assert (base / txn.UNDO_FILE).stat().st_size > 4096 and len(txn.read_records(base / txn.UNDO_FILE)) == 40
    assert txn.recover(base).rolled_back and digest(base) == before


def test_a_pending_answer_costs_a_stat_for_a_log_that_is_done(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    store = WikiStore(base, actor="test")
    with store.lock():
        txn_child._new(store, 0)
    assert not txn.pending(base)
    reads = []
    real = txn._read_segment
    monkeypatch.setattr(txn, "_read_segment", lambda data: reads.append(1) or real(data))
    for _ in range(5):
        assert not txn.pending(base)
    assert reads == []  # known to be done: no read of the file


# -- a rebuild ---------------------------------------------------------------------------------------------------------------------


def test_a_rebuild_keeps_no_undo_log_for_each_document_and_a_mark_until_it_is_over(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")
    sections: list[bool] = []
    real_init = txn.Txn.__init__

    def counting(self: txn.Txn, *a: object, **k: object) -> None:
        sections.append(True)
        real_init(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(txn.Txn, "__init__", counting)
    llm.add("librarian/summarize", draft("Q3 revenue"))
    uncertain_alpha(classifier)
    seen: list[bool] = []
    real_ingest = wiki.ingest_many

    def watching(*a: object, **k: object) -> object:
        seen.append(txn.rebuild_interrupted(wiki.store.root))  # the mark is there while the documents are filed
        return real_ingest(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(wiki, "ingest_many", watching)
    before = len(sections)
    report = wiki.rebuild([Item("revenue.md", data=b"Revenue grew.")])
    assert report.stopped is None and len(report.results) == 1
    filed_in_sections = len(sections) - before
    assert seen == [True] and not txn.rebuild_interrupted(wiki.store.root)
    assert filed_in_sections == 0  # no section had an undo log: the documents were filed with `lock()` suspended
    assert errors(check(wiki.store.root)) == []


def test_an_interrupted_rebuild_leaves_its_mark_and_check_says_so(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")

    def die(*a: object, **k: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(wiki, "ingest_many", die)
    with pytest.raises(KeyboardInterrupt):
        wiki.rebuild([Item("revenue.md", data=b"Revenue grew.")])
    assert txn.rebuild_interrupted(wiki.store.root)
    problems = [str(p) for p in errors(check(wiki.store.root))]
    assert any("rebuild did not finish" in p for p in problems)
    txn.clear_rebuild(wiki.store.root)
    assert not [p for p in errors(check(wiki.store.root)) if "rebuild" in str(p)]


def _rebuild_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier):  # type: ignore[no-untyped-def]
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")
    llm.add("librarian/summarize", draft("Q3 revenue"))
    uncertain_alpha(classifier)
    return wiki


def test_a_rebuild_makes_its_mark_before_it_deletes_anything(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between the clearing and the mark would leave an emptied wiki with no sign; so the mark comes first."""
    wiki = _rebuild_wiki(bundle, llm, classifier)
    seen: list[bool] = []
    real_clear = wiki.store.clear

    def clearing(*a: object, **k: object) -> object:
        seen.append(txn.rebuild_interrupted(wiki.store.root))
        raise KeyboardInterrupt  # the crash: nothing is deleted, the mark stays

    monkeypatch.setattr(wiki.store, "clear", clearing)
    with pytest.raises(KeyboardInterrupt):
        wiki.rebuild([Item("revenue.md", data=b"Revenue grew.")])
    assert seen == [True] and txn.rebuild_interrupted(wiki.store.root)
    assert any("rebuild did not finish" in str(p) for p in errors(check(wiki.store.root)))
    assert real_clear is not None


def test_a_rebuild_flushes_what_it_wrote_before_it_takes_its_mark_away(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _rebuild_wiki(bundle, llm, classifier)
    order: list[str] = []
    real_sync, real_clear = txn.sync_tree, txn.clear_rebuild
    monkeypatch.setattr(txn, "sync_tree", lambda root: order.append("sync") or real_sync(root))
    monkeypatch.setattr(txn, "clear_rebuild", lambda root: order.append("clear") or real_clear(root))
    report = wiki.rebuild([Item("revenue.md", data=b"Revenue grew.")])
    assert report.stopped is None and order == ["sync", "clear"] and not txn.rebuild_interrupted(wiki.store.root)


def test_a_crash_while_a_rebuild_is_flushing_keeps_its_mark(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _rebuild_wiki(bundle, llm, classifier)

    def dying(root: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(txn, "sync_tree", dying)
    with pytest.raises(KeyboardInterrupt):
        wiki.rebuild([Item("revenue.md", data=b"Revenue grew.")])
    assert txn.rebuild_interrupted(wiki.store.root)


def test_sync_tree_flushes_every_file_where_there_is_no_os_sync(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flushed: list[str] = []
    monkeypatch.delattr(os, "sync", raising=False)
    monkeypatch.setattr(txn, "_fsync_path", lambda path, directory=False, **kw: flushed.append(path.name))
    txn.sync_tree(base)
    assert "log.md" in flushed and "index.md" in flushed


# -- the lock ------------------------------------------------------------------------------------------------------------------


def _hold_store(root: str, ready: object, release: object) -> None:
    store = WikiStore(Path(root), actor="test")
    with store.lock():
        ready.set()  # type: ignore[attr-defined]
        release.wait(30)  # type: ignore[attr-defined]


def test_the_bundle_lock_is_a_file_lock_that_other_processes_wait_for(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    store = WikiStore(root, actor="test")
    store.init()
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_store, args=(str(root), ready, release))
    proc.start()
    try:
        assert ready.wait(60)
        got = []
        with store._exclusive(blocking=False) as free:  # held by the other process: not free
            got.append(free)
        assert got == [False]
        t0 = time.monotonic()
        release.set()
        with store.lock():  # waits in the kernel for the holder to let go, then takes it
            waited = time.monotonic() - t0
        assert waited < 30
    finally:
        release.set()
        proc.join(30)


def test_a_folder_can_be_locked_as_it_is(tmp_path: Path) -> None:
    a, b = FileLock(tmp_path, directory=True), FileLock(tmp_path, directory=True)
    if os.name == "nt":
        pytest.skip("a folder is locked by its name on POSIX only")
    assert a.try_acquire() and not b.try_acquire()
    a.release()
    assert b.try_acquire()
    b.release()
    assert not list(tmp_path.iterdir())  # no file made in the folder


# -- appended to, then rewritten ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["declared", "len"])
def test_a_path_appended_to_and_then_rewritten_is_undone_to_its_first_bytes(base: Path, form: str, how: str) -> None:
    """A section that appends to a path and then rewrites it (an index: a line, then the whole file again) must give back what the
    path held before, not the file with the appended line in it."""
    before = digest(base)
    index = base / txn_child.GROUP / "index.md"
    original = index.read_bytes()
    section = txn.Txn(base, id=None)
    with txn.active(section):
        if how == "declared":
            section.declare(extend=[index])
        txn.append(index, "* an appended line\n")
        write_atomic(index, "# Rewritten after the append\n")
    assert index.read_bytes() != original
    done = txn.recover(base)
    assert done.rolled_back and done.conflicts == [] and index.read_bytes() == original and digest(base) == before


@pytest.mark.parametrize("how", ["declared", "len"])
def test_a_path_appended_to_and_then_deleted_is_put_back_to_its_first_bytes(base: Path, form: str, how: str) -> None:
    index = base / txn_child.GROUP / "index.md"
    original = index.read_bytes()
    section = txn.Txn(base, id=None)
    with txn.active(section):
        if how == "declared":
            section.declare(extend=[index])
        txn.append(index, "* an appended line\n")
        txn.unlink(index)
    assert txn.recover(base).conflicts == [] and index.read_bytes() == original


@pytest.mark.parametrize("how", ["declared", "len"])
def test_a_crash_between_the_image_and_the_rewrite_of_an_appended_path_is_undone(base: Path, form: str, how: str) -> None:
    """The record of the image is made and the file still holds the appended text (the rewrite did not happen): not a conflict."""
    index = base / txn_child.GROUP / "index.md"
    original = index.read_bytes()
    section = txn.Txn(base, id=None)
    with txn.active(section):
        if how == "declared":
            section.declare(extend=[index])
        txn.append(index, "* an appended line\n")
        section.before_write(index, b"# Rewritten after the append\n")  # the record is durable; the process dies before the write
    assert index.read_bytes() != original
    assert txn.recover(base).conflicts == [] and index.read_bytes() == original


# -- the answer of `pending` -----------------------------------------------------------------------------------------------------


def _open_section_and_die(root: str) -> None:
    txn.PREALLOCATED = True  # a new process does not inherit the format the test runs in
    section = txn.Txn(Path(root), id=None)
    with txn.active(section):
        txn.append(Path(root) / "log.md", "* a section that never finished\n")
        os._exit(0)


def test_pending_tells_two_sections_apart_by_the_salt_when_size_and_time_are_the_same(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The log is preallocated, so its size never changes, and a coarse clock gives two sections the same time stamp: the answer
    remembered for a section that was done must not be given for the next section of another process, whose log is not."""
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    store = WikiStore(base, actor="test")
    with store.lock():
        txn_child._new(store, 0)
    assert not txn.pending(base)  # remembered as done
    undo = base / txn.UNDO_FILE
    before = os.stat(undo)
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_open_section_and_die, args=(str(base),))
    proc.start()
    proc.join(60)
    os.utime(undo, ns=(before.st_atime_ns, before.st_mtime_ns))  # a clock with one tick for both sections
    after = os.stat(undo)
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert txn.pending(base)  # the other process's section is not done


# -- corrections before `remember` writes into the journal ---------------------------------------------------------------------------


def test_suspending_the_undo_log_is_for_the_thread_that_asks_not_for_the_other_threads(base: Path) -> None:
    """A rebuild opens its sections with no undo log; a thread of the same process that files something else must still have one."""
    store = WikiStore(base, actor="test")
    seen: dict[str, bool] = {}

    def other() -> None:
        with store.lock():
            seen["other"] = txn.current() is not None

    with store.undo_suspended():
        with store.lock():
            seen["own"] = txn.current() is not None
        worker = threading.Thread(target=other)
        worker.start()
        worker.join(30)
        copied = threading.Thread(target=contextvars.copy_context().run, args=(_note_undo, store, seen))
        copied.start()  # a worker of the rebuild runs in a copy of its context
        copied.join(30)
    with store.lock():
        seen["after"] = txn.current() is not None
    assert seen == {"own": False, "other": True, "copied": False, "after": True}


def _note_undo(store: WikiStore, seen: dict[str, bool]) -> None:
    with store.lock():
        seen["copied"] = txn.current() is not None


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_sync_tree_uses_os_sync_only_on_linux_and_full_fsync_on_macos(
    base: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    calls: list[str] = []
    full: list[int] = []
    flushed: list[str] = []
    monkeypatch.setattr(txn.sys, "platform", platform)
    monkeypatch.setattr(os, "sync", lambda: calls.append("sync"), raising=False)
    fake = types.SimpleNamespace(F_FULLFSYNC=51, fcntl=lambda fd, op: full.append(op))
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: flushed.append("f") or real(fd))
    txn.sync_tree(base)
    if platform == "linux":
        assert calls == ["sync"] and not flushed and not full
    else:
        assert calls == [] and flushed  # file by file
        assert len(full) == (1 if platform == "darwin" else 0) and set(full) <= {51}  # macOS: one F_FULLFSYNC, at the end


def test_a_pending_answer_remembered_for_a_salt_is_not_given_for_another_file_with_the_same_salt(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A log put back from a backup taken while a section was open has a salt this process saw close: the file is another one."""
    monkeypatch.setattr(txn, "PREALLOCATED", True)
    undo = base / txn.UNDO_FILE
    backup = base.parent / "undo-backup"
    section = txn.Txn(base, id=None)
    with txn.active(section):
        write_atomic(base / "a" / "extra.md", "x\n")
        shutil.copyfile(undo, backup)  # the section is open: records and no commit
    section.commit()
    section.finish()
    assert not txn.pending(base)  # remembered as done, by the salt (and the file)
    restored = base.parent / "undo-restored"
    shutil.copyfile(backup, restored)
    os.replace(restored, undo)  # the backup comes back: the same salt in another file
    assert txn.pending(base)
