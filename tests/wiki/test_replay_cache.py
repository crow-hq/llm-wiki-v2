# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The replay cache of benchmarks/stability.py (B5), exercised through the CLI with --dry-run (the fake models count their calls)."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier
from tests.wiki.test_stability_metrics import load

DOCS = {
    "a1.md": "# Refund policy\n\nTopic: refunds. Customers get a refund within 14 days of the order.\n",
    "a2.md": "# Roaster maintenance\n\nTopic: roasting. The drum is cleaned every Friday.\n",
}


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


def corpus(root: Path, name: str = "corpus", docs: dict[str, str] | None = None) -> Path:
    folder = root / name
    folder.mkdir()
    texts = docs or DOCS
    for filename, text in texts.items():
        (folder / filename).write_text(text, encoding="utf-8")
    labels = {f: {"kind": "existing", "folders": ["/customers"], "merge_into": None, "group": None} for f in texts}
    (folder / "labels.json").write_text(json.dumps({"docs": labels}), encoding="utf-8")
    return folder


def go(
    stability: ModuleType,
    tmp_path: Path,
    *args: str,
    out: str = "o.json",
    cache_file: str | None = "c.json",
    runs: int = 2,
    folder: Path | None = None,
) -> dict[str, Any]:
    argv = ["--dry-run", "--runs", str(runs), "--out", str(tmp_path / out), "--corpus", str(folder or tmp_path / "corpus"), *args]
    if cache_file:
        argv += ["--cache-file", str(tmp_path / cache_file)]
    code = stability.main(argv)
    assert not code, f"stability.main returned {code}"
    return json.loads((tmp_path / out).read_text(encoding="utf-8"))


def runs_of(raw: dict[str, Any], variant: str | None = None) -> list[dict[str, Any]]:
    variants = raw["variants"]
    return variants[variant if variant else next(iter(variants))]["runs"]


def cache_leaves(node: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], int]]:
    """Every number below a key that mentions the cache, with the keys leading to it."""
    found: list[tuple[tuple[str, ...], int]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += cache_leaves(value, (*path, str(key).lower()))
    elif isinstance(node, (int, float)) and not isinstance(node, bool) and any("cache" in p for p in path):
        found.append((path, int(node)))
    return found


def tally(run: dict[str, Any], kind: str) -> tuple[int, int]:
    """(answers served by the cache, calls sent to the model) of `kind` ("llm" or "classifier") in a run record.

    The spec fixes what is counted, not the names: a key mentioning hit/served counts as served, one mentioning
    miss/model/call/went as sent to the model; `kind` must be part of the path.
    """
    leaves = [(path, n) for path, n in cache_leaves(run) if any(kind in p for p in path)]
    assert leaves, f"no cache count for {kind!r} in the run record: {sorted(run)}"
    served = sum(n for path, n in leaves if any(w in p for p in path for w in ("hit", "served")))
    went = sum(
        n
        for path, n in leaves
        if any(w in p for p in path for w in ("miss", "model", "call", "went")) and not any(w in p for p in path for w in ("hit", "served"))
    )
    return served, went


# -- replay -------------------------------------------------------------------------------------


def test_cache_llm_when_the_first_run_records_then_the_second_is_served_from_the_cache(stability: ModuleType, tmp_path: Path) -> None:
    corpus(tmp_path)

    first, second = runs_of(go(stability, tmp_path, "--cache", "llm"))

    served1, went1 = tally(first, "llm")
    served2, _ = tally(second, "llm")
    assert (served1, went1 > 0) == (0, True)  # nothing was in the cache: every call went to the model
    assert served2 > 0  # what the first run recorded is not asked again


def test_cache_when_only_llm_is_asked_then_the_classifier_is_not_cached(stability: ModuleType, tmp_path: Path) -> None:
    corpus(tmp_path)

    _, second = runs_of(go(stability, tmp_path, "--cache", "llm"))

    try:
        served, _ = tally(second, "classifier")
    except AssertionError:  # no classifier count at all is as good as zero
        served = 0
    assert served == 0


def test_cache_classifier_when_the_first_call_records_then_the_same_question_is_served_from_the_cache(
    stability: ModuleType, tmp_path: Path
) -> None:
    # The dry run files in classic mode, with no classifier: the classifier cache is exercised on a fake one directly.
    # ARRANGE
    fake = FakeClassifier(UsageTracker(), nouls={"match": {"Refunds": 0.9}})
    replay = stability.Replay(tmp_path / "c.json", {"classifier"})
    replay.wrap(None, fake)
    questions = {"n0": {"type": "noul", "instructions": "Refunds"}}

    # ACT
    first = fake.ask("a state", questions, op="match")
    second = fake.ask("a state", questions, op="match")

    # ASSERT
    assert first == second and len(fake.calls) == 1
    assert replay.take()["classifier"] == {"served": 1, "called": 1}


def test_cache_when_both_kinds_are_asked_then_each_is_kept_apart(stability: ModuleType, tmp_path: Path) -> None:
    # ARRANGE
    fake = FakeClassifier(UsageTracker(), nouls={"match": {"Refunds": 0.9}})
    replay = stability.Replay(tmp_path / "c.json", {"llm", "classifier"})
    replay.wrap(None, fake)

    # ACT
    fake.ask("a state", {"n0": {"type": "noul", "instructions": "Refunds"}}, op="match")
    replay.save()

    # ASSERT
    saved = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert len(saved["classifier"]) == 1 and saved["llm"] == {}


def test_cache_ops_when_it_names_an_operation_that_never_happens_then_nothing_is_served(stability: ModuleType, tmp_path: Path) -> None:
    corpus(tmp_path)

    _, second = runs_of(go(stability, tmp_path, "--cache", "llm", "--cache-ops", "no/such-operation"))

    assert tally(second, "llm")[0] == 0


def test_cache_ops_when_it_names_the_summarize_operation_then_only_a_part_of_the_calls_is_served(
    stability: ModuleType, tmp_path: Path
) -> None:
    corpus(tmp_path)
    everything = runs_of(go(stability, tmp_path, "--cache", "llm", out="all.json", cache_file="all.cache.json"))[1]

    _, second = runs_of(
        go(stability, tmp_path, "--cache", "llm", "--cache-ops", "librarian/summarize", out="some.json", cache_file="some.cache.json")
    )

    served_some = tally(second, "llm")[0]
    assert 0 < served_some <= tally(everything, "llm")[0]  # the summaries are answered; what is not listed is never held back


def test_cache_key_when_the_temperature_and_seed_change_then_the_answers_are_not_shared(stability: ModuleType, tmp_path: Path) -> None:
    # `base` runs at the configured temperature and no seed, `t0seed` at temperature 0 and seed 42: other keys.
    corpus(tmp_path)

    raw = go(stability, tmp_path, "--cache", "llm", "--variant", "base", "t0seed", runs=1)

    assert tally(runs_of(raw, "base")[0], "llm")[0] == 0
    served, went = tally(runs_of(raw, "t0seed")[0], "llm")
    assert (served, went > 0) == (0, True)


def test_cache_key_when_the_messages_change_then_the_answers_are_not_shared(stability: ModuleType, tmp_path: Path) -> None:
    # A first corpus fills the cache; another one with other texts shares the file but none of the prompts.
    corpus(tmp_path)
    other = corpus(
        tmp_path,
        "other",
        {"z1.md": "# Tasting bar\n\nTopic: events. Plans for a licence.\n", "z2.md": "# Hiring\n\nTopic: staff. Interview scorecard.\n"},
    )
    go(stability, tmp_path, "--cache", "llm", runs=1)

    (run,) = runs_of(go(stability, tmp_path, "--cache", "llm", runs=1, out="o2.json", folder=other))

    assert tally(run, "llm")[0] == 0


# -- the file -----------------------------------------------------------------------------------


def test_cache_file_when_a_run_ends_then_the_responses_are_saved_and_a_later_start_loads_them(
    stability: ModuleType, tmp_path: Path
) -> None:
    corpus(tmp_path)
    go(stability, tmp_path, "--cache", "llm", runs=1)
    saved = tmp_path / "c.json"
    assert saved.is_file() and json.loads(saved.read_text(encoding="utf-8"))

    (again,) = runs_of(go(stability, tmp_path, "--cache", "llm", runs=1, out="o2.json"))

    served, went = tally(again, "llm")
    assert served > 0 and went == 0  # a new process-like start: everything comes from the file


def test_cache_file_when_everything_is_served_then_the_first_response_stays_and_nothing_is_added(
    stability: ModuleType, tmp_path: Path
) -> None:
    corpus(tmp_path)
    go(stability, tmp_path, "--cache", "llm", runs=1)
    before = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))

    go(stability, tmp_path, "--cache", "llm", runs=1, out="o2.json")

    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8")) == before


def test_cache_file_when_it_is_not_given_then_it_sits_next_to_the_output_with_the_suffix_cache_json(
    stability: ModuleType, tmp_path: Path
) -> None:
    corpus(tmp_path)

    go(stability, tmp_path, "--cache", "llm", runs=1, cache_file=None)

    assert [p.name for p in tmp_path.glob("o*.cache.json")], sorted(p.name for p in tmp_path.iterdir())


def test_dry_run_with_the_cache_when_it_runs_then_it_is_free(stability: ModuleType, tmp_path: Path) -> None:
    corpus(tmp_path)

    raw = go(stability, tmp_path, "--cache", "llm", "--cache", "classifier", runs=1)

    costs = [v for run in runs_of(raw) for k, v in run.get("quality", {}).items() if k == "cost_usd"]
    assert all(cost == 0 for cost in costs)
