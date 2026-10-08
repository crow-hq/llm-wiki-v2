# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The verdict of a stability benchmark: a candidate against main, by a preregistered criterion.

    python benchmarks/verdict.py MAIN.json CANDIDATE.json [--corpus DIR] [--alpha 0.1] [--permutations 20000]

Both files are the raw JSON of `benchmarks/stability.py --out` (the first variant of each is read), or of a stopped run
(`{"stopped": ..., "runs": {variant: [...]}}`). The documents are scored against the `labels.json` of `--corpus`
(default `benchmarks/corpus/growth`).

The criterion: the candidate passes when it is never worse than main on merge accuracy, new accuracy, group accuracy and
pair agreement (no one-sided test says "worse" at p < alpha), and better at p < alpha on the single-document subfolders
(fewer) or on the folder accuracy.

Each test is a one-sided permutation test over the run labels: the runs of both series are pooled and split at random
`--permutations` times (seed fixed), and the observed difference of the series is compared with those of the splits.
Pair agreement is the series' mean pairwise ARI (`stability.ari` between the folder assignments of two runs of the same
series), recomputed on each permuted split from an ARI matrix computed once.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stability as S  # noqa: E402

SEED = 20261007
METRICS = {  # +1: higher is better
    "merge_accuracy": 1, "new_accuracy": 1, "group_accuracy": 1, "update_merged_or_linked": 1,
    "single_doc_folders": -1, "folder_accuracy": 1, "folder_prefix_accuracy": 1, "false_merge_rate": -1,
}  # fmt: skip
NEVER_WORSE = ("merge_accuracy", "new_accuracy", "group_accuracy", "pair_agreement")
BETTER_ONE = ("single_doc_folders", "folder_accuracy")


def runs_of(path: Path | str) -> list[dict[str, Any]]:
    """The runs of the first variant of a result file: a finished one (`variants`) or a stopped one (`runs`)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_variant = {v: r["runs"] for v, r in data["variants"].items()} if "variants" in data else data["runs"]
    return next(iter(by_variant.values()))  # type: ignore[no-any-return]


def compare(
    main: list[dict[str, Any]], cand: list[dict[str, Any]], labels: dict[str, dict[str, Any]], permutations: int = 20_000
) -> dict[str, tuple[float, float, float, float]]:
    """Per metric (and `pair_agreement`): (main's mean, candidate's mean, p that the candidate is worse, p that it is better)."""
    pool = main + cand
    n = len(main)
    values = [S.accuracy(r, labels) for r in pool]
    folders = [{d: v["folder"] for d, v in r["docs"].items()} for r in pool]
    ari = {(i, j): S.ari(folders[i], folders[j]) for i, j in itertools.combinations(range(len(pool)), 2)}

    def pair_mean(idx: Sequence[int]) -> float:
        return statistics.fmean(ari[(min(i, j), max(i, j))] for i, j in itertools.combinations(idx, 2))

    def metric_mean(idx: Sequence[int], m: str) -> float:
        return statistics.fmean(values[i][m] for i in idx)

    rng = random.Random(SEED)
    splits = []
    for _ in range(permutations):
        order = list(range(len(pool)))
        rng.shuffle(order)
        splits.append((order[:n], order[n:]))
    a, b = list(range(n)), list(range(n, len(pool)))

    def test(stat: Callable[[Sequence[int]], float]) -> tuple[float, float]:
        """(p that the candidate is worse, p that it is better): the statistic is in the 'better' direction."""
        obs = stat(b) - stat(a)
        perm = [stat(y) - stat(x) for x, y in splits]
        return sum(d <= obs + 1e-12 for d in perm) / permutations, sum(d >= obs - 1e-12 for d in perm) / permutations

    rows = {}
    for m, sign in METRICS.items():
        p_worse, p_better = test(lambda idx, m=m, s=sign: s * metric_mean(idx, m))  # type: ignore[misc]
        rows[m] = (metric_mean(a, m), metric_mean(b, m), p_worse, p_better)
    p_worse, p_better = test(pair_mean)
    rows["pair_agreement"] = (pair_mean(a), pair_mean(b), p_worse, p_better)
    return rows


def criterion(rows: dict[str, tuple[float, float, float, float]], alpha: float) -> tuple[list[str], list[str]]:
    """(the never-worse metrics on which the candidate is worse at p < alpha, the metrics on which it is better)."""
    worse = [m for m in NEVER_WORSE if rows[m][2] < alpha]
    gains = [m for m in BETTER_ONE if rows[m][3] < alpha]
    return worse, gains


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("main", type=Path, help="the raw JSON of main's runs")
    p.add_argument("candidate", type=Path, help="the raw JSON of the candidate's runs")
    p.add_argument("--corpus", type=Path, default=S.GROWTH, help="the corpus folder with the labels.json (default: growth)")
    p.add_argument("--alpha", type=float, default=0.1, help="the significance level of every test")
    p.add_argument("--permutations", type=int, default=20_000, help="random splits of the pooled runs per test")
    args = p.parse_args(argv)
    main_runs, cand_runs = runs_of(args.main), runs_of(args.candidate)
    rows = compare(main_runs, cand_runs, S.corpus_labels(args.corpus), args.permutations)
    print(f"main {args.main}\ncandidate {args.candidate}\n{len(main_runs)} + {len(cand_runs)} runs, {args.permutations} permutations\n")
    print(f"{'metric':26s} {'main':>8s} {'cand':>8s} {'p_worse':>8s} {'p_better':>9s}")
    for m, (a, b, pw, pb) in rows.items():
        print(f"{m:26s} {a:8.3f} {b:8.3f} {pw:8.3f} {pb:9.3f}")
    worse, gains = criterion(rows, args.alpha)
    print(f"\nworse than main (p < {args.alpha}): {worse or 'none'}")
    print(f"better than main (p < {args.alpha}): {gains or 'none'}")
    print("growth criterion:", "PASS" if not worse and gains else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
