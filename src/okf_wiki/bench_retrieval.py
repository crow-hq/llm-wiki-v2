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

"""Retrieval benchmark: how answering questions scales with the size of the wiki, classic vs CROW.

    okf-wiki bench-retrieval --from bench-runs/<ingestion run> [--cuts 6] [--questions 20]
    okf-wiki bench-retrieval --resume bench-runs/retrieval-<stamp>
    okf-wiki bench-retrieval --report bench-runs/retrieval-<stamp>

1. Snapshot: the two wikis of an ingestion run are copied, so that run can keep going.
2. Cuts: at growing points of the ingestion order each wiki is rebuilt as it stood then:
   only the notes created up to that ingest, empty folders dropped, indexes regenerated.
3. Questions: the LLM writes one question per sampled source document that both wikis hold
   from the first cut on, so every question is answerable at every size, and we know
   which note should be found.
4. Asks: every question at every cut, in both modes. Rows record latency, tokens and cost
   per ledger (LLM, classifier) and per step, the notes read, and whether the right note
   was read (hit) and cited.
"""

from __future__ import annotations

import json
import posixpath
import random
import shutil
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from okf_wiki.document import OKFDocument

from okf_wiki import bench
from okf_wiki.client import ModelError
from okf_wiki.config import WikiConfig
from okf_wiki.llm import LLM
from okf_wiki.prompts import Prompts
from okf_wiki.researcher import NO_ANSWER
from okf_wiki.files import extract_text
from okf_wiki.store import _ENTRY, WikiStore, join_see_also, note_dir, split_see_also
from okf_wiki.usage import Usage, UsageReport, UsageTracker
from okf_wiki.wiki import Wiki

MODES = ["classic", "crow"]


class Question(BaseModel):
    question: str
    answer: str


# -- 1. snapshot of an ingestion run ------------------------------------------------------------------


def snapshot(ingest: Path, out: Path) -> dict[str, list[dict[str, Any]]]:
    """Copy an ingestion run's wikis and rows into out/snapshot. Rows are read first: a note written
    after that moment is simply not part of any cut."""
    rows = bench.load_rows(ingest)
    snap = out / "snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    for mode in rows:
        shutil.copytree(ingest / mode, snap / mode, ignore=shutil.ignore_patterns(".*.tmp"))
    shutil.copy(ingest / "run.json", snap / "run.json")
    (snap / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for m in rows for r in rows[m]), encoding="utf-8")
    return rows


def completed(rows: dict[str, list[dict[str, Any]]]) -> int:
    """Largest k such that every file 1..k was ingested (or rejected as unreadable) in every mode."""
    done = [
        {r["index"] for r in rs if r["action"] in ("created", "merged") or r.get("error_kind") == "file"}
        for rs in rows.values()
    ]
    k = 0
    while all(k + 1 in d for d in done):
        k += 1
    return k


def cut_points(max_cut: int, n: int) -> list[int]:
    return sorted({max(1, round(max_cut * (j + 1) / n)) for j in range(n)})


def created_at(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Note path → the ingest that created it."""
    first: dict[str, int] = {}
    for r in rows:
        if r["action"] == "created":
            first[r["note"]] = min(r["index"], first.get(r["note"], r["index"]))
    return first


# -- 2. the wiki as it stood at a cut --------------------------------------------------------------------


def build_subset(src: Path, dest: Path, keep: set[str]) -> tuple[int, int]:
    """Copy `src` to `dest` keeping only the notes in `keep`; returns (notes, folders)."""
    shutil.copytree(src, dest)
    store = WikiStore(dest, actor="bench")
    root = store.load()
    for note in root.all_notes():
        if note.rel not in keep:
            note.path.unlink()
    for folder in reversed(list(root.walk())):  # deepest first, so emptiness propagates up
        folder.notes = [n for n in folder.notes if n.rel in keep]
        for sub in [s for s in folder.subfolders if not s.all_notes()]:
            shutil.rmtree(sub.path)
        folder.subfolders = [s for s in folder.subfolders if s.all_notes()]
    kept = {n.rel for n in root.all_notes()}
    for folder in root.walk():
        store.write_index(folder)
        for note in folder.notes:  # See-also links to notes that did not exist yet are dropped
            main, entries = split_see_also(note.body)
            live = [e for e in entries if _link_target(note.rel, e) in kept]
            if live != entries:
                note.body = join_see_also(main, live)
                store._save(note)
    return len(kept), sum(1 for _ in root.walk()) - 1


def _link_target(rel: str, entry: str) -> str | None:
    m = _ENTRY.match(entry.strip())
    return posixpath.normpath(posixpath.join(note_dir(rel), m["link"])) if m else None


def targets(wiki_root: Path) -> dict[str, set[str]]:
    """Source file (as the ingestion run named it) → notes written from it."""
    raw_to_file: dict[str, str] = {}
    for raw in (wiki_root / "raw").glob("*.md"):
        resource = OKFDocument.parse(raw.read_text(encoding="utf-8")).frontmatter.get("resource")
        if resource:
            raw_to_file[f"/raw/{raw.name}"] = str(resource)
    out: dict[str, set[str]] = {}
    for note in WikiStore(wiki_root, actor="bench").load().all_notes():
        for source in note.frontmatter.get("sources") or []:
            if isinstance(source, dict) and (file := raw_to_file.get(str(source.get("resource")))):
                out.setdefault(file, set()).add(note.rel)
    return out


# -- 3. questions -----------------------------------------------------------------------------------------


def make_questions(
    rows: dict[str, list[dict[str, Any]]], data: Path, first_cut: int, n: int, seed: int, llm: LLM, prompts: Prompts,
    echo: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """One question per sampled document that every mode filed within the first cut."""
    per_mode = [{r["file"] for r in rs if r["action"] in ("created", "merged") and r["index"] <= first_cut} for rs in rows.values()]
    files = sorted(set.intersection(*per_mode)) if per_mode else []
    random.Random(seed).shuffle(files)
    system = prompts.render("bench/system")
    questions: list[dict[str, Any]] = []
    for file in files:
        if len(questions) >= n:
            break
        path = data / file
        if not path.is_file():
            continue
        try:
            text = extract_text(path.read_bytes(), path.name)
            q = llm.json(system, prompts.render("bench/question", source_text=text[:20_000]), Question, op="bench/question")
        except (ModelError, ValueError) as e:
            echo(f"  skipped {file}: {str(e)[:100]}")
            continue
        questions.append({"id": f"q{len(questions) + 1:02}", "file": file, "question": q.question.strip(), "answer": q.answer.strip()})
        echo(f"  {questions[-1]['id']} {q.question.strip()[:110]}")
    return questions


# -- 4. asking ----------------------------------------------------------------------------------------------


def _usage(u: Usage) -> dict[str, float]:
    return {k: getattr(u, k) for k in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd", "cached_tokens")}


def list_cost(r: dict[str, Any], prices: dict[str, Any]) -> float:
    """What a question would cost at list price, with no prompt-cache discount: tokens × published price."""
    llm, clf = prices.get("llm") or (0.0, 0.0), prices.get("classifier") or bench.JEV_PRICE
    return (r["llm"]["input_tokens"] * llm[0] + r["llm"]["output_tokens"] * llm[1]
            + r["classifier"]["input_tokens"] * clf[0] + r["classifier"]["output_tokens"] * clf[1])


def llm_list_cost(r: dict[str, Any], prices: dict[str, Any]) -> float:
    llm = prices.get("llm") or (0.0, 0.0)
    return r["llm"]["input_tokens"] * llm[0] + r["llm"]["output_tokens"] * llm[1]


def ask_row(wiki: Wiki, mode: str, cut: int, size: tuple[int, int], q: dict[str, Any], gold: set[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"mode": mode, "cut": cut, "notes": size[0], "folders": size[1], "qid": q["id"], "file": q["file"]}
    start = time.perf_counter()
    try:
        a = wiki.ask(q["question"])
        usage: UsageReport = a.usage
        row.update(
            action="no_answer" if a.text == NO_ANSWER else "answered",
            notes_read=a.notes,
            citations=a.citations,
            hit=bool(gold & set(a.notes)),
            cited=bool(gold & set(a.citations)),
            fallback=any(d.fallback for d in a.decisions),
            answer=a.text[:1500],
            **_gold_scores(a.decisions, gold),
        )
    except ModelError as e:
        usage = UsageReport()
        row.update(action="error", error_kind="model", error=str(e)[:300], notes_read=[], citations=[], hit=False, cited=False, fallback=False)
    row["seconds"] = round(time.perf_counter() - start, 3)
    row.update(llm=_usage(usage.llm), classifier=_usage(usage.classifier), by_op={k: _usage(v) for k, v in usage.by_op.items()})
    return row


def _gold_scores(decisions: list[Any], gold: set[str]) -> dict[str, Any]:
    """CROW only: the classifier's probability for the right note (and its rank) and for its folders."""
    notes = next((d.scores for d in decisions if d.step == "retrieve_note" and d.scores), {})
    folders = {k: v for d in decisions if d.step == "retrieve_folder" for k, v in d.scores.items()}
    if not notes and not folders:
        return {}
    ranked = sorted(notes, key=notes.get, reverse=True)
    lineage = {"/" + "/".join(g.split("/")[: i + 1]) for g in gold for i in range(g.count("/"))}
    return {
        "gold_note_score": max((notes[g] for g in gold if g in notes), default=None),
        "gold_note_rank": next((i + 1 for i, n in enumerate(ranked) if n in gold), None),
        "best_other_score": max((p for n, p in notes.items() if n not in gold), default=None),
        "gold_folder_scores": {f: folders[f] for f in sorted(lineage) if f in folders},
    }


def run(
    out: Path,
    cfg: WikiConfig,
    cuts: list[int],
    questions: list[dict[str, Any]],
    gold: dict[str, dict[str, set[str]]],
    created: dict[str, dict[str, int]],
    modes: list[str],
    *,
    workers: int = 4,
    make_wiki: Callable[[WikiConfig], Wiki] = Wiki,
    echo: Callable[[str], None] = print,
    stop: threading.Event | None = None,
    max_failures: int = 3,
) -> None:
    """Every question at every cut in every mode; rows are appended to out/rows.jsonl as they finish."""
    stop = stop or threading.Event()
    done = {(r["cut"], r["qid"], r["mode"]) for r in load_rows(out) if r["action"] != "error"}
    lock = threading.Lock()

    def loop() -> None:
        failures, total = 0, len(cuts) * len(questions) * len(modes)
        count = len(done)
        for cut in cuts:
            if stop.is_set():
                return
            wikis, sizes = {}, {}
            for mode in modes:
                dest = out / "subsets" / f"cut-{cut:04}" / mode
                keep = {note for note, i in created[mode].items() if i <= cut}
                if not dest.exists():
                    build_subset(out / "snapshot" / mode, dest, keep)
                root = WikiStore(dest, actor="bench").load()
                sizes[mode] = (len(root.all_notes()), sum(1 for _ in root.walk()) - 1)
                wikis[mode] = make_wiki(cfg.model_copy(update={"bundle": dest, "mode": mode, "usage_log": out / f"{mode}-calls.jsonl"}))
            echo(f"… cut {cut}: " + " · ".join(f"{m} {sizes[m][0]} notes / {sizes[m][1]} folders" for m in modes))
            tasks = [(q, m) for q in questions for m in modes if (cut, q["id"], m) not in done]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(ask_row, wikis[m], m, cut, sizes[m], q, gold[m].get(q["file"], set())): (q, m) for q, m in tasks}
                for future in as_completed(futures):
                    if future.cancelled():
                        continue
                    row = future.result()
                    with lock:
                        count += 1
                        with (out / "rows.jsonl").open("a", encoding="utf-8") as f:
                            f.write(json.dumps(row) + "\n")
                        echo(_progress(row, count, total))
                        failures = failures + 1 if row["action"] == "error" else 0
                        if failures >= max_failures and not stop.is_set():
                            stop.set()
                            echo(f"\nstopping: {failures} questions in a row failed ({row.get('error', '')[:160]})."
                                 f"\nFix it, then continue with: okf-wiki bench-retrieval --resume {out}")
                    if stop.is_set():  # questions not started are dropped; those in flight finish and are saved
                        for other in futures:
                            other.cancel()
            if stop.is_set():
                return

    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    try:
        bench._join([worker])
    except KeyboardInterrupt:
        stop.set()
        echo("\nstopping after the questions being asked now… (Ctrl+C again to quit at once)")
        try:
            bench._join([worker])
        except KeyboardInterrupt:
            echo("quit: the questions being asked right now were not saved")


def _progress(r: dict[str, Any], count: int, total: int) -> str:
    what = r["action"] if r["action"] == "error" else ("hit " if r["hit"] else "miss") + (" +fallback" if r["fallback"] else "")
    return (
        f"[{r['mode']:7} cut {r['cut']:>4} {r['qid']}] {r['notes']:>4} notes │ {what:15} {r['seconds']:6.1f}s │ "
        f"llm {r['llm']['calls']:.0f}× {bench._k(r['llm']['input_tokens'] + r['llm']['output_tokens'])} tok │ "
        f"clf {r['classifier']['calls']:.0f}× {bench._k(r['classifier']['input_tokens'] + r['classifier']['output_tokens'])} tok │ {count}/{total}"
    )


def load_rows(out: Path) -> list[dict[str, Any]]:
    """Saved rows; when a question was asked again (after an error), the last row wins."""
    path = out / "rows.jsonl"
    latest: dict[tuple[int, str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if line.strip():
            r = json.loads(line)
            latest[(r["cut"], r["qid"], r["mode"])] = r
    return list(latest.values())


# -- analysis ------------------------------------------------------------------------------------------------


SERIES: dict[str, Callable[[dict[str, Any]], float]] = {
    "LLM input tokens": lambda r: r["llm"]["input_tokens"],
    "LLM output tokens": lambda r: r["llm"]["output_tokens"],
    "classifier input tokens": lambda r: r["classifier"]["input_tokens"],
    "latency (s)": lambda r: r["seconds"],
    "cost (USD)": lambda r: r["llm"]["cost_usd"] + r["classifier"]["cost_usd"],
}


def summarize(rows: list[dict[str, Any]], prices: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-mode statistics. With `prices`, costs are also given at list price (no prompt-cache discount),
    which is the fair comparison when the same questions are repeated across sizes (see the report)."""
    prices = prices or {}
    ok = [r for r in rows if r["action"] != "error"]
    n = max(len(ok), 1)

    def discount(g: list[dict[str, Any]]) -> float | None:
        listed = sum(llm_list_cost(r, prices) for r in g)
        return 1 - sum(r["llm"]["cost_usd"] for r in g) / listed if listed and prices.get("llm") else None

    def total(ledger: str, key: str) -> float:
        return sum(r[ledger][key] for r in rows)

    by_cut = []
    for cut in sorted({r["cut"] for r in ok}):
        g = [r for r in ok if r["cut"] == cut]
        by_cut.append({
            "cut": cut, "notes": g[0]["notes"], "folders": g[0]["folders"], "questions": len(g),
            "hit_rate": sum(r["hit"] for r in g) / len(g), "cited_rate": sum(r["cited"] for r in g) / len(g),
            "llm_tokens": statistics.mean(r["llm"]["input_tokens"] + r["llm"]["output_tokens"] for r in g),
            "classifier_tokens": statistics.mean(r["classifier"]["input_tokens"] + r["classifier"]["output_tokens"] for r in g),
            "latency": statistics.mean(r["seconds"] for r in g),
            "cost": statistics.mean(r["llm"]["cost_usd"] + r["classifier"]["cost_usd"] for r in g),
            "list_cost": statistics.mean(list_cost(r, prices) for r in g),
            "cache_discount": discount(g),
            "fallback_rate": sum(r["fallback"] for r in g) / len(g),
        })
    lat = sorted(r["seconds"] for r in ok)
    steps = sorted({k for r in ok for k in r["by_op"]})
    xs = [r["notes"] for r in ok]
    return {
        "asks": len(rows), "errors": len(rows) - len(ok),
        "hit_rate": sum(r["hit"] for r in ok) / n, "cited_rate": sum(r["cited"] for r in ok) / n,
        "answered_rate": sum(r["action"] == "answered" for r in ok) / n, "fallback_rate": sum(r["fallback"] for r in ok) / n,
        "latency_mean": statistics.mean(lat) if lat else 0.0, "latency_p50": statistics.median(lat) if lat else 0.0,
        "latency_p95": bench._pct(lat, 0.95),
        **{f"{ledger}_{key}": total(ledger, key) for ledger in ("llm", "classifier") for key in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd")},
        "list_cost_usd": sum(list_cost(r, prices) for r in ok),
        "cache_discount": discount(ok),
        "llm_cached_tokens": sum(r["llm"].get("cached_tokens", 0) for r in ok),
        "by_cut": by_cut,
        "growth": {name: bench.fit(xs, [f(r) for r in ok]) for name, f in SERIES.items()}
        | {"list cost (USD)": bench.fit(xs, [list_cost(r, prices) for r in ok])},
        "growth_by_step": {s: bench.fit(xs, [r["by_op"].get(s, {}).get("input_tokens", 0) + r["by_op"].get(s, {}).get("output_tokens", 0) for r in ok]) for s in steps},
    }


def _table_rows(stats: dict[str, dict[str, Any]]) -> list[tuple[str, list[str]]]:
    def row(label: str, f: Callable[[dict[str, Any]], str]) -> tuple[str, list[str]]:
        return label, [f(stats[m]) for m in stats]

    per = lambda s, v: v / max(s["asks"] - s["errors"], 1)  # noqa: E731
    return [
        row("questions asked (errors)", lambda s: f"{s['asks']} ({s['errors']})"),
        row("right note read (hit rate)", lambda s: f"{s['hit_rate']:.0%}"),
        row("right note cited", lambda s: f"{s['cited_rate']:.0%}"),
        row("answered (not 'no relevant notes')", lambda s: f"{s['answered_rate']:.0%}"),
        row("fell back to LLM navigation", lambda s: f"{s['fallback_rate']:.0%}"),
        row("latency per question: mean · p50 · p95", lambda s: f"{s['latency_mean']:.1f}s · {s['latency_p50']:.1f}s · {s['latency_p95']:.1f}s"),
        row("LLM calls per question", lambda s: f"{per(s, s['llm_calls']):.1f}"),
        row("LLM tokens per question in / out", lambda s: f"{per(s, s['llm_input_tokens']):,.0f} / {per(s, s['llm_output_tokens']):,.0f}"),
        row("classifier calls per question", lambda s: f"{per(s, s['classifier_calls']):.1f}"),
        row("classifier tokens per question in", lambda s: f"{per(s, s['classifier_input_tokens']):,.0f}"),
        row("cost per question at list price (no cache)", lambda s: f"${per(s, s.get('list_cost_usd', 0)):.4f}"),
        row("cost per question billed (after prompt cache)", lambda s: f"${per(s, s['llm_cost_usd'] + s['classifier_cost_usd']):.4f}"),
        row("prompt-cache discount on LLM cost", lambda s: "n/a" if s.get("cache_discount") is None else f"{s['cache_discount']:.0%}"),
        row("cost billed, total: LLM · classifier", lambda s: f"${s['llm_cost_usd']:.4f} · ${s['classifier_cost_usd']:.4f}"),
    ]


def write_reports(out: Path, meta: dict[str, Any], rows: list[dict[str, Any]], echo: Callable[[str], None] = print, pdf: bool = True) -> dict[str, Any]:
    prices = meta.get("prices") or {}
    stats = {m: summarize([r for r in rows if r["mode"] == m], prices) for m in meta["modes"] if any(r["mode"] == m for r in rows)}
    (out / "results.json").write_text(json.dumps({"params": meta, "summary": stats}, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(_markdown(meta, stats), encoding="utf-8")
    (out / "report.html").write_text(_html(meta, stats, rows), encoding="utf-8")
    if pdf:
        try:
            from okf_wiki.bench_retrieval_pdf import write_pdf

            echo(f"pdf: {write_pdf(out, meta, stats, rows)}")
        except ImportError:
            echo('no PDF: it needs the pdf extra (pip install "llm-wiki-v2[pdf]")')
    return stats


def _markdown(meta: dict[str, Any], stats: dict[str, dict[str, Any]]) -> str:
    modes = list(stats)
    lines = [f"# Retrieval benchmark {meta['started']}", "",
             f"{meta['questions']} questions asked at {len(meta['cuts'])} wiki sizes (cuts of the ingestion order: {', '.join(map(str, meta['cuts']))}), "
             f"from the wikis of `{meta['from']}`.", ""]
    lines += ["## Results", "", "| | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    lines += [f"| {label} | " + " | ".join(v) + " |" for label, v in _table_rows(stats)]
    lines += ["", "## By wiki size", ""]
    for m in modes:
        lines += [f"### {m}", "", "| notes | folders | hit | cited | LLM tok | classifier tok | latency | cost list | cost billed | cache discount | fallback |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {c['notes']} | {c['folders']} | {c['hit_rate']:.0%} | {c['cited_rate']:.0%} | {c['llm_tokens']:,.0f} | {c['classifier_tokens']:,.0f} | "
                  f"{c['latency']:.1f}s | ${c.get('list_cost', 0):.4f} | ${c['cost']:.4f} | {_pct_or_na(c.get('cache_discount'))} | {c['fallback_rate']:.0%} |"
                  for c in stats[m]["by_cut"]]
        lines += [""]
    lines += ["## Growth with the size of the wiki", "", "Linear fit of each question against the notes in the wiki: start · slope per note · r².", ""]
    lines += ["| series | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    lines += [f"| {name} | " + " | ".join(bench._fit_cell(stats[m]["growth"][name]) for m in modes) + " |" for name in SERIES]
    for m in modes:
        lines += ["", f"### {m}: per step (tokens in + out)", "", "| step | start · slope · r² |", "|---|---|"]
        lines += [f"| {s} | {bench._fit_cell(g)} |" for s, g in stats[m]["growth_by_step"].items()]
    lines += ["", "## Prompt caching", "", CACHE_NOTE, ""]
    lines += ["", "## Setup", "", "```json", json.dumps({k: meta[k] for k in ("llm", "classifier", "crow")}, indent=2), "```", ""]
    return "\n".join(lines)


CACHE_NOTE = (
    "The provider caches repeated prompt prefixes and bills them at a discount. This benchmark asks the same questions at "
    "every wiki size, so an answer prompt that selects the same notes repeats identically from one size to the next and is "
    "served from cache: billed cost then understates what a new question would cost, most of all at small sizes and for "
    "short, stable prompts (CROW's answer call). Token counts are not affected. Costs at list price (tokens × published "
    "price, no cache) are the fair comparison; billed costs and the cache discount are shown next to them."
)


def _pct_or_na(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def _html(meta: dict[str, Any], stats: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    import html as h

    modes = list(stats)

    def series(f: Callable[[dict[str, Any]], float], only: list[str] | None = None) -> dict[str, tuple[list[float], list[float], str]]:
        out = {}
        for m in only or modes:
            ok = [r for r in rows if r["mode"] == m and r["action"] != "error"]
            if ok:
                out[m] = ([r["notes"] for r in ok], [f(r) for r in ok], bench.COLORS.get(m, "#555"))
        return out

    charts = [bench._svg("LLM input tokens per question", series(SERIES["LLM input tokens"]), "tok"),
              bench._svg("Classifier input tokens per question", series(SERIES["classifier input tokens"], [m for m in modes if m == "crow"]), "tok"),
              bench._svg("Latency per question (seconds)", series(SERIES["latency (s)"]), "s")]
    table = "".join(f"<tr><th>{h.escape(label)}</th>" + "".join(f"<td>{h.escape(v)}</td>" for v in vals) + "</tr>" for label, vals in _table_rows(stats))
    heads = "".join(f"<th>{m}</th>" for m in modes)
    return (f"<!doctype html><meta charset='utf-8'><title>retrieval benchmark</title><style>body{{font:15px Georgia,serif;background:#f5efe3;max-width:980px;margin:40px auto;padding:0 24px}}"
            f"table{{border-collapse:collapse;width:100%;font:13px ui-monospace,monospace}}th,td{{border-bottom:1px solid #e2d9c7;padding:6px 10px;text-align:left}}"
            f"svg{{width:100%;background:#fbf8f1;border:1px solid #e2d9c7;margin:10px 0}}.t{{font:600 13px Georgia}}.n{{font:11px monospace;fill:#756d60}}.ax{{stroke:#bdb3a0}}</style>"
            f"<h1>Retrieval benchmark · {h.escape(meta['started'])}</h1><table><tr><th></th>{heads}</tr>{table}</table>"
            f"<h2>Growth with the size of the wiki</h2><p>Each dot is one question, placed by the notes in the wiki.</p>{''.join(charts)}")


# -- entry point ----------------------------------------------------------------------------------------------


def main(args: Any, cfg: WikiConfig, *, make_wiki: Callable[[WikiConfig], Wiki] = Wiki, echo: Callable[[str], None] = print) -> int:
    pdf = not getattr(args, "no_pdf", False)
    if getattr(args, "report", None):
        out = Path(args.report)
        meta = json.loads((out / "run.json").read_text(encoding="utf-8")) if (out / "run.json").is_file() else None
        if meta is None:
            echo(f"no retrieval benchmark in {out}")
            return 2
        _ensure_prices(out, meta, echo)
        write_reports(out, meta, load_rows(out), echo, pdf)
        _print(out, meta, echo)
        return 0
    if getattr(args, "resume", None):
        out = Path(args.resume)
        if not (out / "run.json").is_file():
            echo(f"nothing to resume in {out} (no run.json)")
            return 2
        meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
        cfg = WikiConfig.model_validate({**meta["config"], "bundle": out, "llm": {**meta["config"]["llm"], "api_key": cfg.llm.api_key},
                                         "classifier": {**meta["config"]["classifier"], "api_key": cfg.classifier.api_key}})
        saved = [json.loads(line) for line in (out / "snapshot" / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = {m: [r for r in saved if r["mode"] == m] for m in meta["modes"]}
    elif getattr(args, "like", None):
        prior = Path(args.like)
        if not (prior / "run.json").is_file():
            echo(f"{prior} is not a retrieval benchmark (no run.json)")
            return 2
        before = json.loads((prior / "run.json").read_text(encoding="utf-8"))
        out = Path(args.out or f"bench-runs/retrieval-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        out.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prior / "snapshot", out / "snapshot", dirs_exist_ok=True)
        shutil.copy(prior / "questions.jsonl", out / "questions.jsonl")
        meta = {**before, **_meta(cfg), "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "like": str(prior), "workers": args.workers}
        (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        saved = [json.loads(line) for line in (out / "snapshot" / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = {m: [r for r in saved if r["mode"] == m] for m in meta["modes"]}
        echo(f"same snapshot, sizes {meta['cuts']} and {meta['questions']} questions as {prior}; thresholds from the current settings")
    else:
        ingest = Path(getattr(args, "source"))
        if not (ingest / "run.json").is_file():
            echo(f"{ingest} is not an ingestion benchmark (no run.json)")
            return 2
        out = Path(args.out or f"bench-runs/retrieval-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        echo(f"snapshot of {ingest} → {out / 'snapshot'} (the ingestion run is not touched)")
        rows = snapshot(ingest, out)
        modes = [m for m in MODES if m in rows]
        max_cut = completed(rows)
        if max_cut < 2 or not modes:
            echo(f"the ingestion run has too few files in every mode ({max_cut})")
            return 2
        cuts = cut_points(max_cut, args.cuts)
        seed = args.seed if args.seed is not None else random.randrange(1_000_000)
        ingest_run = json.loads((ingest / "run.json").read_text(encoding="utf-8"))
        echo(f"cuts of the ingestion order: {cuts} (all {max_cut} files done in {', '.join(modes)}) · writing {args.questions} questions")
        llm = make_wiki(cfg.model_copy(update={"bundle": out / "snapshot" / modes[0], "mode": "classic", "usage_log": out / "questions-calls.jsonl"})).llm
        questions = make_questions(rows, Path(ingest_run["data"]), cuts[0], args.questions, seed, llm, Prompts(cfg.prompts_dir), echo)
        if not questions:
            echo("no question could be written")
            return 2
        (out / "questions.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions), encoding="utf-8")
        meta = {
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "from": str(ingest), "modes": modes, "cuts": cuts,
            "questions": len(questions), "seed": seed, "workers": args.workers, **_meta(cfg),
        }
        (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        rows = {m: rows[m] for m in modes}
    _ensure_prices(out, meta, echo)
    questions = [json.loads(line) for line in (out / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {m: targets(out / "snapshot" / m) for m in meta["modes"]}
    created = {m: created_at(rows[m]) for m in meta["modes"]}
    echo(f"asking {len(questions)} questions × {len(meta['cuts'])} sizes × {len(meta['modes'])} modes → {out}")
    run(out, cfg, meta["cuts"], questions, gold, created, meta["modes"], workers=meta.get("workers", 4), make_wiki=make_wiki, echo=echo)
    write_reports(out, meta, load_rows(out), echo, pdf)
    _print(out, meta, echo)
    missing = len(meta["cuts"]) * len(questions) * len(meta["modes"]) - sum(r["action"] != "error" for r in load_rows(out))
    if missing:
        echo(f"\nnot finished ({missing} asks left) — continue with:\n  okf-wiki bench-retrieval --resume {out}")
    return 0


def _ensure_prices(out: Path, meta: dict[str, Any], echo: Callable[[str], None]) -> None:
    """List prices per token, looked up once (OpenRouter's public price list) and saved in run.json."""
    if meta.get("prices", {}).get("llm"):
        return
    cfg = WikiConfig.model_validate({**meta["config"], "bundle": out})
    llm_p = bench.llm_price(cfg)
    meta["prices"] = {"llm": llm_p, "classifier": bench.JEV_PRICE}
    (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    echo(f"list prices per 1M tokens: LLM {'n/a' if llm_p is None else f'{llm_p[0]*1e6:.2f} in / {llm_p[1]*1e6:.2f} out'} · classifier 0.042 in")


def _meta(cfg: WikiConfig) -> dict[str, Any]:
    """The settings a run records (never the API keys)."""
    return {
        "config": cfg.model_dump(mode="json", exclude={"bundle", "usage_log"}) | {
            "llm": cfg.llm.model_dump(exclude={"api_key"}), "classifier": cfg.classifier.model_dump(exclude={"api_key"})},
        "llm": {k: v for k, v in cfg.llm.model_dump().items() if k != "api_key"},
        "classifier": {k: v for k, v in cfg.classifier.model_dump().items() if k != "api_key"},
        "crow": cfg.crow.model_dump(),
        "max_notes": cfg.max_notes,
    }


def _print(out: Path, meta: dict[str, Any], echo: Callable[[str], None]) -> None:
    stats = json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]
    echo("\n" + " " * 45 + "  ".join(f"{m:>26}" for m in stats))
    for label, vals in _table_rows(stats):
        echo(f"{label:44} " + "  ".join(f"{v:>26}" for v in vals))
    echo(f"\nreport: {out / 'report.md'} · charts: {out / 'report.html'}")
