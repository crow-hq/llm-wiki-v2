# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of a long source are asked at once (`StepContext.parallel`): each call beside the document's own borrows a free slot of
the preparation (`holding`, the wiki's `concurrency`), so the calls in flight of all the documents being prepared stay within it; the
notes reach the combine in the order of the blocks; with no slot free the parts go in turn, as before."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, ReplyInvalid, Source, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.steps import holding
from llmw2.config import LLMConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_summarize_long import COMBINE, DRAFT, PART, READ, RecordingLLM, context, document, notes_for

PAUSE = 0.15  # seconds a part takes


def part_of(user: str) -> int:
    found = re.search(r"part (\d+) of \d+", user, re.IGNORECASE)
    assert found is not None
    return int(found.group(1))


class Gauge:
    """Counts the calls of the preparation in flight (the summary's: one call, the parts, the combine), and the parts alone. The
    calls of a filing (made under the wiki's lock, one document at a time) are not the preparation's and are not counted."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.now = self.peak = self.parts_now = self.parts_peak = 0

    def enter(self, op: str) -> None:
        with self.lock:
            if op.startswith("librarian/summarize"):
                self.now += 1
                self.peak = max(self.peak, self.now)
            if op.endswith("summarize_part"):
                self.parts_now += 1
                self.parts_peak = max(self.parts_peak, self.parts_now)

    def leave(self, op: str) -> None:
        with self.lock:
            if op.startswith("librarian/summarize"):
                self.now -= 1
            if op.endswith("summarize_part"):
                self.parts_now -= 1


class SlowParts(RecordingLLM):
    """A RecordingLLM whose part k of n takes PAUSE * (n - k + 1) seconds (the first is the slowest: they end in reverse order)."""

    def __init__(self, usage: UsageTracker, fail: dict[int, float] | None = None, together: int = 0, **handlers: Any) -> None:
        super().__init__(usage, **handlers)
        self.gauge = Gauge()
        self.fail = fail or {}  # part -> seconds before it fails
        self.together = threading.Barrier(together, timeout=10) if together else None  # parts that must all be in flight at once

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.gauge.enter(op)
        try:
            if op == PART:
                k = part_of(messages[1]["content"])
                if k in self.fail:
                    time.sleep(self.fail[k])
                    self.calls.append((op, [dict(m) for m in messages]))
                    raise ReplyInvalid(f"no notes for part {k}")
                n = int(re.search(r"part \d+ of (\d+)", messages[1]["content"], re.IGNORECASE).group(1))  # type: ignore[union-attr]
                if self.together is not None:
                    self.together.wait()  # BrokenBarrierError unless all of them are asked at once
                time.sleep(PAUSE * (n - k + 1))
            reply = super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)
            self.usage.record("llm", self.model, 10, 10, op=op)  # counted in the spans open in the context of the call
            return reply
        finally:
            self.gauge.leave(op)


def summarize(tracker: UsageTracker, slots: int | None, **extra: Any) -> SlowParts:
    llm = SlowParts(tracker, summarize_part=notes_for, summarize_combine=DRAFT, **extra)
    source = Source(document(), title="Memo")
    if slots is None:
        classic.summarize(context(llm, source), source)
    else:
        with holding(threading.BoundedSemaphore(slots)):
            classic.summarize(context(llm, source), source)
    return llm


def test_the_parts_of_a_long_source_are_asked_at_once_and_reach_the_combine_in_the_order_of_the_blocks(tracker: UsageTracker) -> None:
    llm = summarize(tracker, 8, together=4)

    assert llm.gauge.parts_peak == 4
    assert llm.ops.count(PART) == 4 and llm.ops[-1] == COMBINE
    assert [part_of(p) for p in llm.prompts(PART)] == [4, 3, 2, 1]  # recorded as they end: the first is the slowest
    combine = llm.prompts(COMBINE)[0]
    at = [combine.index(f"- notes of part {k}: Code ZX-{k}00.") for k in range(1, 5)]
    assert at == sorted(at)
    assert all(combine.index(f"Part {k} of 4") < at[k - 1] for k in range(1, 5))


def test_the_same_combine_prompt_whether_the_parts_go_at_once_or_in_turn(tracker: UsageTracker) -> None:
    at_once, in_turn = summarize(tracker, 8, together=4), summarize(tracker, 1)

    assert in_turn.gauge.parts_peak == 1
    assert [part_of(p) for p in in_turn.prompts(PART)] == [1, 2, 3, 4]
    assert at_once.prompts(COMBINE) == in_turn.prompts(COMBINE)
    assert sorted(at_once.prompts(PART)) == sorted(in_turn.prompts(PART))


def test_the_parts_borrow_only_the_free_slots_and_give_them_back(tracker: UsageTracker) -> None:
    slots = threading.BoundedSemaphore(3)
    llm = SlowParts(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")
    with holding(slots):  # this document
        with holding(slots):  # another document being prepared: one slot is left to borrow
            classic.summarize(context(llm, source), source)
        assert llm.gauge.parts_peak == 2
    assert all(slots.acquire(blocking=False) for _ in range(3))


def test_outside_a_preparation_the_parts_go_in_turn(tracker: UsageTracker) -> None:
    llm = summarize(tracker, None)

    assert llm.gauge.parts_peak == 1
    assert llm.ops == [PART, PART, PART, PART, COMBINE]


def test_the_error_of_the_first_failed_part_by_index_is_raised(tracker: UsageTracker) -> None:
    with pytest.raises(ReplyInvalid, match="part 2 of 4"):
        summarize(tracker, 8, fail={2: 0.3, 4: 0.0})  # the fourth fails first


def test_no_part_starts_after_a_failure(tracker: UsageTracker) -> None:
    llm = SlowParts(tracker, fail={1: 0.0}, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")
    with pytest.raises(ReplyInvalid, match="part 1 of 4"), holding(threading.BoundedSemaphore(2)):
        classic.summarize(context(llm, source), source)

    assert llm.ops.count(PART) <= 2 and COMBINE not in llm.ops


def test_the_calls_of_the_parts_count_in_the_usage_of_the_document(tracker: UsageTracker) -> None:
    llm = SlowParts(tracker, together=4, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")
    with holding(threading.BoundedSemaphore(8)), tracker.span() as used:
        classic.summarize(context(llm, source), source)

    assert llm.gauge.parts_peak == 4
    assert used.llm.calls == 5


# -- through the wiki: the bound is the wiki's `concurrency` ---------------------------------------------------------------


class LongLLM(ContentLLM):
    """tests.wiki.fakes.ContentLLM with the calls of a long source, each part taking PAUSE seconds, and a gauge."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.gauge = Gauge()

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.gauge.enter(op)
        try:
            if op == PART:
                time.sleep(PAUSE)
            return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)
        finally:
            self.gauge.leave(op)

    def _summarize_part(self, user: str) -> str:
        return notes_for(user)

    def _summarize_combine(self, user: str) -> dict[str, Any]:
        topic = re.search(r"topic: (\S+)", user)
        name = topic.group(1) if topic else "misc-0"
        return {"title": f"About {name}", "summary": f"All on {name}.", "tags": [name.split("-")[0]], "body": f"On {name}."}


def long_source(topic: str) -> str:
    return f"topic: {topic}\n\n" + document()


def make(bundle: Path, concurrency: int) -> tuple[Wiki, LongLLM]:
    llm = LongLLM(UsageTracker())
    w = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=concurrency, llm=LLMConfig(read_chars=READ)), llm=llm)
    w.init()
    return w, llm


def test_a_batch_keeps_the_calls_in_flight_within_its_concurrency(tmp_path: Path) -> None:
    w, llm = make(tmp_path / "wiki", 2)
    try:
        report = w.ingest_many([Item(f"d{i}.md", text=long_source(f"alpha-{i}")) for i in range(3)], concurrency=2)
    finally:
        w.close()

    assert len(report.results) == 3 and all(not isinstance(r, Exception) for _, r in report.results)
    assert llm.gauge.peak <= 2
    assert llm.gauge.parts_peak == 2  # a document with a free slot beside it asks two parts at once


def test_a_batch_at_one_asks_the_parts_in_turn(tmp_path: Path) -> None:
    w, llm = make(tmp_path / "wiki", 1)
    try:
        w.ingest_many([Item("d.md", text=long_source("alpha-1"))], concurrency=1)
    finally:
        w.close()

    assert llm.gauge.peak == 1


def test_the_filer_asks_the_parts_of_an_entry_at_once_within_the_concurrency(tmp_path: Path) -> None:
    w, llm = make(tmp_path / "wiki", 4)
    try:
        w.remember(long_source("alpha-1"), title="One")
        w.drain(30)
        assert llm.gauge.parts_peak == 4
        for i in range(3):  # three entries prepared at once (the filer's and the prefetch's) and their parts: still 4 calls at most
            w.remember(long_source(f"beta-{i}"), title=f"B{i}")
        w.drain(60)
    finally:
        w.close()

    assert llm.gauge.peak <= 4


def test_a_slot_borrowed_for_a_thread_that_cannot_start_goes_back(tracker: UsageTracker, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: the pool cannot start a thread (the process is out of them)
    from concurrent.futures import ThreadPoolExecutor

    def refuse(self: ThreadPoolExecutor, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(ThreadPoolExecutor, "submit", refuse)
    slots = threading.BoundedSemaphore(3)
    llm = SlowParts(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")

    # ACT
    with pytest.raises(RuntimeError, match="new thread"), holding(slots):
        classic.summarize(context(llm, source), source)

    # ASSERT: every slot is free again
    assert all(slots.acquire(blocking=False) for _ in range(3))


def test_with_a_writer_whose_server_runs_one_call_at_a_time_the_parts_go_in_turn(tracker: UsageTracker) -> None:
    # ARRANGE: a writer on Ollama, which queues the calls beyond its one place without a byte: a part queued behind its siblings
    # would pass the client's timeout
    llm = SlowParts(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")
    ctx = context(llm, source)
    ctx.cfg = ctx.cfg.model_copy(update={"llm": LLMConfig(provider="ollama", read_chars=READ)})

    # ACT
    with holding(threading.BoundedSemaphore(8)):
        classic.summarize(ctx, source)

    # ASSERT
    assert llm.gauge.parts_peak == 1
    assert llm.ops == [PART, PART, PART, PART, COMBINE]


def test_a_hosted_provider_s_name_at_another_address_asks_the_parts_in_turn(tracker: UsageTracker) -> None:
    # ARRANGE: provider "openai" pointed at a local server: whatever answers there may run one call at a time
    llm = SlowParts(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(), title="Memo")
    ctx = context(llm, source)
    local = LLMConfig(provider="openai", base_url="http://localhost:8080/v1", model="local", api_key="k", read_chars=READ)
    ctx.cfg = ctx.cfg.model_copy(update={"llm": local})

    # ACT
    with holding(threading.BoundedSemaphore(8)):
        classic.summarize(ctx, source)

    # ASSERT
    assert llm.gauge.parts_peak == 1
    assert llm.ops == [PART, PART, PART, PART, COMBINE]
