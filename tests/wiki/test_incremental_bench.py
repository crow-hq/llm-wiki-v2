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


def test_note_retries_counts_the_retries_and_the_source_fallbacks_of_a_result() -> None:
    from types import SimpleNamespace

    from llmw2.agents.base import Decision

    steps = ["note_retry", "note_retry", "note_source", "route"]
    result = SimpleNamespace(decisions=[Decision(s, "code", "x") for s in steps])

    assert load("incremental").note_retries(result) == {"retries": 2, "sources": 1}
    assert load("incremental").note_retries(ValueError("x")) == {"retries": 0, "sources": 0}


def test_a_run_keeps_the_preset_of_its_classifier_and_the_thresholds_of_the_environment(incremental: ModuleType) -> None:
    from llmw2 import WikiConfig

    local = {"OKF_CLASSIFIER_PROVIDER": "custom", "OKF_CLASSIFIER_MODEL": "small-local", "OKF_CLASSIFIER_BASE_URL": "http://localhost:1/v1"}
    cfg = WikiConfig.from_env(local, bundle=Path("w"))
    cfg.crow.tau_cons = 0.123  # what a local settings file could hold: never used by a run
    incremental.bench_thresholds(cfg, local)
    laya = (cfg.crow.tau_path, cfg.crow.k)
    incremental.bench_thresholds(cfg, {**local, "OKF_TAU_CONS": "0.8"})
    set_here = cfg.crow.tau_cons
    cloud = WikiConfig.from_env({}, bundle=Path("w"))
    incremental.bench_thresholds(cloud, {})

    assert laya == (0.65, 5) and set_here == 0.8
    assert (cloud.crow.tau_path, cloud.crow.k, cloud.crow.tau_cons) == (0.5, 8, 0.6)  # Jev's, as before


# -- the additions for the runs that grow: attribution of the calls, chunks, projection, order, plan, growth ----------------


@pytest.fixture(scope="module")
def growth() -> ModuleType:
    return load("growth")


@pytest.fixture
def corpus60(tmp_path: Path) -> Path:
    folder = tmp_path / "corpus60"
    folder.mkdir()
    for i in range(60):  # more than one window of the batch (50)
        text = f"# Doc {i}\n\nTopic: t{i % 7}-{i // 7}. Text number {i} about the topic.\n"
        (folder / f"{i:02d}-doc.md").write_text(text, encoding="utf-8")
    return folder


def test_every_call_says_which_document_it_belongs_to_in_both_flows(
    incremental: ModuleType, growth: ModuleType, corpus60: Path, tmp_path: Path
) -> None:
    for flow in ("batch", "single"):
        out = tmp_path / flow
        assert incremental.main(["--corpus", str(corpus60), "--out", str(out), "--flow", flow, "--dry-run"]) == 0
        calls = lines(out / "calls.jsonl")
        assert [c["filed"] for c in calls if str(c["op"]).endswith("summarize")] == list(range(60))  # each document's summary is its own
        assert {c["phase"] for c in calls} <= {"plan", "file"}
        assert all((c["phase"] == "plan") == (str(c["op"]).rsplit("/", 1)[-1] in {"plan", "plan_more", "split"}) for c in calls)
        assert any(str(c["op"]).endswith("/plan") for c in calls) == (flow == "batch")  # one at a time: no plan, only splits
        got = growth.per_doc(out)
        parts = [*got["docs"], got["plan"], got["tail"]]
        assert len(got["docs"]) == 60
        assert sum(d["cost"] for d in parts) == pytest.approx(sum(c["cost"] for c in calls))  # the sum over the documents is the total
        assert sum(d["clf"] + d["llm_dec"] + d["llm_write"] for d in parts) == sum(c["in"] + c["out"] for c in calls)


def test_no_plan_asks_for_no_plan_and_the_default_does(incremental: ModuleType, corpus60: Path, tmp_path: Path) -> None:
    for name, extra in (("plan", []), ("noplan", ["--no-plan"])):
        assert incremental.main(["--corpus", str(corpus60), "--out", str(tmp_path / name), "--dry-run", *extra]) == 0
    ops = {n: {str(c["op"]) for c in lines(tmp_path / n / "calls.jsonl")} for n in ("plan", "noplan")}
    assert "librarian/plan" in ops["plan"]
    assert not {o for o in ops["noplan"] if o.rsplit("/", 1)[-1] in {"plan", "plan_more"}}
    assert json.loads((tmp_path / "noplan" / "meta.json").read_text(encoding="utf-8"))["no_plan"] is True
    with pytest.raises(SystemExit):
        incremental.main(["--corpus", str(corpus60), "--out", str(tmp_path / "x"), "--flow", "single", "--no-plan", "--dry-run"])


def test_an_order_seed_files_the_documents_in_a_seeded_permutation(incremental: ModuleType, corpus: Path, tmp_path: Path) -> None:
    runs = {}
    for name, seed in (("a", 1), ("a2", 1), ("b", 2), ("name", None)):
        extra = [] if seed is None else ["--order-seed", str(seed)]
        for flow in ("batch", "single"):
            out = tmp_path / f"{name}-{flow}"
            assert incremental.main(["--corpus", str(corpus), "--out", str(out), "--flow", flow, "--dry-run", *extra]) == 0
            docs = lines(out / "docs.jsonl")
            assert [d["order"] for d in docs] == list(range(8))  # `order` is the position in filing order
            runs[name, flow] = [d["doc"] for d in docs]
    assert runs["a", "batch"] == runs["a", "single"] == runs["a2", "batch"]  # the same seed, the same permutation, in either flow
    assert runs["a", "batch"] != runs["name", "batch"] and runs["a", "batch"] != runs["b", "batch"]
    assert sorted(runs["a", "batch"]) == runs["name", "batch"] == sorted(runs["b", "batch"])


def test_a_chunked_run_leaves_a_snapshot_after_each_chunk_that_a_rerun_reproduces(
    incremental: ModuleType, scoring: ModuleType, retrieval: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache.json"
    for name in ("first", "second"):
        out = tmp_path / name
        args = ["--corpus", str(corpus), "--out", str(out), "--chunk", "3", "--order-seed", "4", "--cache", str(cache), "--dry-run"]
        assert incremental.main(args) == 0
    snaps = sorted(p.name for p in (tmp_path / "first").glob("ckpt-*"))
    assert snaps == ["ckpt-00003", "ckpt-00006", "ckpt-00008"]
    def digest(run: str, n: str) -> str:
        return str(json.loads((tmp_path / run / n / "meta.json").read_text(encoding="utf-8"))["digest"])

    digests = {n: [digest(r, n) for r in ("first", "second")] for n in snaps}
    assert all(a == b for a, b in digests.values())  # the same cache gives the same wiki at each checkpoint
    assert len({a for a, _ in digests.values()}) == 3  # and the wikis differ from one checkpoint to the next
    for n, count in zip(snaps, (3, 6, 8), strict=True):
        ck = tmp_path / "first" / n
        meta = json.loads((ck / "meta.json").read_text(encoding="utf-8"))
        assert meta["checkpoint"] == count == meta["filed"]
        assert [d["order"] for d in lines(ck / "docs.jsonl")] == list(range(count))
        assert len(list((ck / "wiki").rglob("*.md"))) >= count
        assert (ck / "moves.jsonl").exists() and (ck / "usage.jsonl").exists()
        assert max(c["filed"] for c in lines(ck / "calls.jsonl")) <= count
    # a checkpoint is read like a run
    capsys.readouterr()
    assert scoring.main([str(tmp_path / "first" / "ckpt-00006")]) == 0
    assert "documents 6, errors 0" in capsys.readouterr().out
    cases = tmp_path / "cases.json"
    assert retrieval.main(["--make-cases", str(corpus), "--n", "3", "--out", str(cases)]) == 0
    questions = tmp_path / "questions.json"
    asked = [{"id": c["id"], "question": "q"} for c in json.loads(cases.read_text(encoding="utf-8"))]
    questions.write_text(json.dumps(asked), encoding="utf-8")
    ck3 = str(tmp_path / "first" / "ckpt-00003")
    assert retrieval.main(["--questions", str(questions), "--cases", str(cases), ck3, "--dry-run", "--out", str(tmp_path / "r.json")]) == 0


def test_the_projection_follows_the_rule_and_then_the_runs_own_growth(incremental: ModuleType) -> None:
    flat = incremental.projected_spend([(200, 20.0)], 1000, 0.18)  # c1 = 0.1: 0.1 * 1000 + 0.18 * 0.1 * (1000^2 - 200^2) / 200
    assert flat == pytest.approx(100 + 0.18 * 0.1 * (1_000_000 - 40_000) / 200)
    steady = incremental.projected_spend([(200, 20.0), (200, 20.0), (200, 20.0)], 1000, 0.18)  # no growth seen: s = 0
    assert steady == pytest.approx(100.0)
    growing = incremental.projected_spend([(200, 20.0), (200, 24.0)], 1000, 0.0)  # the cost per document rises 0.02 over 200 documents
    assert growing == pytest.approx(100 + (100 * 0.02 / 200 / 0.1) * 0.1 * 960_000 / 200)
    assert incremental.projected_spend([(200, 20.0), (200, 16.0)], 1000, 0.18) == pytest.approx(100.0)  # never below no growth


def test_a_run_stops_at_the_chunk_where_its_projection_passes_the_limit(incremental: ModuleType, corpus: Path, tmp_path: Path) -> None:
    base = ["--corpus", str(corpus), "--chunk", "3", "--dry-run"]
    assert incremental.main([*base, "--out", str(tmp_path / "ok"), "--stop-projected", "100"]) == 0
    assert sorted(p.name for p in (tmp_path / "ok").glob("ckpt-*")) == ["ckpt-00003", "ckpt-00006", "ckpt-00008"]
    assert incremental.main([*base, "--out", str(tmp_path / "stop"), "--stop-projected", "0.000001"]) == 1
    assert sorted(p.name for p in (tmp_path / "stop").glob("ckpt-*")) == ["ckpt-00003"]  # the first chunk is the first look
    meta = json.loads((tmp_path / "stop" / "meta.json").read_text(encoding="utf-8"))
    assert "projected spend" in meta["stopped"] and meta["stop_projected"] == 0.000001
    assert len(lines(tmp_path / "stop" / "docs.jsonl")) == 3
    with pytest.raises(SystemExit):
        incremental.main(["--corpus", str(corpus), "--out", str(tmp_path / "no-chunk"), "--stop-projected", "1", "--dry-run"])


def test_the_meta_names_the_commit_and_the_plan_and_says_when_the_tree_is_not_the_commit(
    incremental: ModuleType, corpus: Path, tmp_path: Path
) -> None:
    import shutil
    import subprocess

    assert incremental.main(["--corpus", str(corpus), "--out", str(tmp_path / "run"), "--dry-run"]) == 0
    meta = json.loads((tmp_path / "run" / "meta.json").read_text(encoding="utf-8"))
    assert meta["plan"] == "writer" and meta["no_plan"] is False and "dirty" in meta
    assert meta["commit"] is None or len(meta["commit"]) == 40  # the full `git rev-parse HEAD`
    assert meta["providers"] == {"dry": len(lines(tmp_path / "run" / "usage.jsonl"))}
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo)]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run([*git, *cmd], check=True, capture_output=True)
    clean = incremental.tree_state(repo)
    assert len(clean["commit"]) == 40 and clean["dirty"] is False and clean["diff_sha256"] is None
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    edited = incremental.tree_state(repo)
    (repo / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    added = incremental.tree_state(repo)
    assert edited["commit"] == clean["commit"] and edited["dirty"] is True
    assert edited["diff_sha256"] and added["diff_sha256"] and edited["diff_sha256"] != added["diff_sha256"]
    assert incremental.tree_state(tmp_path / "nowhere")["commit"] is None


def test_the_scores_cut_the_gold_and_measure_the_tree_without_it(
    scoring: ModuleType, incremental: ModuleType, corpus60: Path, tmp_path: Path
) -> None:
    assert scoring.cut_gold({"a": "/x/y/z", "b": "x", "c": ""}, 2) == {"a": "x/y", "b": "x", "c": ""}
    assert scoring.quantiles([5, 1, 9, 3, 7, 2, 8, 4, 6, 10]) == {"n": 10, "median": 6, "p90": 10, "max": 10}
    assert scoring.quantiles([]) is None
    run = tmp_path / "run"
    assert incremental.main(["--corpus", str(corpus60), "--out", str(run), "--dry-run"]) == 0
    gold = {f"{i:02d}-doc.md": f"g{i % 3}/h{i % 5}" for i in range(60)}
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(json.dumps(gold), encoding="utf-8")
    s = scoring.score(run, gold, [1, 2])
    assert set(s["by_depth"]) == {1, 2} and s["by_depth"][1]["gold_folders"] == 3 and s["by_depth"][2]["gold_folders"] == 15
    assert s["by_depth"][2]["ami"] == pytest.approx(s["ami"])  # the gold is two levels deep
    assert 0 < s["largest_share"] <= 1 and 0 <= s["crowded_share"] <= 1 and s["max_depth"] >= 1 and s["direct_notes"]["p90"] >= 1
    docs = scoring.jsonl(run / "docs.jsonl")
    for step in ("match", "relate"):
        sizes = [len(x["scores"]) for d in docs for x in d["decisions"] if x["step"] == step and x.get("scores")]
        assert s[f"{step}_pool"] == scoring.quantiles(sizes)
    assert s["routed_docs"] == sum(any(x["step"] == "route" for x in d["decisions"]) for d in docs)
    assert s["route_cut_docs"] == 0
    text = scoring.report(s, None)
    assert "gold to 1 level: 3 folders" in text and "gold to 2 levels: 15 folders" in text and "pool (median/p90/max)" in text
    assert scoring.main([str(run), "--gold", str(gold_file), "--gold-depth", "1"]) == 0


def test_the_search_ceiling_and_the_range_of_the_cases(
    retrieval: ModuleType, incremental: ModuleType, corpus: Path, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    incremental.main(["--corpus", str(corpus), "--out", str(run), "--dry-run"])
    cases = tmp_path / "cases.json"
    assert retrieval.main(["--make-cases", str(corpus), "--n", "20", "--range", "2:6", "--out", str(cases)]) == 0
    listed = json.loads(cases.read_text(encoding="utf-8"))
    assert {c["doc"] for c in listed} == {f"{i:02d}-doc.md" for i in range(3, 7)}  # positions 2..5 of the name order
    for bad in ("5", "3:3", "a:b"):
        with pytest.raises(SystemExit):
            retrieval.main(["--make-cases", str(corpus), "--range", bad, "--out", str(cases)])
    asked = [{"id": c["id"], "question": f"What does {c['doc']} say?"} for c in listed]
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(asked), encoding="utf-8")
    out = tmp_path / "retrieval.json"
    args = ["--questions", str(questions), "--cases", str(cases), str(run), "--dry-run", "--as-asked", "--out", str(out)]
    assert retrieval.main(args) == 0
    res = json.loads(out.read_text(encoding="utf-8"))["runs"]["run"]
    assert all("search_rank" in r for r in res["rows"])
    assert res["search@16"] == pytest.approx(sum(r["search_rank"] is not None for r in res["rows"]) / len(res["rows"]))
    assert all(r["search_rank"] is not None for r in res["rows"] if r["hit"])  # what select returns the search offered first


def test_growth_fits_the_slope_per_hundred_documents_from_the_calls_or_from_the_usage_log(growth: ModuleType, tmp_path: Path) -> None:
    run = tmp_path / "A-corp"
    run.mkdir()
    calls, usage = [], []
    for i in range(50):
        for kind, op, tokens in (("llm", "librarian/summarize", 100), ("classifier", "route", 100 + 10 * i)):
            calls.append({"filed": i, "phase": "file", "kind": kind, "op": op, "cost": 0.001, "in": tokens, "out": 0, "seconds": 2.0})
            usage.append({"kind": kind, "op": op, "input_tokens": tokens, "output_tokens": 0, "cost_usd": 0.001, "seconds": 2.0})
    calls.append({"filed": 0, "phase": "plan", "kind": "llm", "op": "librarian/plan", "cost": 0.5, "in": 7, "out": 3, "seconds": 1.0})
    (run / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    (run / "usage.jsonl").write_text("".join(json.dumps(u) + "\n" for u in usage), encoding="utf-8")
    from_calls = growth.per_doc(run)
    assert len(from_calls["docs"]) == 50 and from_calls["plan"]["cost"] == 0.5 and from_calls["source"] == "calls"
    (run / "calls.jsonl").unlink()
    from_usage = growth.per_doc(run)  # a run made before `filed`: documents start at their summarize call
    assert from_usage["source"] == "usage" and from_usage["docs"] == from_calls["docs"]
    xs = [float(i) for i in range(50)]
    assert growth.slope(xs, [float(d["clf"]) for d in from_calls["docs"]]) == pytest.approx(1000.0)  # 10 tokens a document, per 100
    assert growth.loglog([1, 2, 4, 8], [3, 6, 12, 24]) == pytest.approx(1.0) and growth.loglog([1, 2, 4], [5, 5, 5]) == pytest.approx(0.0)


def test_growth_prints_the_pooled_slopes_and_the_blocks_of_a_run(
    growth: ModuleType, incremental: ModuleType, corpus60: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for arm, mode in (("C", "crow"), ("K", "classic")):
        assert incremental.main(["--corpus", str(corpus60), "--out", str(tmp_path / f"{arm}-corp"), "--mode", mode, "--dry-run"]) == 0
    capsys.readouterr()
    runs = [str(tmp_path / "C-corp"), str(tmp_path / "K-corp")]
    assert growth.main([*runs, "--block", "20", "--boot", "50", "--json", str(tmp_path / "g.json")]) == 0
    text = capsys.readouterr().out
    assert "POOLED C clf" in text and "(C docs found 60)" in text and "blocks of 20" in text and "fit over the blocks" in text
    saved = json.loads((tmp_path / "g.json").read_text(encoding="utf-8"))
    assert len(saved["runs"]["C-corp"]["blocks"]) == 3 and "C clf" in saved["pooled"]
    capsys.readouterr()
    assert growth.main([*runs, "--only", "K llm_dec,C clf", "--boot", "50"]) == 0
    only = capsys.readouterr().out
    assert "POOLED K llm_dec" in only and "POOLED C llm_dec" not in only
    with pytest.raises(SystemExit):
        growth.main([*runs, "--only", "Z clf"])
