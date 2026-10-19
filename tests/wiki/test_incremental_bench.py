# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""benchmarks/incremental.py, incremental_score.py and retrieval.py, through their command lines with --dry-run (fake models)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"benchmarks_{name}", BENCH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def incremental() -> ModuleType:
    return load("incremental")


@pytest.fixture(scope="module")
def scoring() -> ModuleType:
    return load("incremental_score")


@pytest.fixture(scope="module")
def retrieval() -> ModuleType:
    return load("retrieval")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "corpus"
    folder.mkdir()
    for i in range(1, 9):
        (folder / f"{i:02d}-doc.md").write_text(f"# Doc {i}\n\nTopic: t{i % 3}. Text number {i} about the topic.\n", encoding="utf-8")
    return folder


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_each_flow_files_the_corpus_and_writes_the_run_folder(incremental: ModuleType, corpus: Path, tmp_path: Path) -> None:
    for mode, flow in (("crow", "batch"), ("crow", "single"), ("classic", "batch")):
        out = tmp_path / f"{mode}-{flow}"
        assert incremental.main(["--corpus", str(corpus), "--out", str(out), "--mode", mode, "--flow", flow, "--dry-run"]) == 0
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        assert (meta["mode"], meta["flow"], meta["documents"]) == (mode, flow, 8)
        docs = lines(out / "docs.jsonl")
        assert [d["doc"] for d in docs] == sorted(str(d["doc"]) for d in docs)
        assert all(d["note"] and (out / "wiki" / str(d["note"])).is_file() for d in docs)
        assert lines(out / "calls.jsonl")
        assert (out / "moves.jsonl").exists()


def test_a_run_folder_is_not_overwritten_and_limit_keeps_the_first_documents(incremental: ModuleType, corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    assert incremental.main(["--corpus", str(corpus), "--out", str(out), "--limit", "3", "--dry-run"]) == 0
    assert len(lines(out / "docs.jsonl")) == 3
    with pytest.raises(SystemExit):
        incremental.main(["--corpus", str(corpus), "--out", str(out), "--dry-run"])


def test_a_rerun_with_the_same_cache_is_served_from_it(incremental: ModuleType, corpus: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    for name in ("first", "second"):
        args = ["--corpus", str(corpus), "--out", str(tmp_path / name), "--flow", "single", "--cache", str(cache), "--dry-run"]
        assert incremental.main(args) == 0
    calls = lines(tmp_path / "second" / "calls.jsonl")
    assert calls
    assert all(c["served"] for c in calls)
    assert all(c["cost"] is not None for c in calls)  # the original cost of a replayed call comes from the sidecar


def test_score_reads_runs_with_a_gold_and_a_baseline(
    incremental: ModuleType, scoring: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("a", "b"):
        incremental.main(["--corpus", str(corpus), "--out", str(tmp_path / name), "--dry-run"])
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({f"{i:02d}-doc.md": f"g{i % 3}" for i in range(1, 9)}), encoding="utf-8")
    capsys.readouterr()
    assert scoring.main([str(tmp_path / "a"), str(tmp_path / "b"), "--gold", str(gold), "--baseline", str(tmp_path / "a")]) == 0
    text = capsys.readouterr().out
    assert "documents 8, errors 0" in text
    assert "gold: 8 documents, AMI" in text
    assert "cache hit rate" in text
    assert "1.00 x a" in text
    s = scoring.score(tmp_path / "a", scoring.load_gold(gold))
    assert -1.0 <= s["ami"] <= 1.0
    assert 0.0 <= s["coloc_f1"] <= 1.0


def test_ami_and_f1_are_one_for_the_same_partition_and_low_for_an_unrelated_one(scoring: ModuleType) -> None:
    gold = ["a"] * 3 + ["b"] * 3 + ["c"] * 2
    same = scoring.agreement(["x"] * 3 + ["y"] * 3 + ["z"] * 2, gold)
    assert same["ami"] == pytest.approx(1.0)
    assert same["coloc_f1"] == pytest.approx(1.0)
    crossed = scoring.agreement(["x", "y", "z", "x", "y", "z", "x", "y"], gold)
    assert crossed["ami"] < 0.1
    assert crossed["coloc_f1"] < 0.5
    one_folder = scoring.agreement(["x"] * 8, gold)
    assert one_folder["coloc_recall"] == pytest.approx(1.0)
    assert one_folder["coloc_precision"] == pytest.approx(7 / 28)


def test_a_documents_final_note_follows_the_moves_made_after_it_was_filed(scoring: ModuleType, tmp_path: Path) -> None:
    rows = [
        {"doc": "a.md", "note": "top/a.md", "log_seq": 2},
        {"doc": "b.md", "note": "top/b.md", "log_seq": 5},
        {"doc": "c.md", "note": "top/c.md", "log_seq": 6},
    ]
    moves = [
        {"seq": 3, "from": "top/a.md", "to": "top/sub/a.md"},
        {"seq": 4, "from": "top/sub/a.md", "to": "top/sub/deep/a.md"},
        {"seq": 7, "from": "top/b.md", "to": "top/sub/b.md"},
        {"seq": 1, "from": "top/c.md", "to": "elsewhere/c.md"},  # before c was filed: another note of the same name
    ]
    (tmp_path / "docs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (tmp_path / "moves.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves), encoding="utf-8")
    assert scoring.final_notes(tmp_path) == {"a.md": "top/sub/deep/a.md", "b.md": "top/sub/b.md", "c.md": "top/c.md"}


def test_the_moved_entries_of_the_log_are_read_with_their_position(incremental: ModuleType, tmp_path: Path) -> None:
    moved = "* **Moved**: [B](x/b.md) - from /top/b.md to /x/b.md"
    log = f"# Wiki log\n\n## 2026-01-02\n\n{moved}\n* **Created**: [B](top/b.md) - from /raw/b.md\n"
    (tmp_path / "log.md").write_text(log, encoding="utf-8")
    entries = incremental.log_entries(tmp_path)
    assert [verb for verb, _ in entries] == ["Created", "Moved"]  # oldest first
    assert incremental.moves_of(entries) == [{"seq": 1, "from": "top/b.md", "to": "x/b.md"}]


def test_retrieval_asks_select_on_a_copy_and_counts_hits(
    incremental: ModuleType, retrieval: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    incremental.main(["--corpus", str(corpus), "--out", str(run), "--dry-run"])
    cases = tmp_path / "cases.json"
    assert retrieval.main(["--make-cases", str(corpus), "--n", "4", "--seed", "1", "--out", str(cases)]) == 0
    listed = json.loads(cases.read_text(encoding="utf-8"))
    assert len(listed) == 4
    assert all({"id", "doc", "text"} <= c.keys() for c in listed)
    asked = [{"id": c["id"], "question": f"What does {c['doc']} say?"} for c in listed] + [{"id": "none", "question": None}]
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(asked), encoding="utf-8")
    before = {p: p.read_bytes() for p in (run / "wiki").rglob("*") if p.is_file()}
    capsys.readouterr()
    out = tmp_path / "retrieval.json"
    assert retrieval.main(["--questions", str(questions), "--cases", str(cases), str(run), "--dry-run", "--out", str(out)]) == 0
    assert "4 questions" in capsys.readouterr().out
    result = json.loads(out.read_text(encoding="utf-8"))["runs"]["run"]
    assert result["questions"] == 4
    assert 0.0 <= result["hit"] <= 1.0
    assert result["hit@1"] <= result["hit@3"] <= result["hit"]
    assert all("question" not in row for row in result["rows"])  # the results name a question by its id, never by its text
    assert before == {p: p.read_bytes() for p in (run / "wiki").rglob("*") if p.is_file()}  # the run's wiki was only read
