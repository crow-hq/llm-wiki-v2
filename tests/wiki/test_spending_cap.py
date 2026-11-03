# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The spending cap of a sync (`max_cost`), the history it keeps and `estimate`, in the library and the command."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from llmw2 import BatchReport, ChangeBatch, InputError, Item, Origin, SpendingCap, SyncReport, Wiki, cli, estimate, sync
from llmw2.agents.librarian import IngestResult
from llmw2.config import ClassifierConfig
from llmw2.models.classifier import Classifier
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM, FakeLLM
from tests.wiki.test_crow_plan import add_folder
from tests.wiki.test_ingest_many import Probe, make_wiki
from tests.wiki.test_llm import completion, make_llm
from tests.wiki.test_sources import Fake, remove, saved_cursor, topic_files, upsert, use

IDS = [f"d{n}" for n in range(12)]
FILING = 5  # most calls (1.0 each) the filing of one document makes: route, name_folder, find_match, merge, relate


def pages(ids: list[str] = IDS, size: int = 4) -> list[ChangeBatch]:
    """The documents in pages of `size`, the cursor after each "c<n>"."""
    chunks = [ids[i : i + size] for i in range(0, len(ids), size)]
    return [ChangeBatch([upsert(i) for i in chunk], f"c{n + 1}", more=n + 1 < len(chunks)) for n, chunk in enumerate(chunks)]


def summarized(llm: ContentLLM) -> list[str]:
    """The topic of each summarize call, in order."""
    return [m[1]["content"].split("topic: ")[-1].split("\n")[0] for op, m in llm.calls if op == "librarian/summarize"]


def history(wiki: Wiki) -> list[dict[str, float]]:
    lines = (wiki.cfg.bundle / ".llmw2" / "sync" / "history.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def notes(wiki: Wiki) -> list[str]:
    return sorted(n.title for n in wiki.store.load().all_notes())


# -- the cap ----------------------------------------------------------------------------


@pytest.mark.parametrize("concurrency", [1, 4])
def test_sync_stops_at_the_spending_cap_and_passes_it_by_at_most_the_calls_in_flight(bundle: Path, concurrency: int) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0, jitter=0.002)
    wiki = make_wiki(bundle, concurrency=concurrency, llm=llm)
    source = Fake(*pages(), files=topic_files(*IDS))

    report = sync(wiki, source, max_cost=30.0)

    spent = wiki.tracker.spent()
    assert report.stopped and "spending cap" in report.stopped
    assert 30.0 <= spent <= 30.0 + concurrency * 1.0 + FILING  # the calls in flight, and those of the document being filed
    assert 0 < report.created < len(IDS) and report.errors == []
    assert saved_cursor(wiki) == "c1"  # the last page finished (4 documents), not the one the cap stopped
    assert wiki.check() == []


def test_each_lane_makes_at_most_one_call_after_the_cap_is_passed(bundle: Path) -> None:
    late: dict[int, int] = {}
    lock = threading.Lock()

    class Watching(ContentLLM):
        def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
            reply = super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)  # SpendingCap leaves before the count
            if self.usage.spent() >= 10.0:
                with lock:
                    late[threading.get_ident()] = late.get(threading.get_ident(), 0) + 1
            return reply

    llm = Watching(UsageTracker(), cost=1.0, jitter=0.004)
    wiki = make_wiki(bundle, concurrency=4, llm=llm)

    report = sync(wiki, Fake(*pages(), files=topic_files(*IDS)), max_cost=10.0)

    assert report.stopped and "spending cap" in report.stopped
    assert late and max(late.values()) == 1


def test_the_next_sync_after_a_spending_stop_goes_on_and_files_nothing_twice(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, concurrency=2, llm=llm)
    files = topic_files(*IDS)
    first = sync(wiki, Fake(*pages(), files=files), max_cost=30.0)
    done = [i for i in IDS if wiki.origin(f"fake:Team A:{i}") is not None]
    before = len(llm.calls)

    second = sync(wiki, Fake(*pages(), files=files))

    assert first.stopped and 0 < len(done) < len(IDS)
    assert second.stopped is None and second.created == len(IDS) - len(done)
    topics = summarized(llm)
    assert all(topics.count(f"red-{IDS.index(i)}") == 1 for i in done)  # what was filed was not paid for again
    assert len(llm.calls) > before and wiki.tracker.spent() > 30.0
    assert notes(wiki) == sorted(f"About red-{n}" for n in range(len(IDS)))  # every document once, none twice
    assert wiki.check() == []
    assert saved_cursor(wiki) == "c3"


def test_a_sync_without_a_cap_is_the_same_as_before(bundle: Path, tmp_path: Path) -> None:
    free = ContentLLM(UsageTracker(), cost=1.0)
    plain = make_wiki(bundle, llm=free)
    capped_llm = ContentLLM(UsageTracker(), cost=1.0)
    capped = make_wiki(tmp_path / "other", llm=capped_llm)

    sync(plain, Fake(*pages(), files=topic_files(*IDS)))
    sync(capped, Fake(*pages(), files=topic_files(*IDS)), max_cost=1000.0)  # a cap never reached changes nothing

    assert free.ops == capped_llm.ops
    capped.tracker.record("llm", "m", 1, 1, cost=5000.0)
    capped.tracker.check()  # the cap is closed with the sync
    files = lambda w: [p.relative_to(w.cfg.bundle) for p in sorted(w.cfg.bundle.rglob("*.md")) if "raw" not in p.parts]  # noqa: E731
    assert files(plain) == files(capped)  # the raw copies carry the time of the run
    assert notes(plain) == notes(capped)


def test_a_provider_that_reports_no_cost_never_reaches_the_cap(bundle: Path) -> None:
    wiki = make_wiki(bundle, llm=ContentLLM(UsageTracker()))  # Ollama: cost 0

    report = sync(wiki, Fake(*pages(), files=topic_files(*IDS)), max_cost=0.0001)

    assert report == SyncReport(created=len(IDS))


@pytest.mark.parametrize("bad", [0, -1.0])
def test_a_cap_that_is_not_above_zero_is_refused_before_anything_is_listed(bundle: Path, bad: float) -> None:
    source = Fake(*pages(), files=topic_files(*IDS))

    with pytest.raises(InputError, match="max_cost"):
        sync(make_wiki(bundle), source, max_cost=bad)

    assert source.cursors == []


def test_a_cap_is_relative_to_the_spending_when_the_sync_starts(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, llm=llm)
    wiki.tracker.record("llm", "m", 1, 1, cost=50.0)  # spent before, on something else

    report = sync(wiki, Fake(*pages(IDS[:2]), files=topic_files(*IDS[:2])), max_cost=100.0)

    assert report == SyncReport(created=2)


def test_the_cap_stops_the_sync_and_is_not_a_document_failure(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, llm=llm)

    report = sync(wiki, Fake(*pages(), files=topic_files(*IDS)), max_cost=3.0)

    assert report.errors == [] and report.stopped and "spending cap" in report.stopped  # stopped, not listed as a document's error
    with pytest.raises(SpendingCap, match="spending cap"), wiki.tracker.limit(0.5):
        wiki.tracker.record("llm", "m", 1, 1, cost=1.0)
        wiki.tracker.check()


def test_a_cap_under_what_the_first_page_costs_says_that_nothing_was_filed(bundle: Path) -> None:
    wiki = make_wiki(bundle, llm=ContentLLM(UsageTracker(), cost=1.0))

    report = sync(wiki, Fake(*pages(), files=topic_files(*IDS)), max_cost=0.5)

    assert report.created + report.merged == 0
    assert report.stopped and "spending cap" in report.stopped and "nothing was filed" in report.stopped
    assert "$" in report.stopped.rpartition("(")[2]  # and what reading and planning the page cost so far


def test_a_cap_counts_and_stops_only_the_calls_of_its_own_block(tracker: UsageTracker) -> None:
    reached, other = threading.Event(), threading.Event()
    seen: list[str] = []

    def elsewhere() -> None:  # other work on the same tracker, as an `ask` of a server while a sync runs
        reached.wait()
        tracker.record("llm", "m", 1, 1, cost=10.0)
        tracker.check()
        seen.append("not stopped")
        other.set()

    thread = threading.Thread(target=elsewhere)
    thread.start()
    with tracker.limit(2.0):
        tracker.record("llm", "m", 1, 1, cost=1.0)
        tracker.check()  # 1.0 of 2.0: what the other thread spends is not counted here
        reached.set()
        other.wait()
        tracker.check()
        tracker.record("llm", "m", 1, 1, cost=1.0)
        with pytest.raises(SpendingCap):
            tracker.check()
    thread.join()

    assert seen == ["not stopped"]


# -- where it is checked -----------------------------------------------------------------


def test_the_real_llm_checks_before_each_request(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, completion("ok", {"prompt_tokens": 5, "completion_tokens": 1, "cost": 2.0}))

    with tracker.limit(3.0):
        assert llm.complete("s", "u") == "ok"  # 2.0 spent, under 3.0
        assert llm.complete("s", "u") == "ok"  # 4.0 spent
        with pytest.raises(SpendingCap, match="spending cap"):
            llm.complete("s", "u")

    assert len(requests) == 2  # the third was never sent
    assert llm.complete("s", "u") == "ok"  # the cap is closed with the block


def test_a_classifier_answer_from_the_cache_is_served_under_a_reached_cap(tracker: UsageTracker) -> None:
    store: dict[str, dict[str, dict[str, object]]] = {}

    class Memory:
        def get(self, key: str) -> dict[str, dict[str, object]] | None:
            return store.get(key)

        def put(self, key: str, answers: dict[str, dict[str, object]]) -> None:
            store[key] = answers

    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = {"answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": {"input_tokens": 10, "output_tokens": 0, "cost": 2.0}}
        return httpx.Response(200, json=body)

    cfg = ClassifierConfig(base_url="https://router.test/api/v1", model="m", api_key="k")
    clf = Classifier(cfg, tracker, client=httpx.Client(transport=httpx.MockTransport(handle)), cache=Memory())
    question = {"q": {"type": "noul", "instructions": "Is it?"}}
    clf.ask("state", question)  # paid once: 2.0

    with tracker.limit(1.0):
        tracker.record("classifier", "m", 1, 0, cost=1.0)  # the cap is reached
        assert clf.ask("state", question) == {"q": {"type": "noul", "noul": 0.5}}  # served, free, not blocked
        with pytest.raises(SpendingCap):
            clf.ask("another state", question)

    assert len(sent) == 1 and tracker.snapshot().classifier.served == 1


# -- history and estimate ----------------------------------------------------------------


def test_a_sync_keeps_its_documents_and_cost_in_the_history_of_the_wiki(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, llm=llm)

    first = sync(wiki, Fake(*pages(IDS[:4], 4), files=topic_files(*IDS[:4])))
    paid = wiki.tracker.spent()
    stopped = sync(wiki, Fake(*pages(IDS[4:], 4), files=topic_files(*IDS)), max_cost=8.0)

    [one, two] = history(wiki)
    assert one == {"documents": first.created + first.merged, "cost_usd": pytest.approx(paid)} and one["documents"] == 4
    assert stopped.stopped and two["documents"] == stopped.created + stopped.merged
    assert one["cost_usd"] + two["cost_usd"] == pytest.approx(wiki.tracker.spent())
    assert not any(wiki.cfg.bundle.joinpath(".llmw2", "sync").glob("history.json"))  # not mistaken for a cursor


class Elsewhere(ContentLLM):
    """On its first call, other work on the same tracker (a server's `ask`, in a thread of its own) spends a fortune."""

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        if not self.calls:
            other = threading.Thread(target=self.usage.record, args=("llm", "m", 1, 1), kwargs={"cost": 100.0})
            other.start()
            other.join()
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def test_the_history_counts_only_the_calls_of_the_sync_not_other_work_on_the_tracker(bundle: Path) -> None:
    llm = Elsewhere(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, llm=llm)

    report = sync(wiki, Fake(*pages(IDS[:4], 4), files=topic_files(*IDS[:4])))

    [one] = history(wiki)
    assert one == {"documents": report.created + report.merged, "cost_usd": pytest.approx(float(len(llm.calls)))}
    assert wiki.tracker.spent() == pytest.approx(len(llm.calls) + 100.0)


def test_a_sync_that_did_nothing_adds_nothing_to_the_history(bundle: Path) -> None:
    wiki = make_wiki(bundle, llm=ContentLLM(UsageTracker(), cost=1.0))
    sync(wiki, Fake(*pages(IDS[:2]), files=topic_files(*IDS[:2])))

    sync(wiki, Fake(*pages(IDS[:2]), files=topic_files(*IDS[:2])))  # all known: skipped

    assert len(history(wiki)) == 1


def test_estimate_without_history_counts_the_documents_and_gives_no_cost(bundle: Path) -> None:
    wiki = make_wiki(bundle, llm=ContentLLM(UsageTracker(), cost=1.0))
    source = Fake(*pages(), files=topic_files(*IDS))

    found = estimate(wiki, source)

    assert found == {"documents": 12, "est_cost": None, "basis": "no history"}
    assert source.fetched == [] and source.cursors == [None, "c1", "c2"]  # every page listed, nothing fetched
    assert not (bundle / ".llmw2" / "sync").exists() or not list((bundle / ".llmw2" / "sync").glob("*"))  # no cursor saved
    assert wiki.tracker.spent() == 0 and wiki.tracker.snapshot().llm.calls == 0


def test_estimate_multiplies_the_documents_by_the_mean_cost_per_document_of_the_history(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    wiki = make_wiki(bundle, llm=llm)
    sync(wiki, Fake(*pages(IDS[:4]), files=topic_files(*IDS[:4])))
    mean = wiki.tracker.spent() / 4
    path = bundle / ".llmw2" / "sync" / "history.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + json.dumps({"documents": 0, "cost_usd": 99.0}) + "\n", encoding="utf-8")  # ignored
    # the connector lists the 4 filed (same version), 3 new and a removal
    listing = ChangeBatch([*(upsert(i) for i in IDS[:7]), upsert("gone"), remove("gone")], "c9")
    source = Fake(listing, files=topic_files(*IDS))

    found = estimate(wiki, source)

    assert found["documents"] == 3  # d0..d3 are known at their version; "gone" is last listed as a removal
    assert found["est_cost"] == pytest.approx(3 * mean)
    assert "1 past sync" in found["basis"]
    assert source.fetched == [] and saved_cursor(wiki) == "c1"  # the cursor of the real sync is untouched


# -- the command -----------------------------------------------------------------------------


@pytest.fixture
def topics(tmp_path: Path) -> Path:
    path = tmp_path / "topics"
    path.mkdir()
    for n in range(6):
        (path / f"doc-{n}.md").write_text(f"topic: red-{n}\nBody {n}.", encoding="utf-8")
    return path


def test_cli_sync_max_cost_stops_and_says_so(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    use(monkeypatch, bundle, Probe(pause=0.0), llm, concurrency=1)

    code = cli.main(["--bundle", str(bundle), "sync", str(topics), "--max-cost", "7"])

    captured = capsys.readouterr()
    assert code == 2 and "spending cap" in captured.err
    assert 7.0 <= llm.usage.spent() <= 8.0


@pytest.mark.parametrize("bad", ["0", "-2"])
def test_cli_sync_max_cost_not_above_zero_fails_and_files_nothing(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    use(monkeypatch, bundle, Probe(pause=0.0), llm)

    assert cli.main(["--bundle", str(bundle), "sync", str(topics), "--max-cost", bad]) != 0

    assert llm.calls == []


def test_cli_sync_estimate_files_nothing_and_advises_a_cap_without_history(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    use(monkeypatch, bundle, Probe(pause=0.0), llm)

    code = cli.main(["--bundle", str(bundle), "sync", str(topics), "--estimate"])

    out = capsys.readouterr().out
    assert code == 0 and "6 document(s)" in out and "--max-cost" in out
    assert llm.calls == [] and not list((bundle / ".llmw2" / "sync").glob("*.json"))


def test_cli_sync_estimate_with_history_prints_the_cost(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ContentLLM(UsageTracker(), cost=1.0)
    use(monkeypatch, bundle, Probe(pause=0.0), llm)
    assert cli.main(["--bundle", str(bundle), "sync", str(topics)]) == 0
    (topics / "doc-new.md").write_text("topic: red-9\nNew.", encoding="utf-8")
    calls = len(llm.calls)
    capsys.readouterr()

    code = cli.main(["--bundle", str(bundle), "sync", str(topics), "--estimate", "--json"])

    found = json.loads(capsys.readouterr().out)
    assert code == 0 and found["documents"] == 1 and found["est_cost"] > 0
    assert len(llm.calls) == calls


# -- the cap never costs a document a worse decision --------------------------------------------


class Bursting(ContentLLM):
    """Spends `burst` on the first call of `op` whose prompt holds `needle`, as if that call had cost a fortune."""

    def __init__(self, usage: UsageTracker, op: str, needle: str, burst: float = 100.0) -> None:
        super().__init__(usage, cost=1.0)
        self.trigger, self.needle, self.burst, self.fired = op, needle, burst, False

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        reply = super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)
        if op == self.trigger and self.needle in messages[1]["content"] and not self.fired:
            self.fired = True
            self.usage.record("llm", self.model, 1, 1, cost=self.burst)
        return reply


def three_documents() -> list[Item]:
    return [Item(f"doc-{n}.md", text=f"topic: red-{n}\nBody of {n}.", origin=Origin("t", "a", f"d{n}")) for n in range(3)]


def no_fallback(report: BatchReport) -> bool:
    return not any(d.fallback for _, r in report.results if isinstance(r, IngestResult) for d in r.decisions)


def test_a_cap_reached_while_a_document_is_summarized_leaves_it_unfiled_and_the_next_run_redoes_it(bundle: Path) -> None:
    llm = Bursting(UsageTracker(), "librarian/summarize", "red-1\n")
    wiki = make_wiki(bundle, llm=llm)
    with wiki.tracker.limit(50.0):
        report = wiki.ingest_many(three_documents())

    assert "spending cap" in (report.stopped or "")
    assert [i.filename for i, _ in report.results] == ["doc-0.md"] and no_fallback(report)  # doc-1 was paid for, not filed
    assert wiki.origin("t:a:d1") is None and wiki.origin("t:a:d2") is None

    again = wiki.ingest_many(three_documents())

    assert again.stopped is None and sorted(i.filename for i, r in again.results if r.action == "created") == ["doc-1.md", "doc-2.md"]  # type: ignore[union-attr]
    topics = summarized(llm)
    assert [topics.count(f"red-{n}") for n in range(3)] == [1, 2, 1]  # only the interrupted one is paid twice
    assert notes(wiki) == ["About red-0", "About red-1", "About red-2"] and wiki.check() == []


@pytest.mark.parametrize("op", ["librarian/route", "librarian/find_match", "librarian/relate"])
def test_a_cap_reached_while_a_document_is_filed_does_not_interrupt_it(bundle: Path, op: str) -> None:
    llm = Bursting(UsageTracker(), op, "red-1")
    wiki = make_wiki(bundle, llm=llm)
    with wiki.tracker.limit(50.0):
        report = wiki.ingest_many(three_documents())

    assert "spending cap" in (report.stopped or "")
    [_, (_, second)] = report.results  # doc-1 ended with all its decisions; doc-2 was not started
    assert [i.filename for i, _ in report.results] == ["doc-0.md", "doc-1.md"] and no_fallback(report)
    assert second.related == ["red/about-red-0.md"] and "relate" in [d.step for d in second.decisions]  # type: ignore[union-attr]
    assert wiki.origin("t:a:d2") is None and wiki.check() == []

    wiki.ingest_many(three_documents())

    assert notes(wiki) == ["About red-0", "About red-1", "About red-2"]
    assert [summarized(llm).count(f"red-{n}") for n in range(3)] == [1, 1, 1]  # nothing paid twice


def test_a_cap_reached_before_the_plan_stops_the_window_and_files_nothing(crow_wiki: Wiki, llm: FakeLLM, tracker: UsageTracker) -> None:
    llm.cost = 1.0
    add_folder(crow_wiki, "/", "finance", "Money.")
    with tracker.limit(1.0):
        tracker.record("llm", "m", 1, 1, cost=1.0)
        report = crow_wiki.ingest_many(three_documents())

    assert "spending cap" in (report.stopped or "") and report.results == [] and len(report.not_done) == 3
    assert llm.calls == [] and crow_wiki.origin("t:a:d0") is None  # the plan did not take the cap for a failed plan
