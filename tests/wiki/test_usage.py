# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import threading
from pathlib import Path

from okf_wiki.usage import UsageReport, UsageTracker


def test_record_splits_llm_and_classifier_ledgers(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 100, 10, op="librarian/summarize")
    tracker.record("llm", "gemini", 200, 20, op="librarian/merge")
    tracker.record("classifier", "jev", 50, 0, op="route")

    report = tracker.snapshot()

    assert (report.llm.calls, report.llm.input_tokens, report.llm.output_tokens) == (2, 300, 30)
    assert (report.classifier.calls, report.classifier.input_tokens, report.classifier.output_tokens) == (1, 50, 0)


def test_record_keeps_a_ledger_per_model(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 100, 10)
    tracker.record("llm", "claude", 300, 30)
    tracker.record("classifier", "jev", 50, 0)

    by_model = tracker.snapshot().by_model

    assert set(by_model) == {"llm:gemini", "llm:claude", "classifier:jev"}
    assert by_model["llm:claude"].total_tokens == 330
    assert by_model["classifier:jev"].calls == 1


def test_a_response_without_usage_counts_as_missing(tracker: UsageTracker) -> None:
    tracker.record("classifier", "jev", None, None)
    tracker.record("classifier", "jev", 50, 0)

    classifier = tracker.snapshot().classifier

    assert (classifier.calls, classifier.missing, classifier.input_tokens) == (2, 1, 50)
    assert tracker.snapshot().by_model["classifier:jev"].missing == 1


def test_span_collects_only_the_calls_made_inside_it(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 1, 1)
    with tracker.span() as span:
        tracker.record("llm", "gemini", 100, 10)
        tracker.record("classifier", "jev", 50, 0)
    tracker.record("llm", "gemini", 1, 1)

    assert (span.llm.calls, span.llm.total_tokens) == (1, 110)
    assert (span.classifier.calls, span.classifier.total_tokens) == (1, 50)
    assert tracker.snapshot().llm.calls == 3


def test_nested_spans_both_get_the_inner_calls(tracker: UsageTracker) -> None:
    with tracker.span() as outer:
        tracker.record("llm", "gemini", 100, 10)
        with tracker.span() as inner:
            tracker.record("classifier", "jev", 50, 0)

    assert (outer.llm.calls, outer.classifier.calls) == (1, 1)
    assert (inner.llm.calls, inner.classifier.calls) == (0, 1)


def test_spans_in_two_threads_do_not_see_each_others_calls(tracker: UsageTracker) -> None:
    both_open = threading.Barrier(2)
    both_recorded = threading.Barrier(2)
    reports: dict[str, UsageReport] = {}

    def work(name: str, tokens: int) -> None:
        with tracker.span() as span:
            both_open.wait(timeout=5)
            tracker.record("llm", name, tokens, 0)
            both_recorded.wait(timeout=5)
        reports[name] = span

    threads = [threading.Thread(target=work, args=(name, tokens)) for name, tokens in (("a", 10), ("b", 20))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert set(reports["a"].by_model) == {"llm:a"}
    assert set(reports["b"].by_model) == {"llm:b"}
    assert (reports["a"].llm.input_tokens, reports["b"].llm.input_tokens) == (10, 20)
    total = tracker.snapshot()
    assert (total.llm.calls, total.llm.input_tokens) == (2, 30)


def test_snapshot_is_a_copy(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 100, 10)

    snapshot = tracker.snapshot()
    snapshot.llm.calls = 99
    snapshot.by_model["llm:gemini"].input_tokens = 0
    snapshot.by_model["llm:other"] = snapshot.llm

    fresh = tracker.snapshot()
    assert fresh.llm.calls == 1
    assert fresh.by_model["llm:gemini"].input_tokens == 100
    assert set(fresh.by_model) == {"llm:gemini"}


def test_log_path_gets_one_json_line_per_call(tmp_path: Path) -> None:
    log_path = tmp_path / "usage.jsonl"
    tracker = UsageTracker(log_path)

    tracker.record("llm", "gemini", 100, 10, op="librarian/summarize", seconds=1.23456, cost=0.002)
    tracker.record("classifier", "jev", None, None, op="route")

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [{k: v for k, v in line.items() if k != "ts"} for line in lines] == [
        {"kind": "llm", "model": "gemini", "op": "librarian/summarize", "input_tokens": 100, "output_tokens": 10,
         "seconds": 1.235, "cost_usd": 0.002, "cached_tokens": None},
        {"kind": "classifier", "model": "jev", "op": "route", "input_tokens": None, "output_tokens": None,
         "seconds": 0.0, "cost_usd": None, "cached_tokens": None},
    ]
    assert all(line["ts"] for line in lines)


def test_reset_clears_the_totals(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 100, 10)
    tracker.record("classifier", "jev", None, None)

    tracker.reset()

    assert tracker.snapshot().to_dict() == UsageReport().to_dict()


def test_summary_reports_both_ledgers(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 1200, 34)
    tracker.record("llm", "gemini", 300, 6)
    tracker.record("classifier", "jev", 50, 0)

    assert tracker.snapshot().summary() == "llm 2 calls 1,500 in / 40 out | classifier 1 calls 50 in / 0 out"


def test_to_dict_includes_total_tokens(tracker: UsageTracker) -> None:
    tracker.record("llm", "gemini", 100, 10)
    tracker.record("classifier", "jev", None, None)

    data = tracker.snapshot().to_dict()

    assert data["llm"] == {
        "calls": 1, "input_tokens": 100, "output_tokens": 10, "missing": 0, "seconds": 0.0, "cost_usd": 0.0, "cached_tokens": 0, "total_tokens": 110
    }
    assert data["classifier"]["total_tokens"] == 0
    assert data["classifier"]["missing"] == 1
    assert data["by_model"]["llm:gemini"]["total_tokens"] == 110


def test_latency_and_reported_cost_add_up_per_ledger(tracker: UsageTracker) -> None:
    tracker.record("llm", "m", 10, 1, seconds=2.0, cost=0.001)
    tracker.record("llm", "m", 10, 1, seconds=3.0, cost=None)
    tracker.record("classifier", "jev", 5, 0, seconds=0.25, cost=0.0000002)

    report = tracker.snapshot()

    assert (report.llm.seconds, report.llm.cost_usd) == (5.0, 0.001)
    assert (report.classifier.seconds, report.classifier.cost_usd) == (0.25, 0.0000002)


def test_by_op_splits_each_ledger_per_step(tracker: UsageTracker) -> None:
    tracker.record("llm", "m", 10, 5, op="librarian/summarize")
    tracker.record("classifier", "jev", 7, 1, op="route")
    tracker.record("classifier", "jev", 3, 0, op="route")

    by_op = tracker.snapshot().to_dict()["by_op"]

    assert by_op["llm:librarian/summarize"]["total_tokens"] == 15
    assert (by_op["classifier:route"]["calls"], by_op["classifier:route"]["input_tokens"]) == (2, 10)


def test_cached_prompt_tokens_are_counted_per_ledger_and_step(tracker: UsageTracker) -> None:
    tracker.record("llm", "m", 1000, 10, op="researcher/answer", cached=768)
    tracker.record("llm", "m", 500, 10, op="researcher/answer")

    report = tracker.snapshot()

    assert report.llm.cached_tokens == 768 and report.by_op["llm:researcher/answer"].cached_tokens == 768
