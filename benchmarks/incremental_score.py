# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The measures of the runs of `incremental.py`: the shape of the wiki, what it cost and, with gold folders, how well it agrees.

    python benchmarks/incremental_score.py RUN [RUN ...]
    python benchmarks/incremental_score.py RUN... --gold manifest.json --baseline RUN_MAIN_CLASSIC

Free: it only reads the run folders. Per run it prints:
- documents, errors (documents whose ingest failed), notes, folders;
- below first level: share of the notes in a folder of depth 2 or more;
- largest folder (notes in it, subfolders not counted), folders with one note (leaves that hold exactly one), notes at the root;
- with `--gold`: AMI and pairwise co-location F1 against the gold folders (definitions below);
- cost per document: the original cost of every call, a replayed one included, over the documents;
- cache hit rate: calls served by the replay cache over all calls;
- with `--baseline RUN`: the cost per document as a ratio of the baseline run's.

`--gold` is a JSON file `{file: folder}`, or a manifest of the selection scripts (a `docs` list of `{file, folder}`).
A document's final note is the note its ingest wrote, followed through the moves logged after it was filed (`moves.jsonl`).
Documents whose ingest failed, or whose note is gone, are left out of the agreement.

Agreement with the gold, over the documents D that have a final note: each document is put in the folder its final note
ends up in (a note at the root is a folder of its own, so a root is never a cluster) and in its gold folder.
- Pairwise co-location: a pair of documents is "together" in a partition when both are in the same folder. Precision is the
  share of the pairs together in the wiki that are together in the gold, recall the share of the pairs together in the gold
  that are together in the wiki, F1 their harmonic mean.
- AMI, the adjusted mutual information of the two partitions, with the arithmetic normaliser: (MI - E[MI]) / (mean(H(a), H(b)) -
  E[MI]), E[MI] the exact expected mutual information under random partitions with the same folder sizes (the hypergeometric
  sum). 1 for the same partition, about 0 for a random one. Pure Python: it does not need scikit-learn.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def wiki_of(run: Path) -> Path:
    return run / "wiki"


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def notes(wiki: Path) -> dict[str, dict[str, str]]:
    """Every note of the wiki by its path: folder ("" for the root) and title."""
    out = {}
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki)
        if p.name in ("index.md", "log.md") or any(x == "raw" or x.startswith((".", "_")) for x in rel.parts):
            continue
        front = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1])
        folder = rel.parent.as_posix()
        out[rel.as_posix()] = {"folder": "" if folder == "." else folder, "title": str(front["title"])}
    return out


def folders_of(wiki: Path) -> set[str]:
    """Every folder (path without the leading slash) that a parent's index lists."""
    found = set()
    for index in wiki.rglob("index.md"):
        parent = index.parent.relative_to(wiki).as_posix()
        for name in re.findall(r"^\* \[[^\]]*\]\(([^)]+)/index\.md\) - ", index.read_text(encoding="utf-8"), re.M):
            found.add("/".join(x for x in (parent, name) if x not in ("", ".")))
    return found


def final_notes(run: Path) -> dict[str, str]:
    """Document -> the path of its note at the end of the run: the note its ingest wrote, followed through the later moves."""
    moves = jsonl(run / "moves.jsonl")
    out = {}
    for d in jsonl(run / "docs.jsonl"):
        rel = d.get("note")
        for m in moves:
            if m["seq"] >= d.get("log_seq", 0) and m["from"] == rel:
                rel = m["to"]
        out[d["doc"]] = rel or ""
    return out


def comb2(n: int) -> int:
    return n * (n - 1) // 2


def entropy(counts: Any, n: int) -> float:
    return -sum(c / n * math.log(c / n) for c in counts if c)


def ami(a: list[str], b: list[str]) -> float:
    """Adjusted mutual information of two partitions of the same n items (arithmetic normaliser, exact expected MI)."""
    n = len(a)
    ca, cb = Counter(a), Counter(b)
    joint = Counter(zip(a, b, strict=True))
    mi = sum(c / n * math.log(n * c / (ca[x] * cb[y])) for (x, y), c in joint.items())
    lg = math.lgamma
    emi = 0.0
    for ai in ca.values():
        for bj in cb.values():
            for nij in range(max(1, ai + bj - n), min(ai, bj) + 1):
                emi += (
                    (nij / n)
                    * math.log(n * nij / (ai * bj))
                    * math.exp(
                        lg(ai + 1)
                        + lg(bj + 1)
                        + lg(n - ai + 1)
                        + lg(n - bj + 1)
                        - lg(n + 1)
                        - lg(nij + 1)
                        - lg(ai - nij + 1)
                        - lg(bj - nij + 1)
                        - lg(n - ai - bj + nij + 1)
                    )
                )
    ha, hb = entropy(ca.values(), n), entropy(cb.values(), n)
    den = (ha + hb) / 2 - emi
    return (mi - emi) / den if abs(den) > 1e-12 else 1.0


def agreement(a: list[str], g: list[str]) -> dict[str, float]:
    """Pairwise co-location precision, recall and F1, and AMI, of the wiki's folders `a` against the gold's `g` (one per document)."""
    together = sum(comb2(c) for c in Counter(zip(a, g, strict=True)).values())
    in_wiki, in_gold = sum(comb2(c) for c in Counter(a).values()), sum(comb2(c) for c in Counter(g).values())
    precision, recall = together / in_wiki if in_wiki else 0.0, together / in_gold if in_gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"coloc_precision": precision, "coloc_recall": recall, "coloc_f1": f1, "ami": ami(a, g)}


def load_gold(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {d["file"]: d["folder"] for d in raw["docs"]} if isinstance(raw, dict) and isinstance(raw.get("docs"), list) else dict(raw)


def score(run: Path, gold: dict[str, str] | None = None) -> dict[str, Any]:
    """The measures of one run folder."""
    docs, calls = jsonl(run / "docs.jsonl"), jsonl(run / "calls.jsonl")
    n = len(docs)
    ns, folders, final = notes(wiki_of(run)), folders_of(wiki_of(run)), final_notes(run)
    size = Counter(x["folder"] for x in ns.values())
    leaves = [f for f in folders if not any(g.startswith(f + "/") for g in folders)]
    cost = sum(c["cost"] or 0 for c in calls)
    out: dict[str, Any] = {
        "run": run.name,
        "docs": n,
        "errors": sum("error" in d for d in docs),
        "notes": len(ns),
        "folders": len(folders),
        "below_first_level": sum("/" in x["folder"] for x in ns.values()) / max(1, len(ns)),
        "largest_folder": max((size[f] for f in folders), default=0),
        "single_note_folders": sum(size.get(f, 0) == 1 for f in leaves),
        "root_notes": size.get("", 0),
        "usd_per_doc": cost / n if n else None,
        "cache_hit_rate": sum(c["served"] for c in calls) / len(calls) if calls else None,
        "median_folder": statistics.median([size[f] for f in folders if size.get(f)] or [0]),
    }
    if gold:
        ids = [d["doc"] for d in docs if "error" not in d and final.get(d["doc"]) in ns and d["doc"] in gold]
        out["gold_docs"] = len(ids)
        if len(ids) >= 2:
            a = [ns[final[d]]["folder"] or f"<root:{d}>" for d in ids]
            out |= agreement(a, [gold[d] for d in ids])
    return out


def fmt(v: Any, pattern: str = "{:.3f}") -> str:
    return "-" if v is None else pattern.format(v)


def report(s: dict[str, Any], baseline: dict[str, Any] | None) -> str:
    lines = [
        f"== {s['run']}",
        f"documents {s['docs']}, errors {s['errors']}, notes {s['notes']}, folders {s['folders']}",
        f"below first level {fmt(100 * s['below_first_level'], '{:.0f}%')}, largest folder {s['largest_folder']} "
        f"(median {s['median_folder']:g}), folders with one note {s['single_note_folders']}, notes at the root {s['root_notes']}",
    ]
    if "gold_docs" in s:
        lines.append(f"gold: {s['gold_docs']} documents, AMI {fmt(s.get('ami'))}, F1 {fmt(s.get('coloc_f1'))} "
                     f"(precision {fmt(s.get('coloc_precision'))}, recall {fmt(s.get('coloc_recall'))})")
    per_doc = None if s["usd_per_doc"] is None else 1000 * s["usd_per_doc"]
    cost = f"cost per document {fmt(per_doc)} m$ (original cost, replayed calls included)"
    if baseline and baseline["usd_per_doc"] and s["usd_per_doc"] is not None:
        cost += f", {s['usd_per_doc'] / baseline['usd_per_doc']:.2f} x {baseline['run']}"
    lines += [cost, f"cache hit rate {fmt(None if s['cache_hit_rate'] is None else 100 * s['cache_hit_rate'], '{:.0f}%')}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="measures of the runs of incremental.py")
    p.add_argument("runs", type=Path, nargs="+", help="run folders written by incremental.py")
    p.add_argument("--gold", type=Path, default=None, help="gold folders: JSON {file: folder}, or a manifest with a `docs` list")
    p.add_argument("--baseline", type=Path, default=None, help="a run whose cost per document the others are given as a ratio of")
    p.add_argument("--json", type=Path, default=None, help="also write the measures to this file")
    a = p.parse_args(argv)
    for run in [*a.runs, *([a.baseline] if a.baseline else [])]:
        if not (run / "docs.jsonl").exists():
            p.error(f"{run}: no docs.jsonl (not a run of incremental.py)")
    gold = load_gold(a.gold) if a.gold else None
    base = score(a.baseline, gold) if a.baseline else None
    scored = [score(run, gold) for run in a.runs]
    print("\n\n".join(report(s, base) for s in scored))
    if a.json:
        a.json.write_text(json.dumps(scored, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
