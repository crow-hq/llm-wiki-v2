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

"""Benchmark: catalogue the same Markdown files into a classic and a CROW wiki, side by side.

    okf-wiki bench [--data bench-data] [--limit N] [--out bench-runs/<stamp>]
    okf-wiki bench --resume bench-runs/<stamp> [--limit N]   continue a stopped run
    okf-wiki bench --report bench-runs/<stamp>               rebuild the report from saved data
    (every run also writes report.pdf when the pdf extra is installed; --no-pdf skips it)

For every ingest it records latency, tokens, model time and cost, split into the
LLM and the classifier ledgers and per step, and the number of notes already in
the wiki, so the report can show how consumption grows as the wiki grows.
Output: two wikis (open them with `okf-wiki --bundle … serve`), rows.jsonl,
report.md and report.html. Every ingest is saved as it finishes, so a run can be
stopped (Ctrl+C) and resumed later, or analysed as it is.
"""

from __future__ import annotations

import html
import json
import math
import random
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from okf_wiki.client import ModelError
from okf_wiki.config import WikiConfig
from okf_wiki.usage import Usage, UsageReport
from okf_wiki.wiki import Wiki

Price = tuple[float, float]  # USD per token: (input, output)
JEV_PRICE: Price = (0.042e-6, 0.0)  # TypeSafe / OpenRouter list price; Jev output is free
OPENROUTER = "https://openrouter.ai/api/v1"
COLORS = {"classic": "#34548a", "crow": "#b3261e"}


# -- inputs ---------------------------------------------------------------------------


def collect(data: Path, limit: int | None = None, seed: int | None = None) -> list[Path]:
    """Every .md file under `data`, any depth, hidden folders skipped.

    With a `seed` the order is shuffled (the same seed always gives the same order), so a
    run samples topics across folders instead of going folder by folder; `limit` then takes
    the first files of that order.
    """
    files = sorted(
        p for p in Path(data).rglob("*.md") if p.is_file() and not any(part.startswith(".") for part in p.parts)
    )
    if seed is not None:
        random.Random(seed).shuffle(files)
    return files[:limit] if limit else files


def llm_price(cfg: WikiConfig, client: httpx.Client | None = None) -> Price | None:
    """OpenRouter list price of the LLM, for the first pinned provider if one is set."""
    if not cfg.llm.model.startswith("openrouter/"):
        return None
    model = cfg.llm.model.removeprefix("openrouter/").split(":")[0]
    order = (cfg.llm.extra_body.get("provider") or {}).get("order") or []
    client = client or httpx.Client(timeout=20)
    try:
        if order:
            endpoints = client.get(f"{OPENROUTER}/models/{model}/endpoints").json()["data"]["endpoints"]
            for e in endpoints:
                if str(e.get("tag") or "").split("/")[0] == order[0] or e.get("provider_name", "").lower() == order[0]:
                    return float(e["pricing"]["prompt"]), float(e["pricing"]["completion"])
        for m in client.get(f"{OPENROUTER}/models").json()["data"]:
            if m["id"] == model:
                return float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        pass
    return None


# -- running ---------------------------------------------------------------------------


def _delta(before: UsageReport, after: UsageReport) -> dict[str, Any]:
    """What one ingest spent: per ledger, and per step ("llm:librarian/summarize", "classifier:route")."""

    def diff(a: Usage | None, b: Usage) -> dict[str, float]:
        a = a or Usage()
        return {k: round(getattr(b, k) - getattr(a, k), 9) for k in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd")}

    return {
        "llm": diff(before.llm, after.llm),
        "classifier": diff(before.classifier, after.classifier),
        "by_op": {k: diff(before.by_op.get(k), v) for k, v in after.by_op.items() if v.calls != getattr(before.by_op.get(k), "calls", 0)},
    }


def run(
    files: list[Path],
    data: Path,
    out: Path,
    cfg: WikiConfig,
    modes: list[str],
    *,
    make_wiki: Callable[[WikiConfig], Wiki] = Wiki,
    echo: Callable[[str], None] = print,
    stop: threading.Event | None = None,
    skip: dict[str, set[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Ingest `files` into one wiki per mode, in lockstep: each file goes to every mode at the same
    time (in parallel), and the next file starts only when all of them are done, so the modes
    always hold the same files. Rows are appended to out/rows.jsonl as each pair finishes.

    Files in `skip[mode]` were catalogued by an earlier session: a file that only some modes have
    is filed for the others first, which re-aligns them. Setting `stop` (the first Ctrl+C does)
    lets the current pair finish, then returns.
    """
    out.mkdir(parents=True, exist_ok=True)
    stop = stop or threading.Event()
    skip = skip or {}
    results: dict[str, list[dict[str, Any]]] = {m: [] for m in modes}
    wikis = {
        m: make_wiki(cfg.model_copy(update={"bundle": out / m, "mode": m, "usage_log": out / f"{m}-calls.jsonl"}))
        for m in modes
    }
    for wiki in wikis.values():
        wiki.init()
    rel = {p: p.relative_to(data).as_posix() for p in files}
    todo = [(i, p) for i, p in enumerate(files, 1) if any(rel[p] not in skip.get(m, set()) for m in modes)]

    def loop() -> None:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(modes)) as pool:
            for n, (i, path) in enumerate(todo, 1):
                if stop.is_set():
                    return
                need = [m for m in modes if rel[path] not in skip.get(m, set())]
                rows = list(pool.map(lambda m: _ingest(wikis[m], m, i, path, rel[path]), need))
                with (out / "rows.jsonl").open("a", encoding="utf-8") as f:
                    for row in rows:
                        results[row["mode"]].append(row)
                        f.write(json.dumps(row) + "\n")
                eta = (time.perf_counter() - started) / n * (len(todo) - n)
                for row in rows:
                    echo(_progress(row, len(files), eta))

    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    try:
        _join([worker])
    except KeyboardInterrupt:
        stop.set()
        echo("\nstopping after the file being filed now… (Ctrl+C again to quit at once)")
        try:
            _join([worker])
        except KeyboardInterrupt:
            echo("quit: the file being filed right now was not saved")
    return results


def _ingest(wiki: Wiki, mode: str, index: int, path: Path, rel: str) -> dict[str, Any]:
    """One ingest, measured: latency, and what it cost per ledger and per step."""
    tree = wiki.store.load()
    row: dict[str, Any] = {
        "mode": mode,
        "index": index,
        "file": rel,
        "notes_before": len(tree.all_notes()),
        "folders_before": sum(1 for _ in tree.walk()) - 1,
    }
    before, start = wiki.usage, time.perf_counter()
    try:
        result = wiki.ingest_file(path.read_bytes(), path.name, resource=rel)
        row.update(action=result.action, note=result.note, folder=result.folder, created_folders=result.created_folders)
    except (ModelError, ValueError, OSError) as e:
        row.update(action="error", error=str(e)[:300])
    row["seconds"] = round(time.perf_counter() - start, 3)
    row.update(_delta(before, wiki.usage))
    return row


def _join(threads: list[threading.Thread]) -> None:
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(timeout=0.5)


def load_rows(out: Path) -> dict[str, list[dict[str, Any]]]:
    """Saved rows per mode, in order; when a file was ingested again (after an error), the last row wins."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    path = out / "rows.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if line.strip():
            row = json.loads(line)
            latest.pop((row["mode"], row["file"]), None)  # re-insert so order follows the last attempt
            latest[(row["mode"], row["file"])] = row
    rows: dict[str, list[dict[str, Any]]] = {}
    for (mode, _), row in latest.items():
        rows.setdefault(mode, []).append(row)
    return rows


def catalogued(rows: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    """Files each mode already filed successfully (errors are retried on resume)."""
    return {mode: {r["file"] for r in rs if r["action"] != "error"} for mode, rs in rows.items()}


def _progress(r: dict[str, Any], total: int, eta: float) -> str:
    llm, clf = r["llm"], r["classifier"]
    what = r["action"] if r["action"] == "error" else f"{r['action']:7} {r['folder']}"
    return (
        f"[{r['mode']:7} {r['index']:>4}/{total}] {r['notes_before']:>4} notes │ {what[:34]:34} {r['seconds']:6.1f}s │ "
        f"llm {llm['calls']:.0f}× {_k(llm['input_tokens'] + llm['output_tokens'])} tok │ "
        f"clf {clf['calls']:.0f}× {_k(clf['input_tokens'] + clf['output_tokens'])} tok │ eta {_dur(eta)}"
    )


# -- analysis -----------------------------------------------------------------------------


def fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least squares y = a + b·x; returns (a, b, r²)."""
    n = len(xs)
    if n < 2 or len(set(xs)) < 2:
        return (sum(ys) / n if n else 0.0), 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - ss_res / ss_tot) if ss_tot else 1.0


SERIES: dict[str, Callable[[dict[str, Any]], float]] = {
    # LLM and classifier tokens are priced ~10x apart, so they are never summed.
    "LLM tokens (in + out)": lambda r: r["llm"]["input_tokens"] + r["llm"]["output_tokens"],
    "LLM input tokens": lambda r: r["llm"]["input_tokens"],
    "LLM output tokens": lambda r: r["llm"]["output_tokens"],
    "classifier input tokens": lambda r: r["classifier"]["input_tokens"],
    "latency (s)": lambda r: r["seconds"],
}


def summarize(rows: list[dict[str, Any]], llm_p: Price | None, clf_p: Price) -> dict[str, Any]:
    ok = [r for r in rows if r["action"] != "error"]
    lat = sorted(r["seconds"] for r in ok)

    def total(ledger: str, key: str) -> float:
        return sum(r[ledger][key] for r in rows)

    def listed(ledger: str, price: Price | None) -> float | None:
        return None if price is None else total(ledger, "input_tokens") * price[0] + total(ledger, "output_tokens") * price[1]

    llm_list, clf_list = listed("llm", llm_p), listed("classifier", clf_p) if total("classifier", "calls") else 0.0
    return {
        "ingests": len(rows),
        "created": sum(r["action"] == "created" for r in rows),
        "merged": sum(r["action"] == "merged" for r in rows),
        "errors": len(rows) - len(ok),
        "folders_created": sum(len(r.get("created_folders") or []) for r in rows),
        "latency_mean": sum(lat) / len(lat) if lat else 0.0,
        "latency_p50": statistics.median(lat) if lat else 0.0,
        "latency_p95": _pct(lat, 0.95),
        "wall_seconds": sum(r["seconds"] for r in rows),
        **{f"{ledger}_{key}": total(ledger, key) for ledger in ("llm", "classifier") for key in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd")},
        "llm_cost_list": llm_list,
        "classifier_cost_list": clf_list,
        "growth": {name: fit([r["notes_before"] for r in ok], [f(r) for r in ok]) for name, f in SERIES.items()},
        "growth_by_step": _step_growth(ok),
    }


def _step_growth(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float, float]]:
    steps = sorted({k for r in rows for k in r["by_op"]})
    xs = [r["notes_before"] for r in rows]
    return {
        s: fit(xs, [r["by_op"].get(s, {}).get("input_tokens", 0) + r["by_op"].get(s, {}).get("output_tokens", 0) for r in rows])
        for s in steps
    }


def buckets(rows: list[dict[str, Any]], n: int = 8) -> list[tuple[str, float, float, int]]:
    """(range of notes already saved, mean LLM tokens, mean classifier tokens, ingests) per bucket."""
    ok = [r for r in rows if r["action"] != "error"]
    if not ok:
        return []
    hi = max(r["notes_before"] for r in ok) + 1
    width = max(1, math.ceil(hi / n))
    out = []
    for lo in range(0, hi, width):
        group = [r for r in ok if lo <= r["notes_before"] < lo + width]
        if group:
            llm = sum(r["llm"]["input_tokens"] + r["llm"]["output_tokens"] for r in group) / len(group)
            clf = sum(r["classifier"]["input_tokens"] + r["classifier"]["output_tokens"] for r in group) / len(group)
            out.append((f"{lo}–{lo + width - 1}", llm, clf, len(group)))
    return out


# -- reports --------------------------------------------------------------------------------


def params(cfg: WikiConfig, data: Path, files: list[Path], llm_p: Price | None, clf_p: Price) -> dict[str, Any]:
    return {
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": str(data),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "llm": {k: v for k, v in cfg.llm.model_dump().items() if k != "api_key"},
        "classifier": {k: v for k, v in cfg.classifier.model_dump().items() if k != "api_key"},
        "crow": cfg.crow.model_dump(),
        "wiki": {k: getattr(cfg, k) for k in ("summarize", "max_depth", "max_links", "source_chars")},
        "price_per_million": {
            "llm": None if llm_p is None else [llm_p[0] * 1e6, llm_p[1] * 1e6],
            "classifier": [clf_p[0] * 1e6, clf_p[1] * 1e6],
        },
    }


def write_reports(out: Path, meta: dict[str, Any], results: dict[str, list[dict[str, Any]]], llm_p: Price | None, clf_p: Price) -> dict[str, Any]:
    stats = {m: summarize(rows, llm_p, clf_p) for m, rows in results.items() if rows}
    (out / "results.json").write_text(json.dumps({"params": meta, "summary": stats}, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(_markdown(meta, stats, results, out), encoding="utf-8")
    (out / "report.html").write_text(_html(meta, stats, results), encoding="utf-8")
    return stats


def _table_rows(stats: dict[str, dict[str, Any]]) -> list[tuple[str, list[str]]]:
    modes = list(stats)

    def row(label: str, f: Callable[[dict[str, Any]], str]) -> tuple[str, list[str]]:
        return label, [f(stats[m]) for m in modes]

    def money(v: float | None) -> str:
        return "n/a" if v is None else f"${v:.4f}"

    return [
        row("ingests (created / merged / errors)", lambda s: f"{s['ingests']} ({s['created']} / {s['merged']} / {s['errors']})"),
        row("folders created", lambda s: str(s["folders_created"])),
        row("latency per ingest: mean · p50 · p95", lambda s: f"{s['latency_mean']:.1f}s · {s['latency_p50']:.1f}s · {s['latency_p95']:.1f}s"),
        row("LLM calls", lambda s: f"{s['llm_calls']:.0f}"),
        row("LLM tokens in / out", lambda s: f"{s['llm_input_tokens']:,.0f} / {s['llm_output_tokens']:,.0f}"),
        row("LLM time (sum · per call)", lambda s: f"{s['llm_seconds']:.0f}s · {s['llm_seconds'] / max(s['llm_calls'], 1):.1f}s"),
        row("classifier calls", lambda s: f"{s['classifier_calls']:.0f}"),
        row("classifier tokens in / out", lambda s: f"{s['classifier_input_tokens']:,.0f} / {s['classifier_output_tokens']:,.0f}"),
        row("classifier time (sum · per call)", lambda s: f"{s['classifier_seconds']:.0f}s · {s['classifier_seconds'] / max(s['classifier_calls'], 1):.2f}s"),
        row("cost reported by provider: LLM · classifier", lambda s: f"{money(s['llm_cost_usd'])} · {money(s['classifier_cost_usd'])}"),
        row("cost at list price: LLM · classifier", lambda s: f"{money(s['llm_cost_list'])} · {money(s['classifier_cost_list'])}"),
        row("cost reported, total", lambda s: money(s["llm_cost_usd"] + s["classifier_cost_usd"])),
        row("cost per ingest (reported)", lambda s: money((s["llm_cost_usd"] + s["classifier_cost_usd"]) / max(s["ingests"], 1))),
    ]


def _markdown(meta: dict[str, Any], stats: dict[str, dict[str, Any]], results: dict[str, list[dict[str, Any]]], out: Path) -> str:
    modes = list(stats)
    lines = [f"# Benchmark {meta['started']}", "", f"{meta['files']} Markdown files from `{meta['data']}` ({meta['bytes']:,} bytes).", ""]
    lines += ["## Setup", "", "```json", json.dumps({k: meta[k] for k in ("llm", "classifier", "crow", "wiki", "price_per_million")}, indent=2), "```", ""]
    lines += ["## Results", "", "| | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    lines += [f"| {label} | " + " | ".join(vals) + " |" for label, vals in _table_rows(stats)]
    lines += ["", "## Growth with the size of the wiki", "", "Linear fit of each ingest against the number of notes already saved: "
              "**slope** = extra units per note already in the wiki, **start** = value on an empty wiki.", ""]
    lines += ["| series | " + " | ".join(f"{m} start · slope · r²" for m in modes) + " |", "|---|" + "---|" * len(modes)]
    for name in SERIES:
        lines.append(f"| {name} | " + " | ".join(_fit_cell(stats[m]["growth"][name]) for m in modes) + " |")
    for m in modes:
        lines += ["", f"### {m}: growth per step (tokens in + out)", "", "| step | start · slope · r² |", "|---|---|"]
        lines += [f"| {step} | {_fit_cell(g)} |" for step, g in stats[m]["growth_by_step"].items()]
        lines += ["", f"### {m}: mean tokens per ingest by notes already saved", "", "| notes saved | LLM | classifier | ingests |", "|---|---|---|---|"]
        lines += [f"| {b} | {llm:,.0f} | {clf:,.0f} | {n} |" for b, llm, clf, n in buckets(results[m])]
    lines += ["", "## Open the wikis", "", "```bash"]
    lines += [f"okf-wiki --bundle {out / m} --mode {m} serve --port {8201 + i}" for i, m in enumerate(modes)]
    lines += ["```", "", "Files: `rows.jsonl` (one line per ingest), `<mode>-calls.jsonl` (one line per model call), `report.html` (charts).", ""]
    return "\n".join(lines)


def _fit_cell(g: tuple[float, float, float]) -> str:
    a, b, r2 = g
    return f"{a:,.1f} · {b:+,.2f} · {r2:.2f}"


def _svg(title: str, series: dict[str, tuple[list[float], list[float], str]], unit: str) -> str:
    """Scatter of y against notes already saved, with a fitted line per series."""
    w, h, pad = 660, 260, 46
    pts = [(x, y) for xs, ys, _ in series.values() for x, y in zip(xs, ys)]
    if not pts:
        return ""
    x_max = max(max(x for x, _ in pts), 1)
    y_max = max(max(y for _, y in pts), 1) * 1.08

    def px(x: float) -> float:
        return pad + (w - pad - 12) * x / x_max

    def py(y: float) -> float:
        return h - pad + 8 - (h - pad - 8) * min(max(y, 0.0), y_max) / y_max  # clamped to the plot

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img"><text x="{pad}" y="16" class="t">{html.escape(title)}</text>']
    parts.append(f'<line x1="{pad}" y1="{py(0)}" x2="{w - 12}" y2="{py(0)}" class="ax"/><line x1="{pad}" y1="{py(0)}" x2="{pad}" y2="20" class="ax"/>')
    parts.append(f'<text x="{pad - 6}" y="{py(y_max / 1.08) + 4}" class="n" text-anchor="end">{_k(y_max / 1.08)}</text><text x="{pad - 6}" y="{py(0) + 4}" class="n" text-anchor="end">0</text>')
    parts.append(f'<text x="{w - 12}" y="{h - 8}" class="n" text-anchor="end">{x_max:.0f} notes saved</text><text x="{pad}" y="{h - 8}" class="n">0</text>')
    for i, (label, (xs, ys, color)) in enumerate(series.items()):
        parts += [f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="2.6" fill="{color}" fill-opacity=".45"/>' for x, y in zip(xs, ys)]
        a, b, _ = fit(xs, ys)
        parts.append(f'<line x1="{px(0):.1f}" y1="{py(a):.1f}" x2="{px(x_max):.1f}" y2="{py(a + b * x_max):.1f}" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text x="{w - 14}" y="{34 + 15 * i}" class="n" text-anchor="end" fill="{color}">{html.escape(label)}: {b:+,.1f} {unit}/note</text>')
    return "".join(parts) + "</svg>"


def _html(meta: dict[str, Any], stats: dict[str, dict[str, Any]], results: dict[str, list[dict[str, Any]]]) -> str:
    modes = list(stats)

    def series(f: Callable[[dict[str, Any]], float], only: list[str] | None = None) -> dict[str, tuple[list[float], list[float], str]]:
        return {
            m: ([r["notes_before"] for r in ok], [f(r) for r in ok], COLORS.get(m, "#555"))
            for m in (only or modes)
            if m in results and (ok := [r for r in results[m] if r["action"] != "error"])
        }

    charts = [
        _svg("LLM tokens per ingest (in + out)", series(SERIES["LLM tokens (in + out)"]), "tok"),
        _svg("LLM input tokens per ingest", series(SERIES["LLM input tokens"]), "tok"),
        _svg("Classifier input tokens per ingest", series(SERIES["classifier input tokens"], [m for m in modes if m == "crow"]), "tok"),
        _svg("Latency per ingest (seconds)", series(SERIES["latency (s)"]), "s"),
    ]
    table = "".join(f"<tr><th>{html.escape(label)}</th>" + "".join(f"<td>{html.escape(v)}</td>" for v in vals) + "</tr>" for label, vals in _table_rows(stats))
    growth = "".join(
        f"<tr><th>{html.escape(name)}</th>" + "".join(f"<td>{html.escape(_fit_cell(stats[m]['growth'][name]))}</td>" for m in modes) + "</tr>"
        for name in SERIES
    )
    setup = html.escape(json.dumps({k: meta[k] for k in ("llm", "classifier", "crow", "wiki", "price_per_million")}, indent=2))
    heads = "".join(f"<th style='color:{COLORS.get(m, '#555')}'>{m}</th>" for m in modes)
    return f"""<!doctype html><meta charset="utf-8"><title>okf-wiki benchmark</title>
<style>body{{font:15px/1.5 Georgia,serif;background:#f5efe3;color:#1f1c17;max-width:980px;margin:40px auto;padding:0 24px}}
h1{{font-weight:600;letter-spacing:-.01em}}h2{{margin-top:40px}}table{{border-collapse:collapse;width:100%;font:13px ui-monospace,monospace}}
th,td{{border-bottom:1px solid #e2d9c7;padding:6px 10px;text-align:left}}svg{{width:100%;background:#fbf8f1;border:1px solid #e2d9c7;margin:10px 0}}
.t{{font:600 13px Georgia,serif}}.n{{font:11px ui-monospace,monospace;fill:#756d60}}.ax{{stroke:#bdb3a0}}pre{{background:#fbf8f1;padding:12px;font-size:12px;overflow:auto}}</style>
<h1>Benchmark · {html.escape(meta['started'])}</h1>
<p>{meta['files']} Markdown files from <code>{html.escape(meta['data'])}</code>, catalogued into one wiki per mode.</p>
<h2>Results</h2><table><tr><th></th>{heads}</tr>{table}</table>
<h2>Growth with the size of the wiki</h2><p>Each dot is one ingest, placed by the number of notes already saved; lines are least-squares fits (slope per note).</p>
{''.join(charts)}
<table><tr><th>series (start · slope · r²)</th>{heads}</tr>{growth}</table>
<h2>Setup</h2><pre>{setup}</pre>"""


# -- entry point ------------------------------------------------------------------------------


def main(args: Any, cfg: WikiConfig, *, make_wiki: Callable[[WikiConfig], Wiki] = Wiki, echo: Callable[[str], None] = print) -> int:
    pdf = not getattr(args, "no_pdf", False)
    if getattr(args, "report", None):
        return _report_only(Path(args.report), echo, pdf)
    out = Path(getattr(args, "resume", None) or args.out or f"bench-runs/{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    saved = _load_run(out) if getattr(args, "resume", None) else None
    if getattr(args, "resume", None) and saved is None:
        echo(f"nothing to resume in {out} (no run.json)")
        return 2
    if saved:  # the run's own settings win over the current environment
        data, modes, limit, seed = Path(saved["data"]), saved["modes"], args.limit or saved.get("limit"), saved.get("seed")
        cfg = WikiConfig.model_validate({**saved["config"], "bundle": out, "llm": {**saved["config"]["llm"], "api_key": cfg.llm.api_key},
                                         "classifier": {**saved["config"]["classifier"], "api_key": cfg.classifier.api_key}})
        llm_p, clf_p = _price(saved["prices"]["llm"]), _price(saved["prices"]["classifier"]) or JEV_PRICE
    else:
        data, modes, limit = Path(args.data), [m.strip() for m in args.modes.split(",") if m.strip()], args.limit
        seed = getattr(args, "seed", None)
        seed = random.randrange(1_000_000) if seed is None else seed  # random order, saved to be repeatable
        llm_p = _price(args.llm_price) if args.llm_price else llm_price(cfg)
        clf_p = _price(args.classifier_price) if args.classifier_price else JEV_PRICE
    files = collect(data, limit, seed)
    if not files:
        echo(f"no .md files under {data}")
        return 2
    meta = params(cfg, data, files, llm_p, clf_p) | {"seed": seed}  # type: ignore[arg-type]
    if saved:
        meta["started"] = saved["started"]
    else:
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.json").write_text(json.dumps({
            "started": meta["started"], "data": str(data), "modes": modes, "limit": limit, "seed": seed,
            "config": cfg.model_dump(mode="json", exclude={"bundle", "usage_log"}) | {
                "llm": cfg.llm.model_dump(exclude={"api_key"}), "classifier": cfg.classifier.model_dump(exclude={"api_key"})},
            "prices": {"llm": llm_p, "classifier": clf_p},
        }, indent=2), encoding="utf-8")
    skip = catalogued(load_rows(out))
    left = {m: sum(1 for f in files if f.relative_to(data).as_posix() not in skip.get(m, set())) for m in modes}
    verb = "resuming" if saved else "benchmark"
    order = f"random order (seed {seed})" if seed is not None else "alphabetical order"
    echo(f"{verb}: {len(files)} files in {order} · to do {', '.join(f'{m} {n}' for m, n in left.items())} · llm {cfg.llm.model} · "
         f"classifier {cfg.classifier.model} → {out}")
    run(files, data, out, cfg, modes, make_wiki=make_wiki, echo=echo, skip=skip)
    results = load_rows(out)  # every session of this run, not only this one
    stats = write_reports(out, meta, results, llm_p, clf_p)  # type: ignore[arg-type]
    _print_summary(stats, out, echo)
    unfinished = {m: n for m in modes if (n := len(files) - len(catalogued(results).get(m, set())))}
    if unfinished:
        echo(f"\nnot finished ({', '.join(f'{m}: {n} left' for m, n in unfinished.items())}) — continue with:"
             f"\n  okf-wiki bench --resume {out}")
    return _pdf(out, echo) if pdf else 0


def _report_only(out: Path, echo: Callable[[str], None], pdf: bool = False) -> int:
    """Rebuild the reports of a run from its saved rows, without calling any model."""
    saved, rows = _load_run(out), load_rows(out)
    if saved is None or not rows:
        echo(f"no saved benchmark in {out}")
        return 2
    llm_p, clf_p = _price(saved["prices"]["llm"]), _price(saved["prices"]["classifier"]) or JEV_PRICE
    cfg = WikiConfig.model_validate({**saved["config"], "bundle": out})
    files = collect(Path(saved["data"]), saved.get("limit"), saved.get("seed")) if Path(saved["data"]).is_dir() else []
    meta = params(cfg, Path(saved["data"]), files, llm_p, clf_p) | {"started": saved["started"]}
    stats = write_reports(out, meta, rows, llm_p, clf_p)  # type: ignore[arg-type]
    _print_summary(stats, out, echo)
    return _pdf(out, echo) if pdf else 0


def _pdf(out: Path, echo: Callable[[str], None]) -> int:
    try:
        from okf_wiki.bench_pdf import write_pdf

        echo(f"pdf: {write_pdf(out)}")
        return 0
    except ImportError:
        echo('no PDF: it needs the pdf extra (pip install "llm-wiki-v2[pdf]"); report.md and report.html are written')
        return 0


def _load_run(out: Path) -> dict[str, Any] | None:
    path = out / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _price(value: Any) -> Price | None:
    """'IN,OUT' in USD per million tokens, or a saved (input, output) pair per token."""
    if value is None:
        return None
    if isinstance(value, str):
        a, b = (float(v) / 1e6 for v in value.split(","))
        return a, b
    return float(value[0]), float(value[1])


def _print_summary(stats: dict[str, dict[str, Any]], out: Path, echo: Callable[[str], None]) -> None:
    echo("\n" + " " * 45 + "  ".join(f"{m:>28}" for m in stats))
    for label, vals in _table_rows(stats):
        echo(f"{label:44} " + "  ".join(f"{v:>28}" for v in vals))
    echo(f"\nreport: {out / 'report.md'} · charts: {out / 'report.html'}")
    for i, m in enumerate(stats):
        echo(f"open the {m} wiki: okf-wiki --bundle {out / m} --mode {m} serve --port {8201 + i}")
    return 0


def _pct(values: list[float], q: float) -> float:
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0.0


def _k(n: float) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def _dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"

