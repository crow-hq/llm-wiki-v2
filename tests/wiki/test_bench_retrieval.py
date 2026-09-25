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

"""The retrieval benchmark: snapshots, wiki cuts, questions, asks and reports, on fake models."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest

from okf_wiki import Wiki, WikiConfig, bench, bench_retrieval
from okf_wiki.client import ModelError
from okf_wiki.store import WikiStore
from okf_wiki.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_bench import bench_args, fake_wiki_factory


class Reader(FakeLLM):
    """Writes questions, opens every folder, selects every note shown, and cites the first note it reads."""

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        self.calls.append((op, messages))
        self.usage.record("llm", self.model, 100, 10, op=op)
        prompt = messages[-1]["content"] if op != "researcher/answer" else messages[1]["content"]
        if op == "bench/question":
            return json.dumps({"question": "What does the note say?", "answer": "The facts."})
        if op == "researcher/navigate":
            notes = re.findall(r"^- (\S+\.md): ", prompt.split("Notes in", 1)[-1], re.M)
            subs = re.findall(r"^- ([a-z0-9-]+): ", prompt.split("Subfolders of", 1)[-1].split("Notes in", 1)[0], re.M)
            return json.dumps({"select": notes, "open": subs, "done": False})
        if op == "researcher/answer":
            first = re.search(r"^### (\S+\.md)$", prompt, re.M)
            return f"The facts [{first[1]}]." if first else "Nothing."
        raise AssertionError(f"unexpected op {op}")


def reader_factory(cfg: WikiConfig) -> Wiki:
    tracker = UsageTracker(cfg.usage_log)
    clf = FakeClassifier(tracker, nouls={"retrieve_folder": lambda q: 0.9, "retrieve_note": lambda q: 0.9})
    return Wiki(cfg, llm=Reader(tracker), classifier=clf if cfg.mode == "crow" else None)


def args(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(**({"source": None, "resume": None, "report": None, "like": None, "cuts": 2, "questions": 2, "workers": 2,
                                  "seed": 3, "out": None, "no_pdf": True} | kw))


@pytest.fixture
def ingested(tmp_path: Path) -> Path:
    """An ingestion benchmark of four files, both modes, everything filed at the root."""
    data = tmp_path / "bench-data"
    for i in range(4):
        (data / f"d{i}.md").parent.mkdir(parents=True, exist_ok=True)
        (data / f"d{i}.md").write_text(f"Document {i} says {i}.", encoding="utf-8")
    run = tmp_path / "ingest"
    bench.main(bench_args(data, run, seed=5), WikiConfig(bundle=run), make_wiki=fake_wiki_factory(4), echo=lambda _: None)
    return run


def test_completed_counts_the_prefix_done_in_every_mode() -> None:
    rows = {"classic": [{"index": i, "action": "created"} for i in (1, 2, 3, 5)],
            "crow": [{"index": 1, "action": "created"}, {"index": 2, "action": "error", "error_kind": "file"}, {"index": 3, "action": "merged"}]}
    assert bench_retrieval.completed(rows) == 3
    assert bench_retrieval.cut_points(300, 3) == [100, 200, 300]


def test_subset_keeps_the_notes_of_a_cut_and_drops_empty_folders(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="t")
    store.init()
    root = store.load()
    a, _ = store.create_folder(root, "alpha", "A.")
    b, _ = store.create_folder(root, "beta", "B.")
    n1 = store.write_note(a, title="One", summary="1.", body="x", tags=[], source={"resource": "/raw/1.md"})
    n2 = store.write_note(b, title="Two", summary="2.", body="x", tags=[], source={"resource": "/raw/2.md"})
    store.link(n1, n2)

    notes, folders = bench_retrieval.build_subset(tmp_path / "w", tmp_path / "cut", {n1.rel})

    cut = WikiStore(tmp_path / "cut", actor="t").load()
    assert (notes, folders) == (1, 1) and [f.name for f in cut.subfolders] == ["alpha"]
    assert "beta" not in (tmp_path / "cut" / "index.md").read_text(encoding="utf-8")
    assert "# See also" not in cut.all_notes()[0].body  # the link pointed to a note of the future
    assert (tmp_path / "w" / "beta").is_dir()  # the source is untouched


def test_targets_map_each_source_file_to_its_note(ingested: Path) -> None:
    gold = bench_retrieval.targets(ingested / "classic")
    assert set(gold) == {f"d{i}.md" for i in range(4)}
    assert all(len(notes) == 1 for notes in gold.values())


def test_full_run_snapshots_asks_every_question_at_every_cut_and_reports(ingested: Path, tmp_path: Path) -> None:
    out, lines = tmp_path / "retrieval", []

    code = bench_retrieval.main(args(source=str(ingested), out=str(out)), WikiConfig(bundle=out), make_wiki=reader_factory, echo=lines.append)

    assert code == 0
    rows = bench_retrieval.load_rows(out)
    assert len(rows) == 2 * 2 * 2  # cuts × questions × modes
    assert {r["cut"] for r in rows} == {2, 4} and {(r["cut"], r["notes"]) for r in rows} == {(2, 2), (4, 4)}
    assert all(r["hit"] for r in rows) and any(r["cited"] for r in rows)  # the fake reads every note, cites the first
    crow = [r for r in rows if r["mode"] == "crow"]
    assert all(r["classifier"]["calls"] > 0 for r in crow) and all(r["classifier"]["calls"] == 0 for r in rows if r["mode"] == "classic")
    assert len((out / "questions.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert (out / "snapshot" / "classic" / "index.md").is_file() and (out / "subsets" / "cut-0002" / "crow").is_dir()
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]
    assert summary["classic"]["hit_rate"] == 1.0 and [c["notes"] for c in summary["crow"]["by_cut"]] == [2, 4]
    assert "## By wiki size" in (out / "report.md").read_text(encoding="utf-8")
    assert len(list((ingested / "classic").glob("*.md"))) == 6  # 4 notes + index + log: the ingestion run is untouched


def test_resume_asks_only_what_is_missing_and_report_needs_no_models(ingested: Path, tmp_path: Path) -> None:
    out = tmp_path / "retrieval"
    bench_retrieval.main(args(source=str(ingested), out=str(out)), WikiConfig(bundle=out), make_wiki=reader_factory, echo=lambda _: None)
    lines = (out / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    (out / "rows.jsonl").write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")

    bench_retrieval.main(args(resume=str(out)), WikiConfig(bundle=out), make_wiki=reader_factory, echo=lambda _: None)
    assert len(bench_retrieval.load_rows(out)) == 8

    def no_models(cfg: WikiConfig) -> Wiki:
        raise AssertionError("--report must not ask")

    assert bench_retrieval.main(args(report=str(out)), WikiConfig(bundle=out), make_wiki=no_models, echo=lambda _: None) == 0


def test_an_outage_stops_the_retrieval_run(ingested: Path, tmp_path: Path) -> None:
    out, lines = tmp_path / "retrieval", []

    class Down(Reader):
        def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
            if op != "bench/question":
                raise ModelError("llm 502: no such host")
            return super().chat(messages, op=op)

    def factory(cfg: WikiConfig) -> Wiki:
        tracker = UsageTracker(cfg.usage_log)
        return Wiki(cfg, llm=Down(tracker), classifier=FakeClassifier(tracker) if cfg.mode == "crow" else None)

    bench_retrieval.main(args(source=str(ingested), out=str(out), workers=1, cuts=1, questions=4), WikiConfig(bundle=out), make_wiki=factory, echo=lines.append)

    assert any("stopping: 3 questions in a row failed" in line for line in lines)
    assert any("okf-wiki bench-retrieval --resume" in line for line in lines)


def test_retrieval_pdf(ingested: Path, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    out = tmp_path / "retrieval"
    bench_retrieval.main(args(source=str(ingested), out=str(out), no_pdf=False), WikiConfig(bundle=out), make_wiki=reader_factory, echo=lambda _: None)

    text = "\n".join(p.extract_text() for p in PdfReader(out / "report.pdf").pages)
    assert "retrieval benchmark: classic vs CROW" in text and "As the wiki grows" in text and "By wiki size" in text


def test_like_reuses_snapshot_sizes_and_questions_with_new_settings(ingested: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "r1", tmp_path / "r2"
    bench_retrieval.main(args(source=str(ingested), out=str(first)), WikiConfig(bundle=first), make_wiki=reader_factory, echo=lambda _: None)

    cfg = WikiConfig(bundle=second, crow={"tau_ret": 0.1, "k": 8, "beam": 6, "tau_fold": 0.08})
    assert bench_retrieval.main(args(like=str(first), out=str(second)), cfg, make_wiki=reader_factory, echo=lambda _: None) == 0

    meta = json.loads((second / "run.json").read_text(encoding="utf-8"))
    assert meta["cuts"] == json.loads((first / "run.json").read_text(encoding="utf-8"))["cuts"] and meta["like"] == str(first)
    assert (meta["crow"]["tau_ret"], meta["crow"]["k"]) == (0.1, 8)
    assert (second / "questions.jsonl").read_text() == (first / "questions.jsonl").read_text()
    assert len(bench_retrieval.load_rows(second)) == 8


def test_crow_rows_record_the_classifier_scores_of_the_right_note(ingested: Path, tmp_path: Path) -> None:
    out = tmp_path / "r"
    bench_retrieval.main(args(source=str(ingested), out=str(out)), WikiConfig(bundle=out), make_wiki=reader_factory, echo=lambda _: None)

    crow = [r for r in bench_retrieval.load_rows(out) if r["mode"] == "crow"]
    assert all(r["gold_note_score"] == 0.9 and r["gold_note_rank"] is not None for r in crow)
    assert all("gold_note_score" not in r for r in bench_retrieval.load_rows(out) if r["mode"] == "classic")


def test_classic_reads_at_most_max_notes_whatever_crow_k(tmp_path: Path) -> None:
    from tests.wiki.fakes import FakeLLM as _F

    cfg = WikiConfig(bundle=tmp_path / "w", max_notes=2, crow={"k": 8})
    tracker = UsageTracker()
    wiki = Wiki(cfg, llm=Reader(tracker))
    wiki.init()
    for i in range(4):
        wiki.store.write_note(wiki.store.load(), title=f"N{i}", summary="s", body="b", tags=[], source={"resource": "/raw/x.md"})
    assert len(wiki.ask("anything?").notes) == 2
    assert _F  # the fake module is importable alongside
