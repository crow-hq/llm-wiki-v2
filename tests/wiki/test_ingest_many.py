# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.ingest_many`: documents prepared in parallel and filed in order, and what that must never change.

Models are `ContentLLM`s, whose answers depend only on the prompt, so any interleaving of the calls gets the
same replies. Every wait on another thread has a timeout, so a broken implementation fails instead of hanging.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import (
    CLASSIC,
    BatchReport,
    Candidate,
    InputError,
    Item,
    ModelError,
    Origin,
    Source,
    StepContext,
    StepError,
    Steps,
    Wiki,
    WikiConfig,
)
from llmw2.agents import steps_classic as classic
from llmw2.agents.librarian import IngestResult
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import LLM_TOKENS, ContentLLM

TIMEOUT = 5  # seconds any test waits for another thread before calling it a bug


# -- helpers ----------------------------------------------------------------------


def make_wiki(
    bundle: Path, *, concurrency: int = 1, steps: Steps | None = None, llm: ContentLLM | None = None, jitter: float = 0.0
) -> Wiki:
    llm = llm or ContentLLM(UsageTracker(), jitter=jitter)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=concurrency), llm=llm, steps=steps)
    wiki.init()
    return wiki


def item(n: int, topic: str | None = None, *, modified: str | None = None, **kwargs: Any) -> Item:
    """Document n: a source on `topic` (unique to n by default) with a line of its own."""
    topic = topic or f"red-{n}"
    origin = Origin("test", "acct", f"doc-{n:02d}", modified=modified) if modified is not None or kwargs.pop("origin", True) else None
    return Item(f"doc-{n:02d}.md", data=f"topic: {topic}\nline of document {n}.".encode(), origin=origin, **kwargs)


def filed(report: BatchReport) -> list[Item]:
    return [i for i, r in report.results if isinstance(r, IngestResult)]


def names(items: list[Item]) -> list[str]:
    return [i.filename for i in items]


TIMESTAMP = re.compile(r"\d{8}-\d{6}|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?")


def snapshot(bundle: Path) -> dict[str, str]:
    """Every file of the bundle with its timestamps blanked: what two runs must agree on byte for byte."""
    return {
        TIMESTAMP.sub("<time>", str(p.relative_to(bundle))): TIMESTAMP.sub("<time>", p.read_text(encoding="utf-8"))
        for p in sorted(bundle.rglob("*"))
        if p.is_file()
    }


def outcome(result: IngestResult) -> dict[str, Any]:
    return result.to_dict()


class Probe:
    """Steps that count what runs at once: `summarize` is the parallel part, the rest must run one at a time."""

    SERIAL = ("route", "name_folder", "match", "consolidate", "merge", "relate")

    def __init__(
        self,
        *,
        pause: float = 0.02,
        hook: Callable[[Source], None] | None = None,
        serial_hook: Callable[[str, tuple[Any, ...]], None] | None = None,
    ) -> None:
        self.pause = pause
        self.hook = hook  # called in summarize, once the item counts as in flight
        self.serial_hook = serial_hook  # called inside a serial step: (step name, its arguments after the context)
        self.lock = threading.Lock()
        self.prep_now = self.prep_max = self.serial_now = self.serial_max = 0
        self.started: list[str] = []  # the first line of each source, as summarize begins
        self.filed: list[str] = []  # the title of each candidate, as route begins: the order of filing
        self.threads: set[int] = set()
        self.reached = threading.Event()
        self.want = 0  # in-flight count at which `reached` is set
        self.steps = replace(CLASSIC, name="probe", summarize=self._summarize, **{name: self._serial(name) for name in self.SERIAL})

    def _summarize(self, ctx: StepContext, source: Source) -> Candidate:
        with self.lock:
            self.prep_now += 1
            self.prep_max = max(self.prep_max, self.prep_now)
            self.started.append(source.text.splitlines()[0])
            self.threads.add(threading.get_ident())
            if self.want and self.prep_now >= self.want:
                self.reached.set()
        try:
            if self.want:
                assert self.reached.wait(TIMEOUT), f"never {self.want} in flight"
            if self.hook:
                self.hook(source)
            time.sleep(self.pause)
            return classic.summarize(ctx, source)
        finally:
            with self.lock:
                self.prep_now -= 1

    def _serial(self, name: str) -> Callable[..., Any]:
        real = getattr(classic, name)

        def step(ctx: StepContext, *args: Any) -> Any:
            with self.lock:
                self.serial_now += 1
                self.serial_max = max(self.serial_max, self.serial_now)
                if name == "route":
                    self.filed.append(args[1].title)
            try:
                if self.serial_hook:
                    self.serial_hook(name, args)
                time.sleep(self.pause / 4)
                return real(ctx, *args)
            finally:
                with self.lock:
                    self.serial_now -= 1

        return step


# -- Item and BatchReport -------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        {},
        {"text": "a", "data": b"a"},
        {"text": "a", "fetch": lambda: b"a"},
        {"data": b"a", "fetch": lambda: b"a"},
        {"text": "a", "data": b"a", "fetch": lambda: b"a"},
    ],
    ids=["none", "text+data", "text+fetch", "data+fetch", "all-three"],
)
def test_item_when_not_exactly_one_content_is_given_then_input_error(given: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        Item("a.md", **given)


@pytest.mark.parametrize("given", [{"text": "a"}, {"data": b"a"}, {"fetch": lambda: b"a"}], ids=["text", "data", "fetch"])
def test_item_with_one_content_is_accepted_and_frozen(given: dict[str, Any]) -> None:
    it = Item("a.md", **given)

    assert (it.origin, it.resource, it.fields) == (None, None, {})
    with pytest.raises(dataclasses.FrozenInstanceError):
        it.filename = "b.md"  # type: ignore[misc]


def test_ingest_many_of_nothing_reports_nothing(bundle: Path) -> None:
    report = make_wiki(bundle).ingest_many([])

    assert (report.results, report.not_done, report.stopped) == ([], [], None)


def test_ingest_many_files_text_items_and_fetched_items_like_data_items(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=2)
    items = [
        Item("a.md", text="topic: red-1\nfrom text."),
        Item("b.md", data=b"topic: red-2\nfrom data."),
        Item("c.md", fetch=lambda: b"topic: red-3\nfrom fetch."),
    ]

    report = wiki.ingest_many(items)

    assert [r.action for _, r in report.results if isinstance(r, IngestResult)] == ["created"] * 3
    assert wiki.check() == []
    assert len(list((bundle / "red").glob("about-*.md"))) == 3


# -- order ----------------------------------------------------------------------------


@pytest.mark.parametrize("k", [1, 3])
def test_ingest_many_files_by_origin_modified_then_filename_with_undated_items_last(bundle: Path, k: int) -> None:
    probe = Probe(pause=0.005)
    wiki = make_wiki(bundle, concurrency=k, steps=probe.steps)
    items = [
        item(1, modified="2026-03-01T00:00:00Z"),
        item(2, origin=False),  # no origin, so no date
        item(3, modified="2026-01-01T00:00:00Z"),
        item(4, modified="2026-03-01T00:00:00Z"),
        item(0, origin=False),
        item(5, modified="2026-02-01T00:00:00Z"),
        Item("doc-06.md", data=b"topic: red-6\nundated, with an origin.", origin=Origin("test", "acct", "doc-06")),
    ]

    report = wiki.ingest_many(items)

    expected = [3, 5, 1, 4, 0, 2, 6]
    assert names([i for i, _ in report.results]) == [f"doc-{n:02d}.md" for n in expected]
    assert probe.filed == [f"About red-{n}" for n in expected]


# -- K=1 is today's behaviour ---------------------------------------------------------------


def sequential(wiki: Wiki, items: list[Item]) -> list[IngestResult]:
    """What the same batch gives when filed one `ingest_file` after the other, in the batch's order."""
    def key(i: Item) -> tuple[bool, str, str]:
        modified = i.origin.modified if i.origin else None
        return (modified is None, modified or "", i.filename)

    ordered = sorted(items, key=key)
    return [wiki.ingest_file(i.data or b"", i.filename, resource=i.resource, origin=i.origin, fields=i.fields) for i in ordered]


def corpus() -> list[Item]:
    """Fourteen documents, out of order, on six topics in three folders: new notes, merges, links, two new folders."""
    topics = [
        *("red-1", "blue-1", "red-2", "red-1", "green-1", "blue-1", "red-3"),
        *("blue-2", "red-1", "green-1", "red-2", "blue-1", "green-2", "red-1"),
    ]
    days = [9, 3, 12, 1, 7, 14, 2, 10, 5, 13, 4, 8, 6, 11]
    return [item(n, topic, modified=f"2026-05-{day:02d}T08:00:00Z") for n, (topic, day) in enumerate(zip(topics, days, strict=True))]


def test_ingest_many_with_one_worker_equals_ingest_file_in_turn_byte_for_byte(tmp_path: Path) -> None:
    items = corpus()
    seq_llm, many_llm = ContentLLM(UsageTracker()), ContentLLM(UsageTracker())
    seq = make_wiki(tmp_path / "seq", llm=seq_llm)
    many = make_wiki(tmp_path / "many", llm=many_llm)

    expected = sequential(seq, items)
    report = many.ingest_many(items, concurrency=1)

    assert report.stopped is None and report.not_done == []
    assert [outcome(r) for _, r in report.results if isinstance(r, IngestResult)] == [outcome(r) for r in expected]
    assert snapshot(tmp_path / "many") == snapshot(tmp_path / "seq")
    assert many_llm.calls == seq_llm.calls  # the same prompts, in the same order
    assert many.usage.to_dict() == seq.usage.to_dict()


def test_ingest_many_with_four_workers_makes_the_same_wiki_as_one(tmp_path: Path) -> None:
    items = corpus()
    one = make_wiki(tmp_path / "one")
    four = make_wiki(tmp_path / "four", jitter=0.01)  # who finishes first is shuffled by delays that follow the prompt

    first = one.ingest_many(items, concurrency=1)
    second = four.ingest_many(items, concurrency=4)

    assert [(i.filename, outcome(r)) for i, r in second.results if isinstance(r, IngestResult)] == [
        (i.filename, outcome(r)) for i, r in first.results if isinstance(r, IngestResult)
    ]
    assert snapshot(tmp_path / "four") == snapshot(tmp_path / "one")
    assert four.check() == []
    assert sorted(four.llm.ops) == sorted(one.llm.ops)  # type: ignore[attr-defined]


# -- the limit ------------------------------------------------------------------------


@pytest.mark.parametrize("k", [1, 2, 4])
def test_ingest_many_never_prepares_more_than_k_at_once_and_does_reach_k(bundle: Path, k: int) -> None:
    probe = Probe(pause=0.03)
    probe.want = k
    wiki = make_wiki(bundle, concurrency=k, steps=probe.steps)

    report = wiki.ingest_many([item(n) for n in range(3 * k + 2)])

    assert len(filed(report)) == 3 * k + 2
    assert probe.reached.is_set()
    assert probe.prep_max == k
    assert (len(probe.threads) > 1) == (k > 1)


def test_ingest_many_without_a_count_takes_it_from_the_config_and_an_explicit_one_wins(tmp_path: Path) -> None:
    default = Probe(pause=0.03)
    wiki = make_wiki(tmp_path / "a", concurrency=3, steps=default.steps)
    explicit = Probe(pause=0.03)
    other = make_wiki(tmp_path / "b", concurrency=3, steps=explicit.steps)

    wiki.ingest_many([item(n) for n in range(9)])
    other.ingest_many([item(n) for n in range(9)], concurrency=2)

    assert default.prep_max == 3
    assert explicit.prep_max == 2


# -- the lock -------------------------------------------------------------------------


def test_ingest_many_runs_the_serial_steps_one_at_a_time_in_order(bundle: Path) -> None:
    probe = Probe(pause=0.02)
    wiki = make_wiki(bundle, concurrency=4, steps=probe.steps, jitter=0.01)
    items = [item(n, ["red-1", "red-2", "blue-1"][n % 3]) for n in range(15)]  # merges, new notes and new folders

    report = wiki.ingest_many(items)

    assert len(filed(report)) == 15
    assert probe.serial_max == 1
    assert probe.prep_max > 1  # the pause makes the workers overlap
    assert probe.filed == [f"About {['red-1', 'red-2', 'blue-1'][n % 3]}" for n in range(15)]  # in filename order


def test_ingest_many_prepares_the_next_documents_while_one_is_being_filed(bundle: Path) -> None:
    filing = threading.Event()  # document 0 is inside a serial step
    overlapped: list[bool] = []

    def hold_filing(name: str, args: tuple[Any, ...]) -> None:
        if name == "route":
            filing.set()
            # document 0's filing waits until a later document has finished its preparation
            assert released.wait(TIMEOUT), "no document was prepared while the first was filed"

    def prepare(source: Source) -> None:
        if source.text.startswith("topic: red-0\n"):
            return
        assert filing.wait(TIMEOUT), "preparation of a later document did not overlap filing"
        overlapped.append(True)
        released.set()

    released = threading.Event()
    probe = Probe(pause=0.01, hook=prepare, serial_hook=hold_filing)
    wiki = make_wiki(bundle, concurrency=2, steps=probe.steps)

    report = wiki.ingest_many([item(0), item(1), item(2)])

    assert len(filed(report)) == 3
    assert overlapped


# -- nothing lost ---------------------------------------------------------------------


def log_lines(bundle: Path) -> list[str]:
    return [line for line in (bundle / "log.md").read_text(encoding="utf-8").splitlines() if line.startswith(("- ", "* ", "##"))]


def test_many_documents_merging_into_one_note_lose_no_source_no_text_no_log_line(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=4, jitter=0.005)
    items = [item(n, "red-1") for n in range(20)] + [item(20, "red-2")]

    report = wiki.ingest_many(items)

    assert [r.action for _, r in report.results if isinstance(r, IngestResult)].count("merged") == 19
    note = wiki.store.read("red/about-red-1.md")
    sources = note.frontmatter["sources"]
    assert len(sources) == 20 and len({s["resource"] for s in sources}) == 20
    for n in range(20):
        assert f"line of document {n}." in note.body
    assert len(list((bundle / "raw").glob("*.md"))) == 21
    index = (bundle / "red" / "index.md").read_text(encoding="utf-8")
    assert index.count("about-red-1.md") == 1 and index.count("about-red-2.md") == 1
    assert "about-red-2.md" in note.body and note.body.count("about-red-2.md") == 1  # the link, once, however many merges
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert (log.count("**Merged**"), log.count("**Created**"), log.count("**Created folder**")) == (19, 2, 1)
    assert wiki.check() == []


def test_many_distinct_documents_each_get_a_note_an_index_entry_a_raw_copy_and_a_log_line(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=4, jitter=0.005)

    report = wiki.ingest_many([item(n, f"{('red', 'blue', 'green')[n % 3]}-{n}") for n in range(24)])

    assert len(filed(report)) == 24
    notes = [p for p in bundle.rglob("about-*.md")]
    assert len(notes) == 24
    for folder in ("red", "blue", "green"):
        index = (bundle / folder / "index.md").read_text(encoding="utf-8")
        assert index.count("about-") == 8
    assert len(list((bundle / "raw").glob("*.md"))) == 24
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert (log.count("**Created**"), log.count("**Created folder**")) == (24, 3)
    assert wiki.check() == []


# -- errors ---------------------------------------------------------------------------


def test_an_unreadable_item_is_its_own_error_and_the_batch_goes_on(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=3)
    items = [item(0), Item("doc-01.exe", data=b"MZ"), item(2), Item("doc-03.md", text="  \n"), item(4)]

    report = wiki.ingest_many(items)

    assert report.stopped is None and report.not_done == []
    assert names([i for i, _ in report.results]) == ["doc-00.md", "doc-01.exe", "doc-02.md", "doc-03.md", "doc-04.md"]
    assert [isinstance(r, InputError) for _, r in report.results] == [False, True, False, True, False]
    assert ".exe" in str(report.results[1][1])
    assert len(filed(report)) == 3 and wiki.check() == []


def test_a_fetch_that_raises_input_error_is_that_items_error(bundle: Path) -> None:
    def refuse() -> bytes:
        raise InputError("gone at the source")

    wiki = make_wiki(bundle, concurrency=2)

    report = wiki.ingest_many([item(0), Item("doc-01.md", fetch=refuse), item(2)])

    assert [type(r).__name__ for _, r in report.results] == ["IngestResult", "InputError", "IngestResult"]


def test_on_result_is_called_once_per_item_in_filing_order_with_the_result_or_the_error(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=3)
    seen: list[tuple[str, IngestResult | Exception]] = []

    report = wiki.ingest_many([item(0), Item("doc-01.exe", data=b"MZ"), item(2)], on_result=lambda i, r: seen.append((i.filename, r)))

    assert [name for name, _ in seen] == ["doc-00.md", "doc-01.exe", "doc-02.md"]
    assert [r for _, r in seen] == [r for _, r in report.results]


STOPS: dict[str, Callable[[], Exception]] = {
    "model-429": lambda: ModelError("llm 429 from http://llm.test: busy", status=429),
    "step": lambda: StepError("summarize", "returned nothing"),
    "other": lambda: RuntimeError("boom"),
}


@pytest.mark.parametrize("fail", STOPS.values(), ids=STOPS)
def test_a_failure_that_is_not_the_items_files_the_prefix_and_starts_nothing_new(bundle: Path, fail: Callable[[], Exception]) -> None:
    # Document 5 fails while 6 and 7 are in flight (they wait for the failure, then finish); 8 and 9 must never start.
    failed = threading.Event()

    def hook(source: Source) -> None:
        number = int(source.text.split("red-")[1].split("\n")[0])
        if number == 5:
            time.sleep(0.15)  # let 6 and 7 begin, so they are in flight
            failed.set()
            raise fail()
        if number >= 6:
            assert failed.wait(TIMEOUT)
            time.sleep(0.3)  # they finish after the failure, and may not be filed: 5 is missing before them

    probe = Probe(pause=0.0, hook=hook)
    wiki = make_wiki(bundle, concurrency=3, steps=probe.steps)
    items = [item(n) for n in range(10)]

    report = wiki.ingest_many(items)

    assert names(filed(report)) == names(items[:5])
    assert names(report.not_done) == names(items[5:])
    assert report.stopped
    assert [i for i, _ in report.results] == items[:5]
    assert set(probe.started) <= {f"topic: red-{n}" for n in range(8)}  # 8 and 9 were never started
    assert len(list(bundle.rglob("about-*.md"))) == 5 and wiki.check() == []


def test_a_persistent_429_from_the_model_stops_the_batch_with_the_prefix_filed(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), fail_on={"red-4\n": ModelError("llm 429 from http://llm.test: slow down", status=429)})
    wiki = make_wiki(bundle, concurrency=3, llm=llm)
    items = [item(n) for n in range(9)]

    report = wiki.ingest_many(items)

    assert names(filed(report)) == names(items[:4])
    assert names(report.not_done) == names(items[4:])
    assert report.stopped and "429" in report.stopped
    assert wiki.check() == []


def test_the_documents_after_an_input_error_still_count_as_the_prefix_when_a_later_one_fails(bundle: Path) -> None:
    # the unreadable item breaks nothing: the successful ones around it are filed, up to the model failure
    llm = ContentLLM(UsageTracker(), fail_on={"red-4\n": ModelError("llm 503", status=503)})
    wiki = make_wiki(bundle, concurrency=3, llm=llm)
    items = [item(0), item(1), Item("doc-02.exe", data=b"MZ"), item(3), item(4), item(5), item(6)]

    report = wiki.ingest_many(items)

    assert names([i for i, _ in report.results]) == ["doc-00.md", "doc-01.md", "doc-02.exe", "doc-03.md"]
    assert isinstance(report.results[2][1], InputError)
    assert names(report.not_done) == ["doc-04.md", "doc-05.md", "doc-06.md"]
    assert report.stopped


def test_a_failure_while_filing_stops_at_once_and_leaves_the_rest_unfiled(bundle: Path) -> None:
    def break_relate(name: str, args: tuple[Any, ...]) -> None:
        if name == "relate" and args[0].title == "About red-3":
            raise RuntimeError("disk full")

    probe = Probe(pause=0.0, serial_hook=break_relate)
    wiki = make_wiki(bundle, concurrency=4, steps=probe.steps)
    items = [item(n) for n in range(9)]

    report = wiki.ingest_many(items)

    assert report.stopped and "disk full" in report.stopped
    assert names(filed(report)) == names(items[:3])
    covered = names([i for i, _ in report.results]) + names(report.not_done)
    assert sorted(covered) == names(items)  # every item is in exactly one of the two
    assert set(names(items[4:])) <= set(names(report.not_done))
    assert probe.filed == [f"About red-{n}" for n in range(4)]  # nothing after the failure was filed


# -- "unchanged" ----------------------------------------------------------------------


def test_the_same_document_twice_in_one_batch_is_filed_once_and_the_second_is_unchanged(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=3)
    origin = Origin("test", "acct", "memo", version="v1", modified="2026-01-01T00:00:00Z")
    twin = [Item(name, data=b"topic: red-1\nthe same memo.", origin=origin) for name in ("memo-a.md", "memo-b.md")]

    report = wiki.ingest_many(twin)

    [(_, first), (_, second)] = report.results
    assert isinstance(first, IngestResult) and isinstance(second, IngestResult)
    assert (first.action, second.action) == ("created", "unchanged")
    assert second.note == first.note
    assert len(list(bundle.rglob("about-*.md"))) == 1 and len(list((bundle / "raw").glob("*.md"))) == 1
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert (log.count("**Created**"), log.count("**Created folder**")) == (1, 1)


def test_a_document_filed_by_another_thread_meanwhile_is_unchanged_when_its_turn_comes(bundle: Path) -> None:
    origin = Origin("test", "acct", "memo", version="v1")
    data = b"topic: red-1\nthe memo."
    batch_prepared, other_done = threading.Event(), threading.Event()
    results: dict[str, IngestResult] = {}

    def hook(source: Source) -> None:
        if threading.current_thread().name == "other":
            return
        batch_prepared.set()  # the batch's copy has passed the unlocked check; now the other thread files the same document
        assert other_done.wait(TIMEOUT), "the other thread never filed"

    probe = Probe(pause=0.0, hook=hook)
    wiki = make_wiki(bundle, concurrency=2, steps=probe.steps)

    def other() -> None:
        assert batch_prepared.wait(TIMEOUT)
        results["other"] = wiki.ingest_file(data, "memo.md", origin=origin)
        other_done.set()

    thread = threading.Thread(target=other, name="other")
    thread.start()
    report = wiki.ingest_many([Item("memo.md", data=data, origin=origin)])
    thread.join(TIMEOUT)

    [(_, mine)] = report.results
    assert isinstance(mine, IngestResult)
    assert (results["other"].action, mine.action) == ("created", "unchanged")
    assert mine.note == results["other"].note
    assert len(list(bundle.rglob("about-*.md"))) == 1 and len(list((bundle / "raw").glob("*.md"))) == 1


def test_a_known_document_is_unchanged_without_a_model_call(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=2)
    items = [item(n) for n in range(4)]
    wiki.ingest_many(items)
    calls = len(wiki.llm.calls)  # type: ignore[attr-defined]

    again = wiki.ingest_many(items)

    assert [r.action for _, r in again.results if isinstance(r, IngestResult)] == ["unchanged"] * 4
    assert len(wiki.llm.calls) == calls  # type: ignore[attr-defined]


# -- usage ----------------------------------------------------------------------------


def custom_extract(ctx: StepContext, data: bytes, filename: str) -> str:
    """An extract that asks the model (as an OCR step would): its call must count for the item."""
    ctx.llm.chat([{"role": "system", "content": "s"}, {"role": "user", "content": filename}], op="custom/extract")
    return data.decode("utf-8")


class CountingLLM(ContentLLM):
    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        if op == "custom/extract":
            self.calls.append((op, messages))
            self.usage.record("llm", self.model, *LLM_TOKENS, op=op)
            return "ok"
        return super().chat(messages, op=op)


def test_each_items_usage_holds_its_own_extract_summarize_and_filing_calls_and_nothing_else(tmp_path: Path) -> None:
    items = corpus()
    ocr = replace(CLASSIC, name="ocr", extract=custom_extract)
    wikis = {k: make_wiki(tmp_path / f"k{k}", concurrency=k, steps=ocr, llm=CountingLLM(UsageTracker(), jitter=0.005)) for k in (1, 4)}

    reports = {k: w.ingest_many(items) for k, w in wikis.items()}

    for k, report in reports.items():
        usages = [r.usage for _, r in report.results if isinstance(r, IngestResult)]
        assert len(usages) == len(items)
        for u in usages:
            assert u.by_op["llm:custom/extract"].calls == 1  # extract is inside the span
            assert u.by_op["llm:librarian/summarize"].calls == 1  # and summarize is counted once, not twice
            assert u.llm.calls == sum(op.calls for op in u.by_op.values())
        total = wikis[k].usage
        assert sum(u.llm.calls for u in usages) == total.llm.calls  # no call lost, none counted for two items
        assert sum(u.llm.input_tokens for u in usages) == total.llm.input_tokens
        assert sum(u.llm.output_tokens for u in usages) == total.llm.output_tokens
    # item by item the parallel run costs what the sequential one does
    assert [r.usage.to_dict() for _, r in reports[4].results if isinstance(r, IngestResult)] == [
        r.usage.to_dict() for _, r in reports[1].results if isinstance(r, IngestResult)
    ]


def test_ingest_file_counts_a_custom_extracts_model_call_in_the_result(bundle: Path) -> None:
    wiki = make_wiki(bundle, steps=replace(CLASSIC, name="ocr", extract=custom_extract), llm=CountingLLM(UsageTracker()))

    result = wiki.ingest_file(b"topic: red-1\ntext.", "a.md")

    assert result.usage.by_op["llm:custom/extract"].calls == 1
    assert result.usage.llm.calls == wiki.usage.llm.calls


def test_an_unchanged_item_is_charged_only_what_its_preparation_cost(bundle: Path) -> None:
    wiki = make_wiki(bundle, concurrency=1)
    first = item(0)
    wiki.ingest_many([first])

    [(_, result)] = wiki.ingest_many([first]).results

    assert isinstance(result, IngestResult) and result.action == "unchanged"
    assert result.usage.llm.calls == 0


def test_fetch_and_extract_run_on_a_worker_thread_not_the_callers(bundle: Path) -> None:
    caller = threading.get_ident()
    where: dict[str, int] = {}

    def fetch() -> bytes:
        where["fetch"] = threading.get_ident()
        return b"topic: red-1\nfetched."

    def extract(ctx: StepContext, data: bytes, filename: str) -> str:
        where["extract"] = threading.get_ident()
        return data.decode("utf-8")

    wiki = make_wiki(bundle, concurrency=2, steps=replace(CLASSIC, name="where", extract=extract))

    wiki.ingest_many([Item("a.md", fetch=fetch)])

    assert where["fetch"] != caller and where["extract"] != caller


def test_the_steps_of_filing_run_on_the_calling_thread(bundle: Path) -> None:
    caller = threading.get_ident()
    seen: set[int] = set()

    def note_thread(name: str, args: tuple[Any, ...]) -> None:
        seen.add(threading.get_ident())

    probe = Probe(pause=0.0, serial_hook=note_thread)
    wiki = make_wiki(bundle, concurrency=3, steps=probe.steps)

    wiki.ingest_many([item(n) for n in range(5)])

    assert seen == {caller}
