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

"""The benchmark: file collection, pricing, the growth fit, and a full classic-vs-CROW run on fakes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from okf_wiki import Wiki, WikiConfig, bench, cli
from okf_wiki.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"About {title}.", "tags": [], "body": f"# Overview\n\n{title}."}


def fake_wiki_factory(n: int) -> Any:
    """Wikis whose fakes answer `n` ingests: everything filed at the root, nothing matched or linked."""

    def make(cfg: WikiConfig) -> Wiki:
        tracker = UsageTracker(cfg.usage_log)
        llm = FakeLLM(tracker).add("librarian/summarize", *[draft(f"Doc {i}") for i in range(n)])
        if cfg.mode == "classic":
            llm.add("librarian/route", *[{"action": "here"}] * n)
            llm.add("librarian/find_match", *[{"match": None}] * n).add("librarian/relate", *[{"related": []}] * n)
            return Wiki(cfg, llm=llm)
        clf = FakeClassifier(tracker)
        for _ in range(n):
            clf.add_choice("route", "Here", {"Here": 0.9, "New subfolder": 0.1}, 0.9)
        return Wiki(cfg, llm=llm, classifier=clf)

    return make


@pytest.fixture
def data(tmp_path: Path) -> Path:
    root = tmp_path / "bench-data"
    for rel in ["a.md", "sub/b.md", "sub/deeper/c.md", ".hidden/x.md", "notes.txt"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"Text of {rel}.", encoding="utf-8")
    return root


def test_collect_finds_markdown_at_any_depth_and_skips_hidden(data: Path) -> None:
    assert [p.relative_to(data).as_posix() for p in bench.collect(data)] == ["a.md", "sub/b.md", "sub/deeper/c.md"]
    assert len(bench.collect(data, limit=2)) == 2


def test_fit_recovers_a_line_and_handles_flat_input() -> None:
    a, b, r2 = bench.fit([0, 1, 2, 3], [10, 12, 14, 16])
    assert (round(a, 6), round(b, 6), round(r2, 6)) == (10, 2, 1)
    assert bench.fit([5, 5], [1, 3]) == (2.0, 0.0, 0.0)


def _openrouter(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/endpoints"):
        return httpx.Response(200, json={"data": {"endpoints": [
            {"tag": "coreweave/fp8", "provider_name": "CoreWeave", "pricing": {"prompt": "0.0000002", "completion": "0.00000065"}},
            {"tag": "together", "provider_name": "Together", "pricing": {"prompt": "0.0000003", "completion": "0.0000012"}},
        ]}})
    return httpx.Response(200, json={"data": [{"id": "deepseek/deepseek-v4.1-flash", "pricing": {"prompt": "0.00000014", "completion": "0.00000042"}}]})


def test_llm_price_uses_the_pinned_provider_else_the_list_price(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(_openrouter))
    cfg = WikiConfig(bundle=tmp_path, llm={"model": "openrouter/deepseek/deepseek-v4.1-flash"})
    assert bench.llm_price(cfg, client) == (0.00000014, 0.00000042)
    pinned = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"extra_body": {"provider": {"order": ["together"]}}})})
    assert bench.llm_price(pinned, client) == (0.0000003, 0.0000012)
    assert bench.llm_price(WikiConfig(bundle=tmp_path, llm={"model": "gemini/gemini-2.5-flash"}), client) is None


def test_run_catalogues_the_same_files_into_both_wikis(data: Path, tmp_path: Path) -> None:
    files, out = bench.collect(data), tmp_path / "run"
    lines: list[str] = []

    results = bench.run(files, data, out, WikiConfig(bundle=out), ["classic", "crow"], make_wiki=fake_wiki_factory(3), echo=lines.append)

    for mode in ("classic", "crow"):
        rows = results[mode]
        assert [r["notes_before"] for r in rows] == [0, 1, 2]
        assert [r["action"] for r in rows] == ["created"] * 3
        assert len(list((out / mode).glob("doc-*.md"))) == 3
        assert (out / f"{mode}-calls.jsonl").is_file()
    assert all(r["classifier"]["calls"] == 0 for r in results["classic"])
    assert all(r["classifier"]["calls"] >= 1 and r["classifier"]["input_tokens"] > 0 for r in results["crow"])
    assert "llm:librarian/summarize" in results["crow"][0]["by_op"] and "classifier:route" in results["crow"][0]["by_op"]
    assert len((out / "rows.jsonl").read_text(encoding="utf-8").splitlines()) == 6
    progress = [line for line in lines if line.startswith("[")]
    assert len(progress) == 6 and "eta" in progress[0]
    assert lines[0] == f"… 1/3 {files[0].relative_to(data).as_posix()} → classic + crow"


def test_a_failing_file_becomes_an_error_row(data: Path, tmp_path: Path) -> None:
    (data / "a.md").write_text("   ", encoding="utf-8")
    results = bench.run(bench.collect(data), data, tmp_path / "run", WikiConfig(bundle=tmp_path), ["classic"],
                        make_wiki=fake_wiki_factory(2), echo=lambda _: None)
    rows = results["classic"]
    assert rows[0]["action"] == "error" and "empty" in rows[0]["error"]
    assert [r["action"] for r in rows[1:]] == ["created", "created"]


def test_reports_compare_modes_and_show_the_growth_curve(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    files = bench.collect(data)
    results = bench.run(files, data, out, WikiConfig(bundle=out), ["classic", "crow"], make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    meta = bench.params(WikiConfig(bundle=out), data, files, (1e-6, 2e-6), bench.JEV_PRICE)

    stats = bench.write_reports(out, meta, results, (1e-6, 2e-6), bench.JEV_PRICE)

    classic, crow = stats["classic"], stats["crow"]
    assert classic["created"] == 3 and classic["classifier_calls"] == 0 and crow["classifier_calls"] >= 3
    assert classic["llm_cost_list"] == pytest.approx(classic["llm_input_tokens"] * 1e-6 + classic["llm_output_tokens"] * 2e-6)
    assert crow["classifier_cost_list"] == pytest.approx(crow["classifier_input_tokens"] * 0.042e-6)
    assert set(classic["growth"]) == set(bench.SERIES) and "llm:librarian/summarize" in crow["growth_by_step"]
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## Growth with the size of the wiki" in report and "okf-wiki --bundle" in report and "api_key" not in report
    page = (out / "report.html").read_text(encoding="utf-8")
    assert page.count("<svg") >= 3 and "notes saved" in page
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]["crow"]["ingests"] == 3


def test_buckets_average_tokens_by_notes_already_saved() -> None:
    rows = [{"action": "created", "notes_before": i, "llm": {"input_tokens": 10 * i, "output_tokens": 0},
             "classifier": {"input_tokens": i, "output_tokens": 0}} for i in range(4)]
    assert bench.buckets(rows, n=2) == [("0–1", 5.0, 0.5, 2), ("2–3", 25.0, 2.5, 2)]


def test_cli_bench_runs_and_prints_how_to_open_the_wikis(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "make_bench_wiki", fake_wiki_factory(2))
    out = tmp_path / "run"

    code = cli.main(["bench", "--data", str(data), "--limit", "2", "--out", str(out), "--llm-price", "0.3,1.2"])

    printed = capsys.readouterr().out
    assert code == 0 and "open the crow wiki: okf-wiki --bundle" in printed and "cost at list price" in printed
    assert (out / "report.html").is_file()


def test_cli_bench_without_markdown_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["bench", "--data", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
    assert "no .md files" in capsys.readouterr().out


def test_bench_main_reads_prices_from_flags(data: Path, tmp_path: Path) -> None:
    args = argparse.Namespace(data=str(data), limit=1, out=str(tmp_path / "o"), modes="classic",
                              llm_price="1,2", classifier_price="0.5,0")
    assert bench.main(args, WikiConfig(bundle=tmp_path), make_wiki=fake_wiki_factory(1), echo=lambda _: None) == 0
    prices = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))["params"]["price_per_million"]
    assert prices == {"llm": [1.0, 2.0], "classifier": [0.5, 0.0]}


# -- stop, resume, report ----------------------------------------------------------------------


def bench_args(data: Path, out: Path | None, **kw: Any) -> argparse.Namespace:
    base = dict(data=str(data), limit=None, out=out and str(out), modes="classic,crow", llm_price="1,2",
                classifier_price=None, resume=None, report=None, seed=None, no_pdf=True)
    return argparse.Namespace(**(base | kw))


def test_stop_lets_the_current_notes_finish_and_keeps_them(data: Path, tmp_path: Path) -> None:
    stop = bench.threading.Event()
    out = tmp_path / "run"

    def echo(line: str) -> None:
        if line.startswith("["):
            stop.set()  # "Ctrl+C" right after the first note is filed

    results = bench.run(bench.collect(data), data, out, WikiConfig(bundle=out), ["classic"], make_wiki=fake_wiki_factory(3), echo=echo, stop=stop)

    assert len(results["classic"]) == 1
    assert bench.load_rows(out)["classic"][0]["file"] == "a.md"


def test_resume_continues_where_the_run_stopped(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    assert bench.main(bench_args(data, out, limit=1), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None) == 0
    lines: list[str] = []

    code = bench.main(bench_args(data, None, resume=str(out), limit=3), WikiConfig(bundle=out),
                      make_wiki=fake_wiki_factory(3), echo=lines.append)

    rows = bench.load_rows(out)
    seed = json.loads((out / "run.json").read_text(encoding="utf-8"))["seed"]
    order = [p.relative_to(data).as_posix() for p in bench.collect(data, None, seed)]
    assert code == 0 and lines[0].startswith(f"resuming: 3 files in random order (seed {seed}) · to do classic 2, crow 2")
    for mode in ("classic", "crow"):
        assert [r["file"] for r in rows[mode]] == order  # the same shuffled order across sessions
        assert [r["notes_before"] for r in rows[mode]] == [0, 1, 2]  # the same wiki kept growing
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]["crow"]["ingests"] == 3
    assert not any("--resume" in line for line in lines)


def test_an_unfinished_run_prints_how_to_resume(data: Path, tmp_path: Path) -> None:
    out, lines = tmp_path / "run", []
    (data / "sub" / "b.md").write_text("  ", encoding="utf-8")  # fails: empty

    bench.main(bench_args(data, out, modes="classic"), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lines.append)

    assert any(f"okf-wiki bench --resume {out}" in line for line in lines)


def test_resume_retries_files_that_failed(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    (data / "a.md").write_text("  ", encoding="utf-8")
    bench.main(bench_args(data, out, modes="classic"), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    (data / "a.md").write_text("Now it has text.", encoding="utf-8")

    bench.main(bench_args(data, out, resume=str(out)), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)

    rows = bench.load_rows(out)["classic"]
    assert sorted((r["file"], r["action"]) for r in rows) == [("a.md", "created"), ("sub/b.md", "created"), ("sub/deeper/c.md", "created")]


def test_resume_keeps_the_saved_settings(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    first = WikiConfig(bundle=out, llm={"model": "openrouter/first", "api_key": "secret"})
    bench.main(bench_args(data, out, limit=1, modes="classic"), first, make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    seen: list[WikiConfig] = []

    def make(cfg: WikiConfig) -> Wiki:
        seen.append(cfg)
        return fake_wiki_factory(3)(cfg)

    bench.main(bench_args(data, out, resume=str(out)), WikiConfig(bundle=out, llm={"model": "openrouter/other", "api_key": "k2"}),
               make_wiki=make, echo=lambda _: None)

    assert seen[0].llm.model == "openrouter/first" and seen[0].llm.api_key == "k2"  # saved model, current key
    assert "secret" not in (out / "run.json").read_text(encoding="utf-8")


def test_report_rebuilds_from_saved_data_without_models(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    bench.main(bench_args(data, out), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    (out / "report.md").unlink()
    (out / "report.html").unlink()

    def no_models(cfg: WikiConfig) -> Wiki:
        raise AssertionError("--report must not ingest")

    lines: list[str] = []
    assert bench.main(bench_args(data, out, report=str(out)), WikiConfig(bundle=out), make_wiki=no_models, echo=lines.append) == 0
    assert (out / "report.md").is_file() and (out / "report.html").is_file()
    assert any("cost at list price" in line for line in lines)


def test_resume_or_report_of_an_empty_folder_exits_2(tmp_path: Path) -> None:
    for kw in ({"resume": str(tmp_path)}, {"report": str(tmp_path)}):
        assert bench.main(bench_args(tmp_path, tmp_path, **kw), WikiConfig(bundle=tmp_path), echo=lambda _: None) == 2


def test_cli_resume_and_report_cannot_be_combined(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["bench", "--resume", str(tmp_path), "--report", str(tmp_path)])


def test_modes_run_in_lockstep_so_a_stop_leaves_them_even(data: Path, tmp_path: Path) -> None:
    stop, out, lines = bench.threading.Event(), tmp_path / "run", []

    def echo(line: str) -> None:
        lines.append(line)
        if line.startswith("["):
            stop.set()  # stop as soon as the first pair is reported

    results = bench.run(bench.collect(data), data, out, WikiConfig(bundle=out), ["classic", "crow"],
                        make_wiki=fake_wiki_factory(3), echo=echo, stop=stop)

    assert [len(results["classic"]), len(results["crow"])] == [1, 1]
    assert {r["file"] for r in results["classic"]} == {r["file"] for r in results["crow"]} == {"a.md"}


def test_the_two_modes_of_a_pair_run_at_the_same_time(data: Path, tmp_path: Path) -> None:
    barrier = bench.threading.Barrier(2, timeout=5)  # both ingests of a pair must be in flight together

    def make(cfg: WikiConfig) -> Wiki:
        wiki = fake_wiki_factory(3)(cfg)
        ingest_file = wiki.ingest_file

        def paired(*args: Any, **kwargs: Any) -> Any:
            barrier.wait()
            return ingest_file(*args, **kwargs)

        wiki.ingest_file = paired  # type: ignore[method-assign]
        return wiki

    results = bench.run(bench.collect(data), data, tmp_path / "run", WikiConfig(bundle=tmp_path), ["classic", "crow"],
                        make_wiki=make, echo=lambda _: None)

    assert [r["action"] for m in ("classic", "crow") for r in results[m]] == ["created"] * 6


def test_resume_first_realigns_a_mode_that_fell_behind(data: Path, tmp_path: Path) -> None:
    out, files = tmp_path / "run", bench.collect(data)
    bench.run(files[:1], data, out, WikiConfig(bundle=out), ["classic"], make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    lines: list[str] = []

    bench.run(files, data, out, WikiConfig(bundle=out), ["classic", "crow"], make_wiki=fake_wiki_factory(3),
              echo=lines.append, skip=bench.catalogued(bench.load_rows(out)))

    rows = bench.load_rows(out)
    assert [r["file"] for r in rows["crow"]] == ["a.md", "sub/b.md", "sub/deeper/c.md"]
    assert [r["file"] for r in rows["classic"]] == ["a.md", "sub/b.md", "sub/deeper/c.md"]
    assert lines[0].endswith("→ crow") and lines[1].startswith("[crow ") and "1/3" in lines[1]  # only crow needed a.md



def test_collect_with_a_seed_shuffles_repeatably(tmp_path: Path) -> None:
    for i in range(30):
        (tmp_path / f"f{i:02}" / "n.md").parent.mkdir()
        (tmp_path / f"f{i:02}" / "n.md").write_text("x", encoding="utf-8")
    alphabetical = bench.collect(tmp_path)

    shuffled = bench.collect(tmp_path, seed=7)

    assert shuffled != alphabetical and sorted(shuffled) == alphabetical
    assert bench.collect(tmp_path, seed=7) == shuffled
    assert bench.collect(tmp_path, limit=5, seed=7) == shuffled[:5]


def test_a_new_run_saves_its_seed_and_follows_it(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    bench.main(bench_args(data, out, seed=123, modes="classic"), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)

    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["seed"] == 123
    order = [p.relative_to(data).as_posix() for p in bench.collect(data, seed=123)]
    assert [r["file"] for r in bench.load_rows(out)["classic"]] == order


def test_runs_saved_before_seeds_resume_in_alphabetical_order(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    bench.main(bench_args(data, out, limit=1, modes="classic"), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    del run["seed"]
    (out / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (out / "rows.jsonl").unlink()
    lines: list[str] = []

    bench.main(bench_args(data, None, resume=str(out), limit=3), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lines.append)

    assert "alphabetical order" in lines[0]
    assert [r["file"] for r in bench.load_rows(out)["classic"]] == ["a.md", "sub/b.md", "sub/deeper/c.md"]


def test_pdf_report_is_built_from_the_saved_rows(data: Path, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    out = tmp_path / "run"
    bench.main(bench_args(data, out, seed=1), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    lines: list[str] = []

    code = bench.main(bench_args(data, out, report=str(out), no_pdf=False), WikiConfig(bundle=out), echo=lines.append)

    text = "\n".join(page.extract_text() for page in PdfReader(out / "report.pdf").pages)
    assert code == 0 and lines[-1] == f"pdf: {out / 'report.pdf'}"
    for heading in ("LLM wiki benchmark: classic vs CROW", "Findings", "How consumption grows", "Which steps grow", "Setup"):
        assert heading in text
    assert "random order (seed 1)" in text


def test_every_run_writes_the_pdf_by_default(data: Path, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("reportlab")
    out = tmp_path / "run"

    bench.main(bench_args(data, out, no_pdf=False), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)

    assert (out / "report.pdf").read_bytes().startswith(b"%PDF")


def test_token_series_never_add_llm_and_classifier_tokens() -> None:
    row = {"llm": {"input_tokens": 10, "output_tokens": 5}, "classifier": {"input_tokens": 1000, "output_tokens": 7}}
    assert bench.SERIES["LLM tokens (in + out)"](row) == 15
    assert "total tokens" not in bench.SERIES


def test_pdf_without_the_extra_says_how_to_install_it(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    out = tmp_path / "run"
    bench.main(bench_args(data, out, modes="classic"), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)
    real_import = builtins.__import__

    def no_pdf(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "okf_wiki.bench_pdf":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pdf)
    lines: list[str] = []
    assert bench.main(bench_args(data, out, report=str(out), no_pdf=False), WikiConfig(bundle=out), echo=lines.append) == 0
    assert 'pip install "llm-wiki-v2[pdf]"' in lines[-1]


# -- outages -----------------------------------------------------------------------------------------


def broken_wiki_factory(cfg: WikiConfig) -> Wiki:
    """Every LLM call fails, like an endpoint that lost DNS."""
    from okf_wiki.client import ModelError

    class Down(FakeLLM):
        def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
            raise ModelError("llm 502: lookup openrouter.ai: no such host")

    tracker = UsageTracker(cfg.usage_log)
    return Wiki(cfg, llm=Down(tracker), classifier=FakeClassifier(tracker) if cfg.mode == "crow" else None)


@pytest.fixture
def many(tmp_path: Path) -> Path:
    root = tmp_path / "many"
    root.mkdir()
    for i in range(6):
        (root / f"n{i}.md").write_text(f"Note {i}.", encoding="utf-8")
    return root


def test_a_model_outage_stops_the_run_after_three_files(many: Path, tmp_path: Path) -> None:
    out, lines = tmp_path / "run", []

    results = bench.run(bench.collect(many), many, out, WikiConfig(bundle=out), ["classic", "crow"], make_wiki=broken_wiki_factory, echo=lines.append)

    assert [len(results["classic"]), len(results["crow"])] == [3, 3]
    assert all(r["error_kind"] == "model" for m in results for r in results[m])
    assert any("stopping: a model call failed on 3 files in a row" in line and f"--resume {out}" in line for line in lines)


def test_files_that_cannot_be_read_do_not_stop_the_run(many: Path, tmp_path: Path) -> None:
    for i in range(4):
        (many / f"n{i}.md").write_text("   ", encoding="utf-8")  # empty: a file problem, not an outage

    results = bench.run(bench.collect(many), many, tmp_path / "run", WikiConfig(bundle=tmp_path), ["classic"],
                        make_wiki=fake_wiki_factory(6), echo=lambda _: None)

    assert len(results["classic"]) == 6
    assert sum(r.get("error_kind") == "file" for r in results["classic"]) == 4


def test_failed_attempts_are_kept_and_counted_after_a_resume(many: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    bench.main(bench_args(many, out, limit=2, modes="classic"), WikiConfig(bundle=out), make_wiki=broken_wiki_factory, echo=lambda _: None)

    bench.main(bench_args(many, None, resume=str(out)), WikiConfig(bundle=out), make_wiki=fake_wiki_factory(3), echo=lambda _: None)

    assert len((out / "rows.jsonl").read_text(encoding="utf-8").splitlines()) == 4  # 2 failures + 2 retries, nothing lost
    assert [len(v) for v in bench.retried(out).values()] == [2]
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]["classic"]
    assert (summary["ingests"], summary["errors"], summary["retried_attempts"]) == (2, 0, 2)
    assert "failed attempts retried later" in (out / "report.md").read_text(encoding="utf-8")
