# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A full queue of `remember`: the wait is short by default and says why (`QueueFull`, NOT saved, with the queue and when to try
again), a caller waiting for room looks at the shared tail hint instead of reading the journal over and over, the limits hold when
callers of several threads or processes pass the check together, and a purge is never held by the queue."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import QueueFull, Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle.filelock import FileLock
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

ROOT = Path(__file__).resolve().parents[2]


class Gated(ContentLLM):
    """A model that waits for the test before it answers."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.gate = threading.Event()

    def chat(self, messages: list[dict[str, str]], **kw: Any) -> str:
        self.gate.wait(60)
        return super().chat(messages, **kw)


def make(bundle: Path, llm: ContentLLM | None = None) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm or ContentLLM(UsageTracker()))
    wiki.init()
    return wiki


def open_entries(bundle: Path) -> list[J.Record]:
    """The entries of the journal with a text that no record closed."""
    records = J.Journal(bundle, writable=False).read().records
    closed = {str(r.meta.get("id")) for r in records if r.type in (J.T_DONE, J.T_ABANDON)}
    return [r for r in records if r.type == J.T_ENTRY and r.text and str(r.meta.get("id")) not in closed]


def close_in_another_journal(bundle: Path, ident: str) -> None:
    """What the filer of another process does when it is done with an entry: a `D` record, through a journal object of its own (the
    hub of this process is not told: only the journal and the shared tail hint move)."""
    j = J.Journal(bundle)
    try:
        j.append(J.T_DONE, {"id": ident})
    finally:
        j.close()


# -- the wait is short by default, and the refusal says NOT saved ---------------------------------------------------------------


def test_a_full_queue_refuses_after_a_short_wait_by_default(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
        t = time.monotonic()
        with pytest.raises(QueueFull):
            w.remember("topic: a-2\ntwo")  # no timeout given
        assert time.monotonic() - t <= F.REMEMBER_WAIT + 0.1  # the wait, never past the timeout and 100 ms
        assert len(open_entries(bundle)) == 1
    finally:
        w.close()


def test_the_refusal_says_not_saved_with_the_queue_and_when_to_try_again(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 6)
    w = make(bundle)
    try:
        w._stop_filer()
        kept = [w.remember(f"topic: a-{i}\nentry {i}") for i in range(6)]
        time.sleep(0.5)
        with pytest.raises(QueueFull) as e:
            w.remember("topic: b-0\nmore", timeout=0.1)
        assert str(e.value).startswith("NOT saved")
        assert (e.value.queue, e.value.queue_limit) == (6, 6) and e.value.oldest_s is not None and e.value.oldest_s >= 0
        # no entry was seen filed: each takes at least as long as the oldest has waited, so the queue takes that many times as long
        assert e.value.retry_after_s is not None and abs(e.value.retry_after_s - 6 * e.value.oldest_s) <= 0.4  # both rounded
        assert f"try again in about {e.value.retry_after_s:g} s" in str(e.value)
        time.sleep(0.6)
        for r in kept[:5]:  # five entries filed: the process sees the filer's pace
            close_in_another_journal(bundle, r.id)
        for i in range(5):
            w.remember(f"topic: c-{i}\nagain {i}", timeout=2)
        with pytest.raises(QueueFull) as e:
            w.remember("topic: b-1\nmore", timeout=0.1)
        assert str(e.value).startswith("NOT saved") and e.value.queue == 6
        assert e.value.retry_after_s is not None and e.value.retry_after_s >= 1.0
        assert f"{e.value.retry_after_s:g} s" in str(e.value)
    finally:
        w.close()


def test_a_process_that_saw_no_entry_filed_gives_a_retry_time_from_the_last_filings(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new process (an MCP session) reads the journal once and sees no entry close in the second it waits: the time the last filings
    took (`ms` of their `F` records) gives the rate."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    w = make(bundle)
    try:
        for i in range(3):
            w.remember(f"topic: f-{i}\nfiled {i}", timeout=None)  # the filer files meanwhile: the queue of 2 may be full for a while
        w.drain(30)
        w._stop_filer()
        w.remember("topic: a-1\none")
        w.remember("topic: a-2\ntwo")
    finally:
        w.close()
    fresh = F.Hub(bundle)  # what another process reads of the journal
    try:
        fresh.refresh()
        e = fresh.queue_full("full", limit=F.QUEUE_LIMIT)
        assert str(e).startswith("NOT saved") and e.queue == 2
        assert e.retry_after_s is not None and e.retry_after_s >= 1.0
    finally:
        fresh.journal.close()


def test_the_age_of_the_oldest_entry_does_not_follow_the_wall_clock_once_read(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A robot's clock set right six hours later (it was behind when the entries were written): the oldest entry has not waited six
    hours, and `status` says neither that nor a stall."""
    from datetime import datetime as real_datetime
    from datetime import timedelta

    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
        assert w.status()["oldest_s"] < 60  # read by this process now, by the clock as it is

        class Later(real_datetime):  # the wall clock six hours ahead from now on
            @classmethod
            def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
                return real_datetime.now(tz) + timedelta(seconds=21_700)

        monkeypatch.setattr(F, "datetime", Later)
        st = w.status()
        assert st["oldest_s"] is not None and st["oldest_s"] < 60
        assert not any("has waited" in x for x in st["warnings"])
    finally:
        w.close()


def _later(seconds: float) -> type:
    from datetime import datetime as real_datetime
    from datetime import timedelta

    class Later(real_datetime):  # the wall clock `seconds` ahead from now on
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
            return real_datetime.now(tz) + timedelta(seconds=seconds)

    return Later


def test_a_process_that_reads_an_entry_after_the_wall_clock_jumped_ages_it_by_the_boot_clock(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wall clock is set six hours ahead after the entry was acknowledged, and a process that starts only then reads it: in the same
    boot of the machine its age comes from the boot clock, which the wall clock does not move, not from `at`."""
    if J.boot_clock() is None:
        pytest.skip("this system says no boot clock")
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
    finally:
        w.close()
    assert any(r.meta.get("boot") for r in open_entries(bundle))  # the entry says its boot and its time in it
    monkeypatch.setattr(F, "datetime", _later(21_700))
    fresh = F.Hub(bundle)  # another process, started after the jump
    try:
        fresh.refresh()
        [entry] = fresh.open_entries()
        assert fresh.age(entry) < 60
        assert not any("has waited" in x for x in F.status(fresh, bundle)["warnings"])
    finally:
        fresh.journal.close()


def test_an_entry_of_another_boot_is_aged_by_the_wall_clock_as_before(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
    finally:
        w.close()
    monkeypatch.setattr(J, "boot_clock", lambda: ("another-boot", 5.0))  # the machine was started again since
    monkeypatch.setattr(F, "datetime", _later(3_600))
    fresh = F.Hub(bundle)
    try:
        fresh.refresh()
        [entry] = fresh.open_entries()
        assert 3_500 < fresh.age(entry) < 3_700  # by `at` and the wall clock, the only common clock left
    finally:
        fresh.journal.close()


def test_a_copy_the_compaction_makes_keeps_the_boot_time_of_its_entry(bundle: Path) -> None:
    hub = F.Hub(bundle)
    try:
        hub.journal.append_entry("topic: a-1\none", boot=["win:7", 120.5])
        hub.refresh()
        [entry] = hub.open_entries()
        assert entry.boot == ("win:7", 120.5)
        F.carry(hub, entry)
        hub.refresh()
        copies = [r for r in hub.journal.read().entries() if r.meta["id"] == entry.id]
        assert len(copies) == 2 and all(r.meta.get("boot") == ["win:7", 120.5] for r in copies)
    finally:
        hub.journal.close()


def test_a_pace_seen_from_one_or_two_closures_is_not_believed(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that saw one entry close in a long while would tell an hour; the time the last filings took tells better."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    w = make(bundle)
    try:
        for i in range(3):
            w.remember(f"topic: f-{i}\nfiled {i}", timeout=None)  # the filer files meanwhile: the queue of 2 may be full for a while
        w.drain(30)
        w._stop_filer()
        w.remember("topic: a-1\none")
        w.remember("topic: a-2\ntwo")
        hub = w._journal()
        hub.refresh()
        hub._rate.clear()
        hub._rate.append((time.monotonic() - 100.0, hub.closed_total - 1))  # one closure seen in 100 s
        e = hub.queue_full("full", limit=F.QUEUE_LIMIT)
        assert e.retry_after_s is not None and e.retry_after_s <= 10.0, e.retry_after_s
    finally:
        w.close()


# -- a caller waiting for room ----------------------------------------------------------------------------------------------------


def test_a_caller_waiting_for_room_does_not_read_the_journal_over_and_over(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """While nothing moves in the journal, the wait reads it at the start and then only every `HINT_LOOK`: the shared tail hint says
    that nothing moved, with no system call (it read the journal every 50 ms before, scanning the segments' headers each time)."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
        reads = {"n": 0}
        journal = w._journal().journal
        real = journal.read_from

        def counted(pos: Any = None) -> Any:  # this wiki's journal only: a filer another test left running reads its own
            reads["n"] += 1
            return real(pos)

        monkeypatch.setattr(journal, "read_from", counted)
        with pytest.raises(QueueFull):
            w.remember("topic: a-2\ntwo", timeout=1.0)
        assert reads["n"] <= 3, reads
    finally:
        w.close()


def test_a_waiting_caller_is_let_in_soon_after_another_process_closes_an_entry(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    w = make(bundle)
    try:
        w._stop_filer()
        first = w.remember("topic: a-1\none")
        closed_at: list[float] = []

        def other() -> None:
            time.sleep(0.4)
            close_in_another_journal(bundle, first.id)
            closed_at.append(time.monotonic())

        th = threading.Thread(target=other)
        th.start()
        r = w.remember("topic: a-2\ntwo", timeout=5)
        let_in = time.monotonic()
        th.join()
        assert r.state == "queued" and let_in - closed_at[0] <= 0.1, let_in - closed_at[0]
    finally:
        w.close()


def test_a_caller_in_the_filers_process_gets_in_as_soon_as_the_filer_closes_an_entry(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In the process that holds the lease, the entry the filer closes lets a caller of the same process in at once."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    monkeypatch.setattr(F, "HINT_LOOK", 30.0)  # a caller woken only by the periodic look would wait 30 s: only the notice is fast
    llm = Gated(UsageTracker())
    w = make(bundle, llm)
    try:
        r1 = w.remember("topic: a-1\none")
        hub = w._journal()
        end = time.monotonic() + 10
        while hub.filing is None and time.monotonic() < end:  # the filer of this process is on it: it holds the lease
            time.sleep(0.01)
        assert hub.filing == r1.id
        threading.Timer(0.3, llm.gate.set).start()
        t = time.monotonic()
        w.remember("topic: a-2\ntwo", timeout=20)
        took = time.monotonic() - t
        assert r1.state == "filed" and took <= 5, took  # the filing (after 0.3 s) and the notice, with room for a loaded machine
    finally:
        llm.gate.set()
        w.close()


def test_an_entry_closed_between_the_read_and_the_wait_is_not_missed(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The closure lands right after the caller read the journal and found it full: the wait must not sleep through it."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    w = make(bundle)
    try:
        w._stop_filer()
        first = w.remember("topic: a-1\none")
        hub = w._journal()
        real = hub.refresh
        armed = [True]

        def refresh() -> None:
            real()
            if armed[0] and hub.open_n >= 1:
                armed[0] = False
                close_in_another_journal(bundle, first.id)

        monkeypatch.setattr(hub, "refresh", refresh)
        t = time.monotonic()
        w.remember("topic: a-2\ntwo", timeout=5)
        assert time.monotonic() - t <= 0.3 and not armed[0]
    finally:
        w.close()


def test_a_caller_never_waits_past_its_timeout_for_the_admission_another_writer_holds(bundle: Path) -> None:
    """Another writer holds the journal's admission lock (a long line of callers before this one): the wait for it ends with the wait
    for room, `timeout` and a margin, and the call says NOT saved; nothing is written."""
    w = make(bundle)
    held, release = threading.Event(), threading.Event()

    def other_writer() -> None:
        gate = FileLock(bundle / J.JOURNAL_DIR / "admit.lock")
        gate.acquire()
        held.set()
        release.wait(10)
        gate.release()

    try:
        w._stop_filer()
        w.remember("topic: a-0\nfirst")  # the journal is made
        th = threading.Thread(target=other_writer)
        th.start()
        assert held.wait(10)
        t = time.monotonic()
        with pytest.raises(QueueFull) as e:
            w.remember("topic: a-1\none", timeout=0.2)
        took = time.monotonic() - t
        release.set()
        th.join(10)
        assert took <= 0.2 + 0.1, took
        assert str(e.value).startswith("NOT saved") and "admission" in str(e.value)
        assert e.value.reason == "busy" and "not full" in str(e.value) and "is full" not in str(e.value)  # the true reason
        assert len(open_entries(bundle)) == 1
    finally:
        release.set()
        w.close()


def test_close_does_not_wait_for_an_entry_the_filer_is_still_preparing(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filer is in the model calls of an entry, before the wiki's lock (10 s with real models): `close` returns at once, nothing of
    the entry is written, not even when those calls end after it, and the next process files it once."""
    real = Wiki._prepare
    started, let_go = threading.Event(), threading.Event()

    def slow(self: Wiki, source: Any) -> Any:
        started.set()
        let_go.wait(10)
        return real(self, source)

    monkeypatch.setattr(Wiki, "_prepare", slow)
    w = make(bundle)
    try:
        w.remember("topic: a-1\nthe entry being prepared")
        assert started.wait(10)
        t = time.monotonic()
        w.close()
        took = time.monotonic() - t
    finally:
        let_go.set()
    time.sleep(0.5)  # the preparation ends after the close: it must write nothing
    assert took < 1.0, took
    assert len(open_entries(bundle)) == 1 and not list(bundle.glob("a/about-*.md"))
    monkeypatch.setattr(Wiki, "_prepare", real)
    again = make(bundle)
    try:
        again.drain(30)
        assert open_entries(bundle) == [] and len(list(bundle.glob("a/about-*.md"))) == 1
    finally:
        again.close()


def test_a_filer_left_behind_that_ends_leaves_the_filing_of_the_one_in_its_place(bundle: Path) -> None:
    """A filer thread a close did not wait for ends after another filer of the process took its place: what that one is filing
    (`status`'s `filing`) stays."""
    w = make(bundle)
    hub = w._journal()
    try:
        w._stop_filer()
        old = F.Filer(hub, w)  # left behind: not the hub's filer any more
        old.stopping = True
        hub.filing = "the-entry-of-the-filer-in-place"
        old.start()
        old.join(10)
        assert hub.filing == "the-entry-of-the-filer-in-place"
    finally:
        hub.filing = None
        w.close()


# -- the limits hold when callers pass the check together -----------------------------------------------------------------------


@pytest.mark.parametrize("before", [0, 1])
@pytest.mark.parametrize("by", ["number", "bytes"])
def test_two_callers_that_pass_the_check_together_do_not_go_over_the_limit(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, by: str, before: int
) -> None:
    """Both callers find room, then both reach the write: the second must see the first's entry and wait (here, until its timeout). With
    an entry already there the second judges by the shared tail hint (what was written since its read), with none by reading again."""
    w = make(bundle)
    limit = w._journal().journal.segment_size // 2
    if by == "number":
        monkeypatch.setattr(F, "QUEUE_LIMIT", 1 + before)
        texts = ["topic: a-1\none", "topic: a-2\ntwo"]
    else:
        size = limit * 6 // 10  # one fits in half a segment, two do not
        texts = [f"topic: a-{i}\n" + str(i) * size for i in (1, 2)]
    hub = w._journal()
    w._stop_filer()
    for i in range(before):
        w.remember(f"topic: a-0\nzero {i}")
    together = threading.Barrier(2, timeout=3)
    real_free = hub.disk_free

    def free() -> int:  # between the check of the room and the write, in both callers
        with contextlib.suppress(threading.BrokenBarrierError):
            together.wait()
        return real_free()

    monkeypatch.setattr(hub, "disk_free", free)
    out: list[str] = []

    def call(text: str) -> None:
        try:
            w.remember(text, timeout=0.5)
            out.append("ok")
        except QueueFull:
            out.append("full")

    try:
        threads = [threading.Thread(target=call, args=(t,)) for t in texts]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        assert sorted(out) == ["full", "ok"], out
        left = open_entries(bundle)
        assert len(left) == 1 + before and sum(F.Hub.entry_cost(r.size) for r in left) <= limit
    finally:
        w.close()


CHILD = r"""
import json, sys, time
from pathlib import Path
from llmw2 import QueueFull, Wiki, WikiConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

bundle, start, size, n = Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=ContentLLM(UsageTracker()))
wiki.init()
wiki._stop_filer()
while time.time() < start:
    time.sleep(0.001)
out = []
for i in range(2):
    try:
        wiki.remember(f"topic: p{n}-{i}\n" + "x" * size, timeout=0.5)
        out.append("ok")
    except QueueFull:
        out.append("full")
print(json.dumps(out), flush=True)
wiki.close()
"""


def test_processes_that_remember_together_never_go_over_the_limit_in_bytes(bundle: Path) -> None:
    """Six processes start together with entries of 30% of the limit each (half a segment) and nobody filing: at most three are kept,
    whatever the timing, and the others hear QueueFull."""
    w = make(bundle)
    limit = w._journal().journal.segment_size // 2
    w.close()
    size = limit * 3 // 10
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    start = time.time() + 4.0
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(bundle), str(start), str(size), str(k)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for k in range(6)
    ]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err[-2000:]
        results += json.loads(out.strip().splitlines()[-1])
    left = open_entries(bundle)
    assert sum(F.Hub.entry_cost(r.size) for r in left) <= limit, [r.size for r in left]
    assert len(left) == results.count("ok") == 3 and results.count("full") == 9, results


# -- a purge is never held by a full queue --------------------------------------------------------------------------------------


def test_a_purge_is_never_held_by_a_full_queue(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\nthe code is zq77secret", key="secret-1")
        w.remember("topic: a-2\ntwo")
        with pytest.raises(QueueFull):
            w.remember("topic: a-3\nthree", timeout=0.05)
        t = time.monotonic()
        report = w.purge("secret-1")
        assert time.monotonic() - t < 5.0
        assert report is not None and report.entries == 1 and report.pending == ["journal"]  # sealed by the close below
        assert not [h for h in w.search("zq77secret", 8)]
        w.remember("topic: a-3\nthree", timeout=0.5)  # its place is free again
    finally:
        w.close()
    for p in bundle.rglob("*"):
        if p.is_file():
            assert b"zq77secret" not in p.read_bytes(), p


# -- the share of a process -----------------------------------------------------------------------------------------------------


def another_process(bundle: Path) -> Wiki:
    """A wiki on the same bundle with a hub of its own: what another process knows of the journal (it shares the files, the locks and the
    tail hint, not the entries it wrote)."""
    w = make(bundle)
    w._hub = F.Hub(bundle)
    w._hub.refs = 1
    w._stop_filer()
    return w


def fill(w: Wiki, tag: str, at_most: int) -> tuple[int, QueueFull | None]:
    """Remember until refused (or `at_most`): how many were kept, and the refusal."""
    for i in range(at_most):
        try:
            w.remember(f"topic: {tag}-{i}\n{tag} entry {i}", timeout=0.05)
        except QueueFull as e:
            return i, e
    return at_most, None


def other_writer(bundle: Path, n: int, tag: str = "other") -> None:
    """Entries of a writer that is none of the processes of the test (written in the journal, as another process would)."""
    j = J.Journal(bundle)
    try:
        for i in range(n):
            j.append_entry(f"topic: {tag}-{i}\n{tag} entry {i}", key=f"{tag}-{i}", hash=f"{tag}{i}")
    finally:
        j.close()


def test_busy_processes_leave_the_last_places_to_a_writer_that_keeps_few(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a limit of 64: a process that keeps 8 of its own waiting does not get the last 8 places. Two busy jobs together stop at 56;
    a person (another process, a few entries) still finds room."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    job, job2, person = another_process(bundle), another_process(bundle), another_process(bundle)
    try:
        assert fill(job2, "job2", 8) == (8, None)  # busy from now on
        kept, refusal = fill(job, "job", 100)
        assert kept == 48 and refusal is not None  # 8 while it kept few, then up to 56 in all
        assert "keeps 48 entries" in str(refusal) and "does not get the last 8 places" in str(refusal)
        assert fill(job2, "job2-more", 10)[0] == 0
        assert fill(person, "person", 7) == (7, None)
        assert len(open_entries(bundle)) == 56 + 7
    finally:
        for w in (job, job2, person):
            w.close()


def fill_as(w: Wiki, author: str | None, tag: str, at_most: int) -> tuple[int, QueueFull | None]:
    """`fill`, each entry written by `author` (None: no author named)."""
    for i in range(at_most):
        try:
            w.remember(f"topic: {tag}-{i}\n{tag} entry {i}", author=author, timeout=0.05)
        except QueueFull as e:
            return i, e
    return at_most, None


def test_a_job_and_a_person_in_one_process_are_two_writers(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One process (a server) with a job and a person, each naming its author: the job that keeps 8 does not get the last 8 places,
    the person, in the same process, does; the refusal names the writer."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    server = another_process(bundle)
    try:
        kept, refusal = fill_as(server, "import-job", "job", 100)
        assert kept == 56 and refusal is not None and "'import-job'" in str(refusal) and "keeps 56 entries" in str(refusal)
        assert fill_as(server, "marco", "person", 7) == (7, None)
        assert len(open_entries(bundle)) == 63
    finally:
        server.close()


def test_the_calls_of_a_process_that_name_no_author_are_one_writer(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    server = another_process(bundle)
    try:
        assert fill_as(server, None, "a", 40)[0] == 40
        kept, refusal = fill_as(server, None, "b", 40)  # another call naming none: the same writer, already busy
        assert kept == 16 and refusal is not None and "name no author" in str(refusal)
        assert fill_as(server, "marco", "person", 3) == (3, None)  # a writer that names itself still has the last places
    finally:
        server.close()


def test_a_share_refusal_gives_the_time_until_the_writer_gets_a_place_not_the_time_to_empty_the_queue(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of 64 and a filer of 0.5 entries/s: a job alone that keeps 56 gets a place again when one entry goes (2 s), and one
    that keeps 8 at the head of a queue of 60 when its first one goes (2 s), not when the whole queue does (112 s, 120 s)."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    job = another_process(bundle)
    try:
        monkeypatch.setattr(job._journal(), "pace", lambda: 0.5)
        kept, refusal = fill(job, "job", 100)
        assert kept == 56 and refusal is not None and refusal.reason == "share"
        assert refusal.retry_after_s == 2.0
    finally:
        job.close()


def test_a_share_refusal_of_a_writer_whose_entries_lead_the_queue_counts_its_own(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    job = another_process(bundle)
    try:
        monkeypatch.setattr(job._journal(), "pace", lambda: 0.5)
        assert fill(job, "job", 8) == (8, None)  # at the head of the queue, and busy from now on
        other_writer(bundle, 52)  # 60 in all: under the limit, over the 56 places the job may fill
        kept, refusal = fill(job, "job-more", 1)
        assert kept == 0 and refusal is not None and refusal.reason == "share"
        assert refusal.retry_after_s == 2.0  # its first entry goes first; 5 of anyone would be 10 s
    finally:
        job.close()


def test_a_process_whose_entries_are_filed_gets_places_again(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    job = another_process(bundle)
    try:
        kept, refusal = fill(job, "job", 100)
        assert kept == 56 and refusal is not None
        for r in open_entries(bundle)[:52]:  # filed by the filer of another process
            close_in_another_journal(bundle, str(r.meta["id"]))
        other_writer(bundle, 52)  # another writer keeps the queue at 56
        assert fill(job, "job-again", 3) == (3, None)  # it keeps 4 of its own now: the last places are open to it again
    finally:
        job.close()


def test_the_share_counts_only_the_entries_of_the_process_open_in_the_journal(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ids the process wrote whose closure it could not see (they are in no entry it read) never make it busy."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    other_writer(bundle, 60)
    person = another_process(bundle)
    try:
        person._journal().mine.update({f"gone{i:02d}": "" for i in range(40)})
        assert fill(person, "person", 1) == (1, None)
    finally:
        person.close()


def test_a_journal_read_again_from_its_start_forgets_the_entries_the_process_wrote(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = another_process(bundle)
    try:
        fill(w, "mine", 3)
        hub = w._journal()
        hub.refresh()  # the last of them read back
        assert hub.own_open() == 3 and len(hub.mine) == 3
        real = hub.journal.read_from

        def again(pos: Any = None) -> Any:  # what `read_from` says when the bundle was made again under the process
            out = real(None)
            hub.journal.restarted = True
            return out

        monkeypatch.setattr(hub.journal, "read_from", again)
        hub.refresh()
        assert not hub.mine
    finally:
        w.close()


def test_a_busy_process_whose_view_is_behind_does_not_take_a_kept_place(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The busy job finds one place left under its share and passes the check; another process writes before the job reaches the write:
    under the admission lock the job judges by the hint (one record written since) and must not take the place kept for others."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 64)
    job, person = another_process(bundle), another_process(bundle)
    try:
        assert fill(job, "job", 55) == (55, None)  # busy, one place left under its 56
        hub = job._journal()
        ready, go = threading.Event(), threading.Event()
        real_free = hub.disk_free

        def free() -> int:  # between the job's check and its write
            ready.set()
            go.wait(10)
            return real_free()

        monkeypatch.setattr(hub, "disk_free", free)
        out: list[str] = []

        def call() -> None:
            try:
                job.remember("topic: job-last\nthe last one", timeout=0.3)
                out.append("ok")
            except QueueFull:
                out.append("full")

        th = threading.Thread(target=call)
        th.start()
        assert ready.wait(10)
        person.remember("topic: person-0\na memory of the person", timeout=0.3)
        go.set()
        th.join(30)
        assert out == ["full"] and len(open_entries(bundle)) == 56
    finally:
        for w in (job, person):
            w.close()
