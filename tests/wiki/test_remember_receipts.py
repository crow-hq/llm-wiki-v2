# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The receipts and the queue of `remember`: a receipt for an entry the hub could not read, an entry given up whose text is kept, the
warning of `status` during a live section, the stall of a full queue, and the retry after a hook that failed."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import threading
import types
from pathlib import Path
from typing import Any

import pytest

from llmw2 import FilerError, QueueFull
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle import txn
from llmw2.bundle.store import WikiStore
from llmw2.models.usage import UsageTracker
from tests.wiki.test_journal import seg_path
from tests.wiki.test_remember import Gated, make, notes

AT = "2026-01-01T00:00:00.000000+00:00"


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


# -- an entry the hub has not read is never "filed" -----------------------------------------------------------------------


def test_an_entry_behind_a_fault_is_not_filed_and_nothing_says_it_is(bundle: Path) -> None:
    a, _ = make(bundle)
    a.remember("topic: robots-1\nfirst text").wait(30)
    a.remember("topic: robots-2\nsecond text").wait(30)
    a.drain(30)
    first = next(r for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_ENTRY)
    a.close()
    path = seg_path(bundle, 1)
    data = bytearray(path.read_bytes())
    data[first.off + J.HEAD_SIZE + 6] ^= 0xFF  # a record that was flushed and is not valid: the entries after it are not read
    path.write_bytes(bytes(data))
    b, _ = make(bundle)
    try:
        r = b.remember("topic: robots-3\nthird text")
        with pytest.raises(FilerError):
            _ = r.state
        with pytest.raises(FilerError):
            r.wait(1)
        with pytest.raises(FilerError):
            b.drain(1)
    finally:
        b.close()


# -- an entry given up keeps its text ----------------------------------------------------------------------------------------


def test_compaction_does_not_zero_the_segment_of_an_entry_that_was_given_up(tmp_path: Path) -> None:
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, segment_size=1 << 20)
    j = hub.journal
    j.prepare()
    bad = j.append_entry("the text a model could not file", key="kx", hash="h", at=AT)
    for _ in range(3):
        j.append(J.T_ATTEMPT, {"id": bad.id, "error": "ReplyInvalid"})
    j.append(J.T_ABANDON, {"id": bad.id, "error": "ReplyInvalid"})
    while True:
        r = j.append_entry("x" * 4000, key="kp", hash="h", at=AT)
        j.append(J.T_FILED, {"id": r.id, "note": "a.md"})
        j.append(J.T_DONE, {"id": r.id})
        if r.seg != bad.seg:
            break
    hub.refresh()
    assert bad.seg in j.reusable()  # all entries of its segment are closed: a candidate, but one holds a text that is kept
    F.Filer._compact(types.SimpleNamespace(hub=hub, stopping=False))  # type: ignore[arg-type]
    texts = [x.text for x in J.Journal(tmp_path, writable=False).read().records if x.type == J.T_ENTRY and x.meta.get("id") == bad.id]
    assert texts == [b"the text a model could not file"]
    hub.journal.close()


# -- status: no alarm during a live section -------------------------------------------------------------------------------------


@pytest.mark.parametrize("prealloc", [True, False])
def test_status_does_not_warn_of_an_interrupted_filing_during_a_live_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prealloc: bool
) -> None:
    monkeypatch.setattr(txn, "PREALLOCATED", prealloc)
    st = WikiStore(tmp_path, actor="t")
    st.init()
    hub = F.Hub(tmp_path)
    with st.lock():
        txn.append(tmp_path / "log.md", "x\n")
        assert not [w for w in F.status(hub, tmp_path)["warnings"] if "interrupted" in w]
    section = txn.Txn(tmp_path)  # an orphan: a section whose process is gone, the lock free
    with txn.active(section):
        txn.append(tmp_path / "log.md", "y\n")
    section.abandon()
    assert txn.pending(tmp_path)
    assert [w for w in F.status(hub, tmp_path)["warnings"] if "interrupted" in w]
    hub.journal.close()


# -- a full queue and a slow filer ---------------------------------------------------------------------------------------------


def test_a_full_queue_with_a_slow_filer_waits_and_does_not_raise_after_the_stall(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    monkeypatch.setattr(F, "STALL", 0.3)
    llm = Gated(UsageTracker())
    w, _ = make(bundle, llm)
    try:
        w.remember("topic: a-1\none")
        w.remember("topic: a-2\ntwo")
        threading.Timer(1.0, llm.gate.set).start()  # the filer is slow, not stopped: longer than STALL
        r = w.remember("topic: a-3\nthree")  # no timeout: waits for room
        assert r.wait(30)["action"] == "created"
    finally:
        llm.gate.set()
        w.close()


def test_a_full_queue_that_nobody_files_still_refuses_after_the_stall(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    monkeypatch.setattr(F, "STALL", 0.3)
    w, _ = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
        with pytest.raises(QueueFull):
            w.remember("topic: a-2\ntwo")
    finally:
        w.close()


# -- the retry after a hook that failed ----------------------------------------------------------------------------------------


def test_a_filing_whose_f_record_failed_is_recovered_by_the_retry_and_says_created(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = F.Filing.put
    calls = {"n": 0}

    def put(self: F.Filing, record: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("the journal could not be written")
        real(self, record)

    monkeypatch.setattr(F.Filing, "put", put)
    w, _ = make(bundle)
    try:
        r = w.remember("topic: robots-1\nThe robot left the dock.")
        with pytest.raises(FilerError):
            r.wait(30)  # the first attempt failed: said once
        done = r.wait(30)
        assert done["action"] == "created" and len(notes(bundle)) == 1
        w.drain(30)
    finally:
        w.close()
