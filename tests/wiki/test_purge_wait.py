# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""How long "forget" makes its caller wait. A purge waits `FORGET_WAIT` seconds in all, whatever it waits for (the wiki's lock in each
section, the index, the vectors, the filer's `O`), and says in `pending` or `stopped` what it left to the filer; the work does not count.
A process that has not read the wiki yet reads it before it takes the lock, for a purge and for `delete_note`: the lock is held for what
changes, not for the reading of every note."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import contextlib
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from llmw2 import filer as F
from llmw2 import searchindex as si
from llmw2 import wiki as W
from llmw2.bundle import store as S
from llmw2.bundle.filelock import FileLock
from tests.wiki.test_purge_journal import KEY, SECRET, TEXT, TITLE, child, leftovers, pending, tombstones, wait_for
from tests.wiki.test_remember import make, notes, raws

HOLDER = """
import sys
from pathlib import Path
from llmw2.bundle.store import WikiStore
with WikiStore(Path(sys.argv[1]), actor="test").lock(undo=False):
    print("held", flush=True)
    sys.stdin.read()
"""


TAKER = """
import sys, threading, time
from pathlib import Path
from llmw2.bundle.store import WikiStore
store, stop, first = WikiStore(Path(sys.argv[1]), actor="test"), threading.Event(), True
threading.Thread(target=lambda: (sys.stdin.read(), stop.set()), daemon=True).start()
while not stop.is_set():
    with store.lock(undo=False):  # in the kernel's queue each time: two of these pass the lock to each other, it is never free
        if first:
            print("held", flush=True)
            first = False
        time.sleep(0.2)
"""

@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def in_thread(call: Any) -> tuple[threading.Thread, list[Any]]:
    """`call()` on a thread: the base waited for ever where these tests expect a return, so the test asserts on a join with a limit."""
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append(call()), daemon=True)
    t.start()
    return t, out


@contextlib.contextmanager
def held_in_this_process(bundle: Path) -> Iterator[threading.Event]:
    """The wiki's lock, held by another thread of this process until the event is set."""
    free, taken = threading.Event(), threading.Event()

    def hold() -> None:
        with S.WikiStore(bundle, actor="test").lock(undo=False):
            taken.set()
            free.wait(60)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert taken.wait(10)
    try:
        yield free
    finally:
        free.set()
        t.join(30)


def aged(bundle: Path) -> None:
    """Every file and folder of the wiki written long ago, as in a wiki that a new process opens (nothing is too recent to trust)."""
    t = time.time() - 60
    for p in [bundle, *bundle.rglob("*")]:
        if ".llmw2" not in p.relative_to(bundle).parts:
            os.utime(p, (t, t))


def parses_under_the_lock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count the notes read and parsed while the wiki's lock is held."""
    seen = {"held": 0, "parsed": 0}
    real_lock, real_read = S.WikiStore._exclusive, S.OKFDocument.read

    @contextlib.contextmanager
    def exclusive(self: Any, *, blocking: bool) -> Iterator[bool]:
        with real_lock(self, blocking=blocking) as got:
            seen["held"] += bool(got)
            try:
                yield got
            finally:
                seen["held"] -= bool(got)

    def read(path: Path) -> Any:
        if seen["held"] and str(path).endswith(".md") and "raw" not in Path(path).parts:
            seen["parsed"] += 1
        return real_read(path)

    monkeypatch.setattr(S.WikiStore, "_exclusive", exclusive)
    monkeypatch.setattr(S.OKFDocument, "read", staticmethod(read))
    return seen


# -- the reading of the wiki, before the lock --------------------------------------------------------------------------------


def test_a_new_process_deletes_a_note_without_reading_the_wiki_while_it_holds_the_lock(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = make(bundle)
    for i in range(8):
        w.ingest(f"topic: plain-{i}\nRivers and hills, text {i}.", title=f"Plain {i}", resource=f"https://x/{i}")
    w.close()
    aged(bundle)
    [*_, victim] = notes(bundle)
    seen = parses_under_the_lock(monkeypatch)
    fresh, _ = make(bundle)  # a new process: nothing of the wiki parsed yet (an MCP server opened for one call)
    try:
        fresh.delete_note(victim.relative_to(bundle).as_posix())
        assert not victim.exists() and len(notes(bundle)) == 7
        assert seen["parsed"] == 0  # the 8 notes were read before the lock was taken
    finally:
        fresh.close()


def test_a_new_process_purges_without_reading_the_wiki_while_it_holds_the_lock(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    for i in range(6):
        w.remember(f"topic: plain-{i}\nRivers and hills, text {i}.", key=f"k:{i}")
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w.close()
    aged(bundle)
    seen = parses_under_the_lock(monkeypatch)
    fresh, _ = make(bundle)
    try:
        report = fresh.purge(KEY)
        assert report is not None and len(report.notes) == 1 and report.pending == []
        assert seen["parsed"] == 0
        assert leftovers(bundle, SECRET, TITLE, KEY) == [] and len(notes(bundle)) == 6
    finally:
        fresh.close()


# -- one wait in all -------------------------------------------------------------------------------------------------------


def test_a_purge_while_another_process_holds_the_wiki_returns_with_the_files_pending_and_the_filer_completes_it(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w._stop_filer()
    p = child(HOLDER, bundle)  # a writer that does not let the wiki go (a long filing, a process hung in a section)
    try:
        t0 = time.monotonic()
        t, out = in_thread(lambda: w.purge(KEY))
        t.join(F.FORGET_WAIT + 10)
        took = time.monotonic() - t0
        assert out, "the purge waited for the lock beyond its wait"
        report = out[0]
        assert report is not None and report.pending == ["files", "undo", "journal", "index"] and took < F.FORGET_WAIT + 5
        assert report.entries == 1 and report.notes == [] and len(notes(bundle)) == 1  # nothing deleted without the lock
        assert len(tombstones(bundle)) == 1  # durable: whoever holds the lease next takes it up from the hash (as a purge cut there)
    finally:
        p.communicate("", timeout=30)
        t.join(60)
        w.close()
    w2, _ = make(bundle)
    try:
        w2._start_filer()
        wait_for(lambda: not pending(w2))
        assert notes(bundle) == [] and raws(bundle) == [] and leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w2.close()


def test_the_waits_of_a_purge_share_one_budget(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 1.0)
    given: list[float] = []
    real = W.NoteSearch.forget

    def forget(self: Any, store: Any, wait: float | None) -> bool:
        given.append(-1.0 if wait is None else wait)
        return real(self, store, wait)

    monkeypatch.setattr(W.NoteSearch, "forget", forget)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w.search("falcon")
    w._stop_filer()
    p = child(HOLDER, bundle)
    threading.Timer(0.6, lambda: p.communicate("", timeout=30)).start()  # the wiki is let go after 0.6 s of the purge's wait
    try:
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and notes(bundle) == []
        assert given and 0 <= given[0] <= F.FORGET_WAIT - 0.5  # what the lock took is not given to the index again
    finally:
        p.wait(30)
        w.close()


def test_a_purge_with_the_index_written_by_another_process_says_index_within_its_wait(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.remember("topic: birds-2\nThe owl hunts at night.", key="k:other")
    w.drain(60)
    w.search("falcon")
    w._stop_filer()
    writer = sqlite3.connect(bundle / si.INDEX_FILE, isolation_level=None)  # another process writing the index, as SQLite sees it
    writer.execute("BEGIN IMMEDIATE")
    try:
        t0 = time.monotonic()
        t, out = in_thread(lambda: w.purge(KEY))
        t.join(F.FORGET_WAIT + 10)
        took = time.monotonic() - t0
        assert out, "the purge waited for the index's write lock beyond its wait"
        report = out[0]
        assert report is not None and report.pending == ["index"] and len(report.notes) == 1
        assert took < F.FORGET_WAIT + 5  # the section's own look at the index waits WRITTEN_WAIT at most, as any section does
    finally:
        writer.execute("ROLLBACK")
        writer.close()
        t.join(60)
    try:
        assert w._search.forget(w.store, 2.0)  # the next look empties it
        assert leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w.close()


def test_documents_filed_again_whose_lock_does_not_come_are_stopped_and_keep_their_raw_copies(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, _ = make(bundle)
    w.remember("topic: birds-1\nThe heron waits.", key="k:1")
    w.remember("topic: birds-1\nThe heron flies at dawn.", key="k:2")
    w.drain(60)
    w._stop_filer()
    assert len(notes(bundle)) == 1 and len(raws(bundle)) == 2  # one note holds both
    real = W.Wiki.ingest_many
    hold: list[Any] = []

    def refile(self: Any, *a: Any, **k: Any) -> Any:  # the wiki is taken by another writer between the delete and the filing again
        hold.append(held_in_this_process(bundle))
        hold[0].__enter__()
        return real(self, *a, **k)

    monkeypatch.setattr(W.Wiki, "ingest_many", refile)
    try:
        t0 = time.monotonic()
        t, out = in_thread(lambda: w.purge("k:2"))
        t.join(F.FORGET_WAIT + 10)
        took = time.monotonic() - t0
        assert out, "filing the other document again waited for the lock beyond the purge's wait"
        report = out[0]
        assert report is not None and report.stopped and report.refiled == ["remember::k:1"] and report.filed == []
        assert took < F.FORGET_WAIT + 5 and notes(bundle) == [] and len(raws(bundle)) == 1  # k:1 keeps its raw copy, for `rebuild`
        assert "undo" in report.pending  # the undo log of the section that deleted is zeroed by the filer
    finally:
        if hold:
            hold[0].__exit__(None, None, None)
        t.join(60)
        w.close()
    w2, _ = make(bundle)
    try:
        w2._start_filer()
        wait_for(lambda: not pending(w2))
        assert "dawn" not in b"".join(p.read_bytes() for p in bundle.rglob("*") if p.is_file() and p.suffix != ".lock").decode(
            "utf-8", "replace"
        )
    finally:
        w2.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the kernel's queue of LockFileEx; flock has none, and a try again is all there is")
def test_a_purge_gets_the_wiki_from_writers_that_keep_waiting_for_it_in_the_kernel(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 5.0)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w._stop_filer()
    takers = [child(TAKER, bundle) for _ in range(2)]
    try:
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and notes(bundle) == []  # in the same queue, it had each of its sections
    finally:
        for p in takers:
            p.communicate("", timeout=30)
        w.close()
    assert leftovers(bundle, SECRET, TITLE, KEY) == []


def test_the_lots_of_the_index_wait_for_its_lock_within_one_deadline(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    try:
        w.ingest("topic: plain-0\nRivers and hills.", title="Plain 0", resource="https://x/0")
        w.search("rivers")  # the index is built
        w.store.on_written = None  # what comes now is not told to the index: its next look has it to write, in lots
        for i in range(1, 7):
            w.ingest(f"topic: plain-{i}\nRivers and hills, text {i}.", title=f"Plain {i}", resource=f"https://x/{i}")
        monkeypatch.setattr(si, "BATCH", 2)
        given: list[float] = []
        real = si.SearchIndex._begin

        def begin(self: Any, db: Any, wait: float | None = None) -> bool:
            given.append(-1.0 if wait is None else wait)
            time.sleep(0.15)  # another writer held the lock this long before each lot
            return real(self, db, wait)

        monkeypatch.setattr(si.SearchIndex, "_begin", begin)
        w._search.forget(w.store, 0.5)
        assert len(given) >= 3 and given == sorted(given, reverse=True) and 0 <= given[-1] < 0.5 - 0.25  # one deadline for all the lots
    finally:
        w.close()


# -- the places to read the wiki outside its lock ------------------------------------------------------------------------------


def test_the_places_to_read_come_from_the_free_memory_and_the_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "free_memory", lambda: 1000 * S.READ_BYTES_PER_NOTE * 20 + 1)
    assert S.read_places(1000) == 10  # ten reads of 1000 notes fit in half of what is free: the other half stays to the machine
    assert S.read_places(0) == 1  # notes not known: one
    monkeypatch.setattr(S, "free_memory", lambda: 100)
    assert S.read_places(1000) == 1  # never none
    monkeypatch.setattr(S, "free_memory", lambda: None)
    assert S.read_places(1000) == 1


def test_with_every_place_taken_a_new_process_reads_under_the_lock_and_the_purge_completes(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, _ = make(bundle)
    for i in range(6):
        w.remember(f"topic: plain-{i}" + chr(10) + f"Rivers and hills, text {i}.", key=f"k:{i}")
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w.close()
    aged(bundle)
    monkeypatch.setattr(S, "read_places", lambda notes: 1)
    seen = parses_under_the_lock(monkeypatch)
    taken = FileLock(bundle / S.READ_PLACE.format(0))
    assert taken.try_acquire()  # the one place, held by another reader
    fresh, _ = make(bundle)
    try:
        report = fresh.purge(KEY)
        assert report is not None and len(report.notes) == 1 and report.pending == []
        assert seen["parsed"] == 7  # no place: the 7 notes were read under the lock, as a section always did
        victim = notes(bundle)[0]
        fresh.delete_note(victim.relative_to(bundle).as_posix())
        assert not victim.exists()
    finally:
        taken.release()
        fresh.close()
    assert leftovers(bundle, SECRET, TITLE, KEY) == []


def test_a_place_is_kept_until_the_section_that_deletes_is_done(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    for i in range(3):
        w.ingest(f"topic: plain-{i}" + chr(10) + f"Rivers and hills, text {i}.", title=f"Plain {i}", resource=f"https://x/{i}")
    monkeypatch.setattr(S, "read_places", lambda notes: 1)
    held: list[bool] = []
    real = S.WikiStore.delete_note

    def delete(self: Any, rel: str) -> Any:  # under the wiki's lock: the memory of the read is still the process's, and so is the place
        probe = FileLock(bundle / S.READ_PLACE.format(0))
        free = probe.try_acquire()
        if free:
            probe.release()
        held.append(not free)
        return real(self, rel)

    monkeypatch.setattr(S.WikiStore, "delete_note", delete)
    try:
        w.delete_note(notes(bundle)[0].relative_to(bundle).as_posix())
        assert held == [True]
    finally:
        w.close()


def test_a_machine_with_room_for_a_million_reads_tries_only_as_many_places_as_there_are_readers(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = make(bundle)
    try:
        w.ingest("topic: plain-0" + chr(10) + "Rivers and hills.", title="Plain 0", resource="https://x/0")
        monkeypatch.setattr(S, "read_places", lambda notes: 10**6)
        t0 = time.monotonic()
        w.delete_note(notes(bundle)[0].relative_to(bundle).as_posix())
        assert time.monotonic() - t0 < 5 and len(list((bundle / ".llmw2").glob("read-*.lock"))) == 1
    finally:
        w.close()


# -- a note open in another process while it is deleted (Windows: a file open without FILE_SHARE_DELETE cannot be deleted) ---------

READER = """
import sys, time
from pathlib import Path
how, path, hold = sys.argv[2], Path(sys.argv[3]), float(sys.argv[4])
if how == "library":  # the library's own read of a note, held open as a slow read would
    from llmw2.bundle import document
    f = document.open_note(path)
else:  # another program: an ordinary open, no FILE_SHARE_DELETE
    f = open(path, "rb")
print("held", flush=True)
time.sleep(hold)
f.close()
sys.stdin.read()
"""


def reader(bundle: Path, how: str, note: Path, hold: float) -> Any:
    import subprocess

    from tests.wiki.test_purge_journal import ROOT

    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.Popen([sys.executable, "-c", READER, str(bundle), how, str(note), str(hold)], cwd=ROOT, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX deletes a file that another process holds open")
def test_a_note_the_library_reads_in_another_process_is_purged_and_deleted_all_the_same(bundle: Path) -> None:
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.remember("topic: birds-2" + chr(10) + "The owl hunts at night.", key="k:other")
    w.drain(60)
    w._stop_filer()
    secret_note = next(p for p in notes(bundle) if "falcon" in p.read_text(encoding="utf-8"))
    other = next(p for p in notes(bundle) if p != secret_note)
    readers = [reader(bundle, "library", secret_note, 60)]  # the note being deleted, held for longer than any try again
    try:
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and len(report.notes) == 1 and not secret_note.exists()
        readers.append(reader(bundle, "library", other, 60))
        w.delete_note(other.relative_to(bundle).as_posix())
        assert not other.exists()
    finally:
        for p in readers:
            p.kill()
            p.wait(30)
        w.close()
    assert leftovers(bundle, SECRET, TITLE, KEY) == []


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX deletes a file that another process holds open")
def test_a_note_another_program_holds_open_for_a_moment_is_purged_once_it_lets_go(bundle: Path) -> None:
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w._stop_filer()
    [secret_note] = notes(bundle)
    p = reader(bundle, "plain", secret_note, 0.15)  # an editor, an antivirus: an ordinary open, let go a moment later
    try:
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and notes(bundle) == []
    finally:
        p.communicate("", timeout=30)
        w.close()
    assert leftovers(bundle, SECRET, TITLE, KEY) == []


def test_the_library_reads_a_note_through_the_open_that_shares_deleting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmw2.bundle import document

    note = tmp_path / "n.md"
    note.write_bytes(b"---" + chr(10).encode() + b"type: note" + chr(10).encode() + b"---" + chr(10).encode() + b"body")
    opened: list[Path] = []
    real = document.open_note
    monkeypatch.setattr(document, "open_note", lambda path: (opened.append(path), real(path))[1])
    assert document.OKFDocument.read(note).body.strip() == "body" and opened == [note]


def test_a_note_deleted_while_open_the_old_way_is_taken_as_gone_by_a_read_of_the_wiki(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = make(bundle)
    try:
        for i in range(3):
            w.ingest(f"topic: plain-{i}" + chr(10) + f"Rivers and hills, text {i}.", title=f"Plain {i}", resource=f"https://x/{i}")
        gone = notes(bundle)[0]
        real = S.OKFDocument.read

        def read(path: Path) -> Any:  # FAT, SMB, Windows before 1903: still listed, and refused to open until its reader closes it
            if Path(path) == gone:
                raise PermissionError(13, "Access is denied", str(path))
            return real(path)

        monkeypatch.setattr(S.OKFDocument, "read", staticmethod(read))
        fresh, _ = make(bundle)
        try:
            listed = {n.path for n in fresh.store.load().all_notes()}
            assert gone not in listed and len(listed) == 2
        finally:
            fresh.close()
    finally:
        w.close()
