# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The compaction reuses the oldest segment only, after what is not done in it was copied forward with its state (a closure
never loses its entry's closing); a flush hint that outlived the processes is checked when a process attaches; the entries given up
count in the queue limit."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import time
from pathlib import Path

import pytest

from llmw2 import FailedBacklog, FilerError, QueueFull
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.errors import ReplyInvalid
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_journal import new, seg_path
from tests.wiki.test_remember import make

AT = "2026-01-01T00:00:00.000000+00:00"


class Cut(Exception):
    pass


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def entry(j: J.Journal, ident: str, text: str) -> J.Ref:
    return j.append(J.T_ENTRY, {"id": ident, "key": "k" + ident, "hash": "h" + ident, "at": AT}, text.encode())


def rotate(j: J.Journal) -> None:
    with j._flock("append.lock"):
        j._catch_up()
        j._rotate()


def three_segments(root: Path) -> tuple[F.Hub, J.Journal]:
    """seg 1: the E of x1 (given up later) and of e2; seg 2: the D of e2 and the X of x1; seg 3: e3, done."""
    hub = F.Hub(root)
    hub.journal.close()
    hub.journal = J.Journal(root, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    entry(j, "x1", "text one")
    entry(j, "e2", "text two")
    rotate(j)
    j.append(J.T_DONE, {"id": "e2"})
    j.append(J.T_ABANDON, {"id": "x1", "error": "given up"})
    rotate(j)
    entry(j, "e3", "three")
    j.append(J.T_DONE, {"id": "e3"})
    return hub, j


def states(root: Path) -> tuple[list[str], list[str]]:
    hub = F.Hub(root)
    hub.refresh()
    out = [e.id for e in hub.open_entries()], [e.id for e in hub.entries.values() if e.failed is not None]
    hub.journal.close()
    return out


# -- the compaction: the oldest segment only, what is not done is copied forward -------------------------------------------------


def test_the_oldest_segment_is_reused_and_a_closure_never_loses_its_entry(tmp_path: Path) -> None:
    hub, j = three_segments(tmp_path)
    hub.refresh()
    assert j.reusable()[0] == 1
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    assert states(tmp_path) == ([], ["x1"])  # no entry comes back open; the one given up is still there
    hub2 = F.Hub(tmp_path)
    hub2.refresh()
    assert hub2.entries["x1"].text == "text one" and "e2" not in hub2.open_entries()
    ents = [r for r in J.Journal(tmp_path, writable=False).read().records if r.type == J.T_ENTRY and r.meta["id"] == "x1"]
    assert len(ents) == 1  # the copy; the first went with its segment
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    again = [r for r in J.Journal(tmp_path, writable=False).read().records if r.type == J.T_ENTRY and r.meta["id"] == "x1"]
    assert len(again) == 1  # not copied again: it is ahead
    hub.journal.close()
    hub2.journal.close()


def test_an_open_entry_and_its_state_are_carried_and_the_filing_goes_on(tmp_path: Path) -> None:
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    entry(j, "o1", "open one")
    entry(j, "f1", "filed one")
    j.append(J.T_FILED, {"id": "f1", "note": "a.md", "action": "created"})
    j.append(J.T_ATTEMPT, {"id": "o1", "error": "ModelError: down"})
    rotate(j)
    entry(j, "e2", "two")
    j.append(J.T_DONE, {"id": "e2"})
    rotate(j)
    entry(j, "e3", "three")
    hub.refresh()
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    fresh = F.Hub(tmp_path)
    fresh.refresh()
    assert sorted(e.id for e in fresh.open_entries()) == ["e3", "f1", "o1"]
    assert fresh.entries["f1"].filed == {"note": "a.md", "action": "created"} and fresh.entries["o1"].attempts == 1
    assert fresh.entries["o1"].text == "open one"
    fresh.journal.close()
    hub.journal.close()


def test_a_crash_between_the_copy_and_the_reuse_leaves_one_entry_not_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    hub = F.Hub(root)
    hub.journal.close()
    hub.journal = J.Journal(root, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    entry(j, "o1", "open one")
    entry(j, "x1", "text one")
    rotate(j)
    j.append(J.T_ABANDON, {"id": "x1", "error": "given up"})
    rotate(j)
    entry(j, "e3", "three")
    hub.refresh()

    def die(self: J.Journal, number: int) -> bool:
        raise Cut(number)

    real = J.Journal.recycle
    monkeypatch.setattr(J.Journal, "recycle", die)
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]  # the copies are durable, the segment is not reused: the crash
    monkeypatch.setattr(J.Journal, "recycle", real)
    twice = [r.meta["id"] for r in J.Journal(root, writable=False).read().records if r.type == J.T_ENTRY]
    assert twice.count("o1") == 2 and twice.count("x1") == 2
    assert states(root) == (["o1", "e3"], ["x1"])  # one entry each, not two
    hub2 = F.Hub(root)
    hub2.refresh()
    F.Filer(hub2, None)._compact()  # type: ignore[arg-type]
    after = [r.meta["id"] for r in J.Journal(root, writable=False).read().records if r.type == J.T_ENTRY]
    assert sorted(after) == ["e3", "o1", "x1"]  # nothing copied again; the old segment is reused
    hub2.journal.close()
    hub.journal.close()


# -- the flush hint of a file put back from a backup ---------------------------------------------------------------------------


def test_a_flush_hint_that_outlived_the_processes_is_checked_at_attach_and_the_next_ack_flushes(tmp_path: Path) -> None:
    keep = new(tmp_path, shared_hint=True)  # holds the shared hint (a named mapping goes with its last holder)
    j = new(tmp_path, shared_hint=True)
    j.append_entry("first")
    path = seg_path(tmp_path, 1)
    backup = path.read_bytes()
    for i in range(5):
        j.append_entry(f"later {i}")
    j.close()
    path.write_bytes(backup)  # the backup is put back while no process runs; the hint says the later records were flushed
    p2 = new(tmp_path, shared_hint=True)  # a new process
    before = p2.syncs
    p2.append_entry("after the restore")
    assert p2.syncs == before + 1  # it flushed: the hint did not cover what the file holds
    again = p2.syncs
    p2.append_entry("and another")
    assert p2.syncs >= again  # (a flush each, or shared: never fewer than it needs)
    assert [e.text.decode() for e in p2.read().entries()] == ["first", "after the restore", "and another"]
    keep.close()
    p2.close()


# -- the entries given up count in the queue limit -----------------------------------------------------------------------------


def test_entries_given_up_fill_the_queue_and_remember_refuses_with_a_distinct_error(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    llm = ContentLLM(UsageTracker(), fail_on={"poison": ReplyInvalid("no usable JSON")})
    w, _ = make(bundle, llm)
    try:
        for i in range(2):
            r = w.remember(f"topic: robots-{i}\npoison {i}")
            with pytest.raises(FilerError):
                r.wait(30)
        end = time.monotonic() + 30
        while w.status()["failed"] < 2 and time.monotonic() < end:
            time.sleep(0.05)
        assert w.status()["failed"] == 2
        assert any("FailedBacklog" in x or "given up" in x for x in w.status()["warnings"])
        before = len([r for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_ENTRY])
        with pytest.raises(FailedBacklog) as e:
            w.remember("topic: robots-9\na fine text", timeout=5)
        assert isinstance(e.value, QueueFull)
        assert len([r for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_ENTRY]) == before  # nothing written
    finally:
        w.close()


def test_a_compaction_whose_carried_entries_would_fill_the_segments_again_stops(tmp_path: Path) -> None:
    """With segments smaller than what is open, carrying forward would fill them, make older ones reusable and carry again for ever."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, shared_hint=False, segment_size=8 << 10)
    j = hub.journal
    j.prepare()
    for i in range(12):
        entry(j, f"o{i}", "x" * 1500)  # about 18 KB open, in segments of 8 KiB
    hub.refresh()
    f = F.Filer(hub, None)  # type: ignore[arg-type]
    f._compact()
    top = max(n for n, _p, _h in j.segments())
    for _ in range(5):
        f._compact()
    assert max(n for n, _p, _h in j.segments()) <= top + 2  # it did not run away
    fresh = F.Hub(tmp_path)
    fresh.refresh()
    assert sorted(e.id for e in fresh.open_entries()) == sorted(f"o{i}" for i in range(12))  # nothing lost
    fresh.journal.close()
    hub.journal.close()


# -- a copy cut half way, a reader overtaken, a compaction held back ------------------------------------------------------------------


def test_a_copy_cut_after_its_first_record_keeps_the_state_of_the_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The copy of an entry is one record with its state: cut after the first record written by `_carry`, nothing of a state is missing."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    entry(j, "x1", "given up text")
    for _ in range(3):
        j.append(J.T_ATTEMPT, {"id": "x1", "error": "e"})
    j.append(J.T_ABANDON, {"id": "x1", "error": "given up"})
    entry(j, "f1", "filed text")
    j.append(J.T_FILED, {"id": "f1", "note": "a.md", "action": "created"})
    entry(j, "d1", "done")
    j.append(J.T_DONE, {"id": "d1"})
    rotate(j)
    entry(j, "e2", "two")
    j.append(J.T_DONE, {"id": "e2"})
    hub.refresh()
    real, calls = J.Journal.append, {"n": 0}

    def cut(self: J.Journal, *a: object, **k: object) -> J.Ref:
        calls["n"] += 1
        if calls["n"] == 2:
            raise Cut("killed after the first record of the copy")
        return real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(J.Journal, "append", cut)
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    monkeypatch.setattr(J.Journal, "append", real)
    hub.journal.close()
    hub2 = F.Hub(tmp_path)  # another process: the segment is still there, the copy of x1 is ahead of it, f1 is not copied
    hub2.refresh()
    F.Filer(hub2, None)._compact()  # type: ignore[arg-type]
    hub2.journal.close()
    assert states(tmp_path) == (["f1"], ["x1"])  # f1 is filed and not done: still open, with its F
    check = F.Hub(tmp_path)
    check.refresh()
    assert check.entries["f1"].filed == {"note": "a.md", "action": "created"} and check.entries["x1"].attempts == 3
    check.journal.close()


def test_a_reader_that_the_zeroing_of_a_segment_overtakes_starts_again_and_sees_the_closures(tmp_path: Path) -> None:
    w = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    w.prepare()
    entry(w, "d1", "done later")
    reader = F.Hub(tmp_path)
    reader.journal.close()
    reader.journal = J.Journal(tmp_path, shared_hint=False, writable=False)
    reader.refresh()
    for i in range(20):
        entry(w, f"z{i}", "y" * 2000)
    w.append(J.T_DONE, {"id": "d1"})
    for i in range(20):
        w.append(J.T_DONE, {"id": f"z{i}"})
    rotate(w)
    entry(w, "e2", "two")
    w.append(J.T_DONE, {"id": "e2"})
    real, fired = J.Journal._record_at, []

    def record_at(fd: int, off: int, size: int, salt: int) -> object:
        if not fired and off > 20000:  # the reader is in the middle of segment 1: the filer reuses it now
            fired.append(off)
            assert w.recycle(1)
        return real(fd, off, size, salt)

    J.Journal._record_at = staticmethod(record_at)  # type: ignore[method-assign,assignment]
    try:
        reader.refresh()
    finally:
        J.Journal._record_at = staticmethod(real)  # type: ignore[method-assign,assignment]
    reader.refresh()
    assert fired and [e.id for e in reader.open_entries()] == [] and states(tmp_path)[0] == []
    w.close()
    reader.journal.close()


def test_a_compaction_that_the_guard_holds_back_is_said_in_status_and_check_and_the_guard_counts_bytes(tmp_path: Path) -> None:
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    entry(j, "big", "è" * 300_000)  # 300 K characters, 600 KB: over half of a segment of 1 MiB in bytes, under it in characters
    j.append(J.T_ABANDON, {"id": "big", "error": "context too long"})
    rotate(j)
    for k in range(6):
        for i in range(20):
            entry(j, f"d{k}-{i}", "z" * 3000)
            j.append(J.T_DONE, {"id": f"d{k}-{i}"})
        rotate(j)
    hub.refresh()
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    assert hub.compact_stopped is not None and hub.compact_stopped[0] == 1
    assert any("compaction is stopped" in w for w in F.status(hub, tmp_path)["warnings"])
    assert any("none is" in p.text and p.severity == "warning" for p in J.Journal(tmp_path, writable=False).check())
    hub.journal.close()


# -- admission in bytes ----------------------------------------------------------------------------------------------------------------


def small_journal(w: object, size: int) -> None:
    w._journal().journal.segment_size = size  # type: ignore[attr-defined]


def test_a_text_over_half_a_segment_is_refused_before_anything_is_written_and_the_file_path_does_not_use_the_journal(bundle: Path) -> None:
    from llmw2 import InputError

    w, _ = make(bundle)
    try:
        small_journal(w, 16 << 10)  # half a segment: 8 KiB
        with pytest.raises(InputError, match="ingest_file"):
            w.remember("topic: robots-1\n" + "x" * 9000)
        assert not list((bundle / ".llmw2" / "journal").glob("*.wal")) or J.Journal(bundle, writable=False).read().entries() == []
        done = w.ingest_file(b"topic: robots-2\n" + b"y" * 9000, "big.txt")  # the path of a file is the synchronous one: no journal
        assert done.action == "created" and J.Journal(bundle, writable=False).read().entries() == []
    finally:
        w.close()


def test_the_open_and_given_up_entries_wait_and_refuse_by_bytes(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), fail_on={"poison": ReplyInvalid("no usable JSON")})
    w, _ = make(bundle, llm)
    try:
        small_journal(w, 18 << 10)  # 9 KiB of backlog: an entry of 2 KB weighs about 3.4 KB with its record
        for i in range(2):
            r = w.remember(f"topic: robots-{i}\npoison {i}" + "p" * 2000)
            with pytest.raises(FilerError):
                r.wait(30)
        end = time.monotonic() + 30
        while w.status()["failed"] < 2 and time.monotonic() < end:
            time.sleep(0.05)
        with pytest.raises(FailedBacklog):  # 2 entries given up weigh 6 KB: no room will come
            w.remember("topic: robots-9\n" + "z" * 2000, timeout=3)
    finally:
        w.close()


# -- a reader against the reuse of a segment, cut at every point --------------------------------------------------------------------


@pytest.mark.parametrize("point", ["before-header", "after-new-salt", "mid-zero", "after-zero", "after-free"])
def test_a_reader_in_the_middle_of_a_segment_agrees_with_a_new_one_wherever_the_reuse_is_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    w = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    w.prepare()
    entry(w, "d1", "done later")
    reader = F.Hub(tmp_path)
    reader.journal.close()
    reader.journal = J.Journal(tmp_path, shared_hint=False, writable=False)
    reader.refresh()  # the reader has read d1 and is at the end of what there is
    for i in range(20):
        entry(w, f"z{i}", "y" * 2000)
    w.append(J.T_DONE, {"id": "d1"})
    for i in range(20):
        w.append(J.T_DONE, {"id": f"z{i}"})
    rotate(w)
    entry(w, "e2", "two")
    w.append(J.T_DONE, {"id": "e2"})
    seen: list[str] = []

    def look(where: str) -> None:
        if where == point:
            seen.append(where)
            reader.refresh()  # the reader looks as the reuse stands at this point
            reader.refresh()

    real_header, real_zero, real_pwrite = J._set_header, J._zero_used, J._pwrite
    state = {"headers": 0}

    def set_header(path: Path, header: J.Header) -> None:
        state["headers"] += 1
        if state["headers"] == 1:
            look("before-header")
        real_header(path, header)
        look("after-new-salt" if state["headers"] == 1 else "after-free")

    def zero_used(path: Path) -> None:
        monkeypatch.setattr(J, "ZERO_CHUNK", 4096)
        count = {"n": 0}

        def pwrite(fd: int, data: bytes, off: int) -> None:
            real_pwrite(fd, data, off)
            if not any(data):
                count["n"] += 1
                if count["n"] == 3:
                    look("mid-zero")

        monkeypatch.setattr(J, "_pwrite", pwrite)
        real_zero(path)
        monkeypatch.setattr(J, "_pwrite", real_pwrite)
        look("after-zero")

    monkeypatch.setattr(J, "_set_header", set_header)
    monkeypatch.setattr(J, "_zero_used", zero_used)
    assert w.recycle(1)
    monkeypatch.undo()
    assert seen == [point]
    reader.refresh()
    assert [e.id for e in reader.open_entries()] == [] == states(tmp_path)[0]
    w.close()
    reader.journal.close()


# -- the cost is the whole record, and the counters of the hub ----------------------------------------------------------------


def test_the_guard_counts_the_fields_and_the_title_of_an_entry_given_up_not_only_its_text(tmp_path: Path) -> None:
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, shared_hint=False, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    j.append_entry("short text", id="big", key="kbig", hash="hbig", at=AT, fields={"k": "f" * 700_000})  # 700 KB in the metadata
    j.append(J.T_ABANDON, {"id": "big", "error": "context too long"})
    rotate(j)
    for k in range(6):
        for i in range(20):
            entry(j, f"d{k}-{i}", "z" * 3000)
            j.append(J.T_DONE, {"id": f"d{k}-{i}"})
        rotate(j)
    hub.refresh()
    assert hub.failed_bytes > 700_000  # the record, not the 10 bytes of text
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]
    assert hub.compact_stopped is not None and hub.compact_stopped[0] == 1  # held back and said, not copied at every turn
    hub.journal.close()


def test_the_counters_of_the_hub_are_those_of_a_walk_of_its_entries_and_remember_does_not_walk(tmp_path: Path) -> None:
    hub, j = three_segments(tmp_path)
    entry(j, "o1", "open one")
    entry(j, "o2", "open two" * 50)
    j.append(J.T_FILED, {"id": "o2", "note": "a.md"})
    j.append(J.T_DONE, {"id": "o2"})
    hub.refresh()
    F.Filer(hub, None)._compact()  # type: ignore[arg-type]  # carries x1 and o1 forward: the copies replace the originals
    hub.refresh()
    walk_open = sum(e.cost for e in hub.entries.values() if e.failed is None and not e.closed)
    walk_failed = sum(e.cost for e in hub.entries.values() if e.failed is not None)
    assert hub.open_n == len(hub.open_entries()) and hub.open_n > 0
    assert hub.backlog_bytes() == (walk_open, walk_failed) and hub.failed_count() == 1 and walk_open > 0 and walk_failed > 0
    fresh = F.Hub(tmp_path)
    fresh.refresh()
    assert fresh.backlog_bytes() == hub.backlog_bytes() and fresh.failed_count() == 1 and fresh.open_n == hub.open_n
    fresh.journal.close()
    hub.journal.close()


def test_an_entry_with_huge_fields_is_refused_by_its_whole_weight(bundle: Path) -> None:
    from llmw2 import InputError

    w, _ = make(bundle)
    try:
        w._journal().journal.segment_size = 32 << 10  # 16 KiB of backlog
        with pytest.raises(InputError):
            w.remember("topic: robots-1\nshort", fields={"k": "f" * 40_000})
        assert J.Journal(bundle, writable=False).read().entries() == []
    finally:
        w.close()
