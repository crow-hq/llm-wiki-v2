# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""How the cost of filing grows as the wiki fills, from the runs of `incremental.py`: tokens, cost and time per document.

    python benchmarks/growth.py RUN [RUN ...]
    python benchmarks/growth.py RUN... --block 200 --boot 2000 --json growth.json
    python benchmarks/growth.py runs/C18-* runs/K18-* --only "K18 llm_dec,C18 clf"

Free: it only reads the run folders (or the checkpoint folders `ckpt-*` of a run). A run folder is `LABEL-CORPUS` (the label,
an arm, is what comes before the first dash); the runs of one corpus are printed on one line, in the order they are given.

Per document, over the calls of its filing (the calls that lay out folders, `plan`, `plan_more` and `split`, belong to no
document and are kept apart): tokens by type, `llm_write` (the summaries and merges the writer makes), `llm_dec` (the other LLM
calls: the decisions of a classic run) and `clf` (the classifier), the tokens by operation, the original cost and the seconds
(a document's latency is the sum of the seconds of its calls). A document's calls are read from `calls.jsonl` by their `filed`
(documents filed before the call); for a run made before that field the order of `usage.jsonl` is used instead, a document
starting at its summarize call, which is exact for one at a time except at the edges of a long document.

Per run and measure it prints the least-squares slope of the per-document values over the number of documents already filed,
per 100 documents, and then, per arm and measure, the same slope pooled over corpora (each corpus's values centred on its
own mean) with a bootstrap interval over documents (`--boot` resamples, seed 5, 95%). `--only "LABEL measure,..."` keeps those
pools, in that order (the bootstrap draws in turn from one generator, so the order is part of the result).

With `--block N` it also prints, per run, the documents in blocks of N: the mean of each measure per document with a bootstrap
interval over the documents of the block, and two fits over the block means: the linear one (slope per 100 documents filed) and
the log-log one (the exponent: 1 is linear growth of the cost, 0 flat). `--json FILE` writes all of it.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

WRITE = {"librarian/summarize", "librarian/summarize_part", "librarian/summarize_combine", "librarian/merge", "librarian/merge_source"}
PLAN = {"librarian/plan", "librarian/plan_more", "librarian/split"}
MEASURES = ("llm_dec", "clf", "llm_all")  # what the slopes and the pools are given for
BLOCK_MEASURES = ("llm_write", "llm_dec", "clf", "cost", "seconds")


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def new_doc() -> dict[str, Any]:
    return {"llm_write": 0, "llm_dec": 0, "clf": 0, "cost": 0.0, "seconds": 0.0, "ops": {}}


def add(doc: dict[str, Any], kind: str, op: str, tokens: int, cost: float, seconds: float) -> None:
    key = "clf" if kind != "llm" else "llm_write" if op in WRITE else "llm_dec"
    doc[key] += tokens
    doc["cost"] += cost
    doc["seconds"] += seconds
    doc["ops"][op] = doc["ops"].get(op, 0) + tokens


def per_doc(run: Path) -> dict[str, Any]:
    """The documents of a run in filing order, each {llm_write, llm_dec, clf, cost, seconds, ops}, and the `plan` and `tail`
    (after the last result) totals in the same shape."""
    calls = jsonl(run / "calls.jsonl")
    plan, tail = new_doc(), new_doc()
    if calls and all("filed" in c for c in calls):
        n = len(jsonl(run / "docs.jsonl")) or 1 + max(c["filed"] for c in calls)
        docs = [new_doc() for _ in range(n)]
        for c in calls:
            where = plan if c["phase"] == "plan" else docs[c["filed"]] if c["filed"] < n else tail
            add(where, c["kind"], c["op"], (c["in"] or 0) + (c["out"] or 0), c["cost"] or 0.0, c["seconds"] or 0.0)
        return {"docs": docs, "plan": plan, "tail": tail, "source": "calls"}
    docs, cur, prev = [], None, None
    for u in jsonl(run / "usage.jsonl"):
        op = u.get("op")
        start = (op == "librarian/summarize" and prev != "librarian/summarize_combine") or (
            op == "librarian/summarize_part" and prev != "librarian/summarize_part"
        )
        if start:
            cur = new_doc()
            docs.append(cur)
        prev = op
        where = plan if op in PLAN else cur
        if where is not None:
            tokens = (u["input_tokens"] or 0) + (u["output_tokens"] or 0)
            add(where, u["kind"], op, tokens, u.get("cost_usd") or 0.0, u.get("seconds") or 0.0)
    return {"docs": docs, "plan": plan, "tail": tail, "source": "usage"}


def value(doc: dict[str, Any], measure: str) -> float:
    return doc["llm_dec"] + doc["llm_write"] if measure == "llm_all" else doc[measure]


def slope(xs: list[float], ys: list[float]) -> float:
    """Least-squares slope of ys over xs, per 100 xs."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx * 100 if sxx else 0.0


def loglog(xs: list[float], ys: list[float]) -> float | None:
    """The exponent of ys ~ xs^b (least squares on the logs); None when fewer than two points are positive."""
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys, strict=True) if x > 0 and y > 0]
    if len(pts) < 2:
        return None
    lx, ly = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
    sxx = sum((x - mx) ** 2 for x in lx)
    return sum((x - mx) * (y - my) for x, y in zip(lx, ly, strict=True)) / sxx if sxx else None


def interval(values: list[float], boot: int, rnd: random.Random) -> tuple[float, float]:
    """The mean of `values` with its 95% bootstrap interval over them."""
    means = sorted(sum(rnd.choices(values, k=len(values))) / len(values) for _ in range(boot))
    return means[int(0.025 * boot)], means[boot - 1 - int(0.025 * boot)]


def blocks_of(docs: list[dict[str, Any]], size: int, boot: int) -> list[dict[str, Any]]:
    """Per block of `size` documents: its end and, per measure, the mean per document and the bootstrap interval over them."""
    rnd = random.Random(5)
    out = []
    for start in range(0, len(docs), size):
        part = docs[start : start + size]
        row: dict[str, Any] = {"first": start, "end": start + len(part), "docs": len(part)}
        for m in BLOCK_MEASURES:
            v = [float(d[m]) for d in part]
            row[m] = {"mean": sum(v) / len(v), "low": None, "high": None}
            if boot and len(v) > 1:
                row[m]["low"], row[m]["high"] = interval(v, boot, rnd)
        ops: dict[str, float] = {}
        for d in part:
            for op, t in d["ops"].items():
                ops[op] = ops.get(op, 0) + t / len(part)
        row["ops"] = ops
        out.append(row)
    return out


def block_fits(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    """The fits over the block means: the middle of a block is its position in the documents filed."""
    mid = [(r["first"] + r["end"]) / 2 for r in rows]
    fits: dict[str, dict[str, float | None]] = {}
    for m in BLOCK_MEASURES:
        means = [r[m]["mean"] for r in rows]
        fits[m] = {"slope_per_100": slope(mid, means) if len(rows) > 1 else None, "exponent": loglog(mid, means)}
    return fits


def label_of(run: Path) -> tuple[str, str]:
    arm, _, corpus = run.name.partition("-")
    return (arm, corpus) if corpus else ("", run.name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="how the cost of filing grows with the wiki, from runs of incremental.py")
    p.add_argument("runs", type=Path, nargs="+", help="run folders (LABEL-CORPUS) or checkpoint folders")
    p.add_argument("--block", type=int, default=0, metavar="N", help="also per block of N documents, with fits over the blocks")
    p.add_argument("--boot", type=int, default=2000, help="bootstrap resamples (default 2000)")
    p.add_argument("--only", default=None, help='the pools to print, "LABEL measure,LABEL measure" (default: every one)')
    p.add_argument("--json", type=Path, default=None, help="also write the measures to this file")
    a = p.parse_args(argv)
    for run in a.runs:
        if not ((run / "calls.jsonl").exists() or (run / "usage.jsonl").exists()):
            p.error(f"{run}: no calls.jsonl or usage.jsonl (not a run of incremental.py)")
    loaded = {run: per_doc(run) for run in a.runs}
    arms = list(dict.fromkeys(label_of(r)[0] for r in a.runs))
    pools: dict[str, list[tuple[float, float]]] = {f"{arm} {m}": [] for arm in arms for m in MEASURES}
    if a.only:
        wanted = [x.strip() for x in a.only.split(",")]
        unknown = [x for x in wanted if x not in pools]
        if unknown:
            p.error(f"--only: no pool {unknown}; there are {list(pools)}")
        pools = {x: [] for x in wanted}
    corpora = list(dict.fromkeys(label_of(r)[1] for r in a.runs))
    per_run: dict[str, Any] = {}
    for corpus in corpora:
        line = [f"{corpus:11}"]
        for run in (r for r in a.runs if label_of(r)[1] == corpus):
            arm, docs = label_of(run)[0], loaded[run]["docs"]
            xs = list(range(len(docs)))
            slopes = {}
            for m in MEASURES:
                name = f"{arm} {m}"
                if name not in pools or len(docs) < 2:
                    continue
                ys = [value(d, m) for d in docs]
                mean = sum(ys) / len(ys)
                pools[name] += [(x, y - mean) for x, y in zip(xs, ys, strict=True)]
                slopes[m] = slope([float(x) for x in xs], ys)
                line.append(f"{name} {slopes[m]:+.0f}")
            line.append(f"({arm} docs found {len(docs)})")
            entry: dict[str, Any] = {"documents": len(docs), "source": loaded[run]["source"], "slope_per_100": slopes}
            entry["plan"] = {k: v for k, v in loaded[run]["plan"].items() if k != "ops"}
            if a.block and docs:
                rows = blocks_of(docs, a.block, a.boot)
                entry["blocks"], entry["fits"] = rows, block_fits(rows)
            per_run[run.name] = entry
        print("  ".join(line))
    rnd = random.Random(5)
    pooled: dict[str, Any] = {}
    for name, pts in pools.items():
        if len(pts) < 2:
            continue
        s = slope([x for x, _ in pts], [y for _, y in pts])
        bs = []
        for _ in range(a.boot):
            smp = [pts[rnd.randrange(len(pts))] for _ in pts]
            bs.append(slope([q[0] for q in smp], [q[1] for q in smp]))
        bs.sort()
        low, high = bs[int(0.025 * a.boot)], bs[a.boot - 1 - int(0.025 * a.boot)]
        pooled[name] = {"slope_per_100": s, "low": low, "high": high}
        print(f"POOLED {name}: slope per 100 docs already filed {s:+.0f} [{low:+.0f}, {high:+.0f}]")
    if a.block:
        for name, e in per_run.items():
            print(f"\n== {name}: blocks of {a.block} (mean per document [95% over the block's documents])")
            for r in e.get("blocks", []):
                cells = [
                    f"{m} {r[m]['mean']:.4g}" + ("" if r[m]["low"] is None else f" [{r[m]['low']:.4g}, {r[m]['high']:.4g}]")
                    for m in BLOCK_MEASURES
                ]
                print(f"  {r['first']:5d}-{r['end']:5d}  " + "  ".join(cells))
            fits = e.get("fits", {})
            shown = [
                f"{m} {f['slope_per_100']:+.4g}/100 docs, exponent {f['exponent']:.2f}"
                for m, f in fits.items()
                if None not in f.values()
            ]
            print("  fit over the blocks: " + "; ".join(shown))
    if a.json:
        a.json.write_text(json.dumps({"runs": per_run, "pooled": pooled}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
