# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The filer prepares ahead (`Filer._prefetch`): the model calls before the wiki's lock of up to `cfg.concurrency` entries run at once,
the sections stay one at a time in the journal's order; a slot that frees starts the next preparation, also while the filer waits for a
slow one at the head; a close does not wait for the preparations and nothing of them is written; an entry forgotten while it is prepared
is never filed."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_purge_journal import complete, leftovers
from tests.wiki.test_remember_queue import open_entries

SLOW = 0.3


def make(bundle: Path, concurrency: int = 4) -> Wiki:
    w = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=concurrency), llm=ContentLLM(UsageTracker()))
    w.init()
    return w


class Slow:
    """`Wiki._prepare` that takes SLOW seconds (a real model's summary), counting how many run at once; `hold` keeps them until set."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, hold: threading.Event | None = None) -> None:
        self.now = self.peak = 0
        self.lock = threading.Lock()
        self.hold = hold
        self.real = Wiki._prepare
        slow = self

        def prepare(wiki: Wiki, source: Any) -> Any:
            return slow.prepare(wiki, source)

        monkeypatch.setattr(Wiki, "_prepare", prepare)

    def prepare(self, wiki: Wiki, source: Any) -> Any:
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            if self.hold is not None:
                self.hold.wait(10)
            time.sleep(SLOW)
            return self.real(wiki, source)
        finally:
            with self.lock:
                self.now -= 1


def filed_order(bundle: Path) -> list[str]:
    """The ids of the entries in the order their `F` records were written."""
    view = J.Journal(bundle, writable=False, shared_hint=False).read()
    return [str(r.meta["id"]) for r in view.records if r.type == J.T_FILED and "action" in r.meta]


@pytest.mark.parametrize("concurrency", [1, 4])
def test_the_filer_prepares_ahead_up_to_the_concurrency_and_files_in_the_journals_order(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, concurrency: int
) -> None:
    hold = threading.Event()
    slow = Slow(monkeypatch, hold)
    w = make(bundle, concurrency)
    try:
        w._stop_filer()
        ids = [w.remember(f"topic: t{i}-1\nThe text number {i} of the queue.", author=f"author-{i % 3}").id for i in range(8)]
        w._filer_stopped = False
        w._start_filer()
        end = time.monotonic() + 30
        while slow.now < concurrency and time.monotonic() < end:  # all held: the one being filed and those ahead start at once
            time.sleep(0.02)
        started = slow.now
        hold.set()
        w.drain(60)
    finally:
        hold.set()
        w.close()
    assert started == concurrency  # as many at once as the concurrency while the first is held
    assert slow.peak == concurrency  # and never more
    assert filed_order(bundle) == ids  # the sections in the journal's order, whoever wrote the entries


def test_a_close_does_not_wait_for_the_preparations_ahead_and_nothing_of_them_is_written(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold = threading.Event()
    slow = Slow(monkeypatch, hold)
    w = make(bundle)
    try:
        w._stop_filer()
        for i in range(4):
            w.remember(f"topic: t{i}-1\nThe text number {i} of the queue.")
        w._filer_stopped = False
        w._start_filer()
        end = time.monotonic() + 10
        while slow.now < 4 and time.monotonic() < end:  # the one being filed and the three ahead
            time.sleep(0.02)
        assert slow.now == 4
        t = time.monotonic()
        w.close()
        took = time.monotonic() - t
    finally:
        hold.set()
    time.sleep(SLOW + 0.5)  # the preparations end after the close: nothing of them is written
    assert took < 1.0, took
    assert len(open_entries(bundle)) == 4 and filed_order(bundle) == []
    monkeypatch.setattr(Wiki, "_prepare", slow.real)
    again = make(bundle)
    try:
        again.drain(30)
        assert open_entries(bundle) == [] and len(filed_order(bundle)) == 4  # each filed once, by the next process
    finally:
        again.close()


def test_an_entry_forgotten_while_it_is_prepared_ahead_is_never_filed(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    slow = Slow(monkeypatch, hold)
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\nThe first text, filed.")
        secret = w.remember("topic: b-1\nThe purple falcon lands at midnight.", key="k:secret")
        w._filer_stopped = False
        w._start_filer()
        end = time.monotonic() + 10
        while slow.now < 2 and time.monotonic() < end:  # the first being prepared, the secret prepared ahead
            time.sleep(0.02)
        assert slow.now == 2
        report = w.purge("k:secret")
        assert report is not None and report.entries == 1
        hold.set()
        w.drain(30)
        assert secret.state == "forgotten" and len(filed_order(bundle)) == 1
        assert complete(w)
    finally:
        hold.set()
        w.close()
    assert leftovers(bundle, "purple falcon") == []


def test_with_one_at_a_time_nothing_is_prepared_ahead(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    real = F.Filer._run_ahead

    def run_ahead(self: F.Filer, entry: F.Entry, done: Any) -> None:
        started.append(entry.id)
        real(self, entry, done)

    monkeypatch.setattr(F.Filer, "_run_ahead", run_ahead)
    w = make(bundle, 1)
    try:
        for i in range(3):
            w.remember(f"topic: t{i}-1\nThe text number {i}.")
        w.drain(30)
    finally:
        w.close()
    assert started == []


def test_a_slow_preparation_at_the_head_does_not_leave_the_slots_empty(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """While the filer waits for the summary of the entry at the head, the slots that free start the entries after it, up to
    `AHEAD * concurrency` in all: the queue is not held up by one slow model call."""
    head = threading.Event()
    lock = threading.Lock()
    finished: list[str] = []
    real = Wiki._prepare

    def prepare(wiki: Wiki, source: Any) -> Any:
        text = source(None).text
        if "number 0 " in text:
            head.wait(10)
        else:
            time.sleep(0.05)
        out = real(wiki, source)
        with lock:
            finished.append(text)
        return out

    monkeypatch.setattr(Wiki, "_prepare", prepare)
    w = make(bundle, 2)
    try:
        w._stop_filer()
        for i in range(10):
            w.remember(f"topic: t{i}-1\nThe text number {i} of the queue.")
        w._filer_stopped = False
        w._start_filer()
        end = time.monotonic() + 5
        while len(finished) < 3 and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.3)
        assert len(finished) == 3  # all the window (`AHEAD` 2 times 2) but the head, one slot at a time, and no more
        assert filed_order(bundle) == []  # the sections wait for the head, in the journal's order
        head.set()
        w.drain(30)
        assert len(filed_order(bundle)) == 10
    finally:
        head.set()
        w.close()


def test_the_entry_being_filed_is_prepared_before_the_ones_ahead(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry the filer files goes first to a free slot: the ones it prepares ahead never make it wait for its own summary."""
    order: list[str] = []
    lock = threading.Lock()
    real = Wiki._prepare

    def prepare(wiki: Wiki, source: Any) -> Any:
        with lock:
            order.append(source(None).text.split(" of ")[0].rsplit(" ", 1)[-1])
        time.sleep(0.05)
        return real(wiki, source)

    monkeypatch.setattr(Wiki, "_prepare", prepare)
    w = make(bundle, 2)
    try:
        w._stop_filer()
        for i in range(6):
            w.remember(f"topic: t{i}-1\nThe text number {i} of the queue.")
        w._filer_stopped = False
        w.drain(30)
    finally:
        w.close()
    assert order == [str(i) for i in range(6)]  # in the journal's order, the head first
