# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which planted facts survive into the final notes: the wikis kept by `stability.py --keep`, read against the labels.

    python benchmarks/note_facts.py --corpus benchmarks/corpus/long2 --keep K1 K2 --raw R1.json R2.json [--note-chars 10000] [--out F.json]

Free: no model is called. `--corpus` is the folder of a corpus whose labels.json has `facts` (repeat it for more corpora: their
documents are the sources of the notes), `--keep` the folders written by `stability.py --keep` and `--raw` the JSON of the same
runs, one per `--keep` in the same order (they say in which note each document ended up). `--base` is the wiki the runs started
from (default the demo), whose notes are not inventions when a number comes from them.

Facts are searched in the body of the final note (frontmatter and See-also left out) after a normalisation: lower case, spaces and
thousands separators removed (a group is exactly three digits, so "Q2 2026" is not "2 202" and "6"), "," and "." equivalent
as decimal marks, currency symbols ignored ("€3.85", "3,85 €" and "3.85" are the same), names and codes
matched without regard to spaces. Per variant, the mean over its runs:
- fact_recall, in all and by kind (headline, marginal), by position (begin, middle, tail) and beyond_100k;
- superseded_ok: facts that replace an older value, with the new value in the note; superseded_kept: with the old one too
  (expected, as "previously");
- invented_numbers: numbers of the note (at least two digits) that are neither in the sources filed into it nor in the note
  it started from, per note; note_chars: the length of the body, and over_cap, the share of notes longer than 1.2 times `--note-chars`.
- headline recall is the primary measure: marginal recall depends on the note's length, and a copy of the source finds facts by
  copying them. So recall is read split by summarized and copied notes (recall_headline_summarized, recall_headline_copied and the
  same for marginal), next to note_ratio (the note's length against its sources), verbatim (the share of its 8-word shingles that
  are in its sources) and copied_share (the share of notes with a verbatim of 0.3 or more).
The notes counted are those that received a document of the corpora, once each in a run. A document with no note counts as
not found.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from llmw2.bundle.document import OKFDocument, OKFDocumentError  # noqa: E402
from llmw2.bundle.tree import split_see_also  # noqa: E402

DEMO = HERE.parent / "src" / "llmw2" / "demo"
OVER = 1.2  # a note longer than this times the cap is over it

GAP = " " + chr(0xA0) + chr(0x202F)  # the spaces that group thousands: plain, no-break, narrow no-break
NUMBER = re.compile(rf"(?<![\w-])\d{{1,3}}(?:[{GAP}.,]\d{{3}}(?!\d))+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
SEPARATORS = re.compile(rf"[{GAP}.,]")
CURRENCY = re.compile(r"[€$£]|\b(?:eur|euros?)\b")
MARKS = ("headline", "marginal")
POSITIONS = ("begin", "middle", "tail")
SHINGLE = 8  # words per shingle
COPIED = 0.3  # a note is a copy when at least this share of its shingles is in its sources
FIRST = (  # the rows of the report that come first
    "recall_headline",
    "recall_headline_summarized",
    "recall_headline_copied",
    "note_ratio",
    "copied_share",
    "recall_marginal",
    "recall_marginal_summarized",
)


# -- the normalisation ---------------------------------------------------------------------------------


def _number(match: re.Match[str]) -> str:
    parts = SEPARATORS.split(match.group())
    if len(parts) > 1 and len(parts[-1]) != 3:  # the last group is not a thousands one: it is the decimals
        return "".join(parts[:-1]) + "." + parts[-1]
    return "".join(parts)


def normalize(text: str) -> str:
    """Lower case, currency symbols out, every number as digits with "." for the decimals ("3,85" and "1.234,5" too)."""
    return NUMBER.sub(_number, CURRENCY.sub(" ", text.lower()))


def has_fact(text: str, fact: str) -> bool:
    """Whether `fact` is in `text` after the normalisation, whatever the spaces, and not inside a longer word or number."""
    wanted = "".join(normalize(fact).split())
    if not wanted:
        return False
    pattern = r"(?<![a-z0-9])" + r"\s*".join(re.escape(c) for c in wanted) + r"(?![a-z0-9])"
    return re.search(pattern, normalize(text)) is not None


def numbers(text: str) -> set[str]:
    """The numbers of a text with at least two digits, normalised (a code such as "AJP-5870" gives 5870)."""
    return {n for n in re.findall(r"\d+(?:\.\d+)?", normalize(text)) if sum(c.isdigit() for c in n) >= 2}


def invented(body: str, references: Iterable[str]) -> set[str]:
    """The numbers of `body` that are in none of the `references`."""
    known: set[str] = set()
    for text in references:
        known |= numbers(text)
    return numbers(body) - known


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    """The runs of `n` consecutive words of a text (lower case), each joined by a space."""
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def verbatim_share(body: str, sources: Iterable[str], n: int = SHINGLE) -> float:
    """The share of the shingles of `body` that are in the `sources`: 0.0 for a body with none."""
    mine = shingles(body, n)
    known: set[str] = set()
    for text in sources:
        known |= shingles(text, n)
    return len(mine & known) / len(mine) if mine else 0.0


# -- the facts of one note ---------------------------------------------------------------------------------


def check_facts(body: str | None, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per fact of a document: its labels and whether it is in the note `body` (None: no note, nothing is found).

    `old_found` is for the facts with `supersedes`: whether the replaced value is in the note too.
    """
    out = []
    for f in facts:
        found = body is not None and has_fact(body, f["fact"])
        record = {"fact": f["fact"], "kind": f["kind"], "position": f["position"], "beyond_100k": bool(f["beyond_100k"]), "found": found}
        if f.get("supersedes"):
            record["old_found"] = body is not None and has_fact(body, f["supersedes"])
        out.append(record)
    return out


def share(hits: list[bool]) -> float | None:
    return sum(hits) / len(hits) if hits else None


def recall_metrics(records: list[dict[str, Any]]) -> dict[str, float | None]:
    """The recall metrics of fact records (of one run, or of one document)."""
    out: dict[str, float | None] = {"fact_recall": share([r["found"] for r in records])}
    for kind in MARKS:
        out[f"recall_{kind}"] = share([r["found"] for r in records if r["kind"] == kind])
    for position in POSITIONS:
        out[f"recall_{position}"] = share([r["found"] for r in records if r["position"] == position])
    out["recall_beyond_100k"] = share([r["found"] for r in records if r["beyond_100k"]])
    changed = [r for r in records if "old_found" in r]
    out["superseded_ok"] = share([r["found"] for r in changed])
    out["superseded_kept"] = share([r["old_found"] for r in changed])
    for kind in MARKS:
        for copied, name in ((False, "summarized"), (True, "copied")):
            out[f"recall_{kind}_{name}"] = share([r["found"] for r in records if r["kind"] == kind and r.get("copied") is copied])
    return out


def note_metrics(notes: list[dict[str, Any]], cap: int | None) -> dict[str, float | None]:
    """invented_numbers, note_chars, over_cap and the length and copy measures of notes `{"chars", "invented", ...}`: one record per note.

    `ratio`, `verbatim` and `copied` are optional in a record: without them note_ratio, verbatim and copied_share are None.
    """
    ratios = [n["ratio"] for n in notes if n.get("ratio") is not None]
    verbatims = [n["verbatim"] for n in notes if n.get("verbatim") is not None]
    copies = [n["copied"] for n in notes if "copied" in n]
    return {
        "invented_numbers": statistics.fmean(n["invented"] for n in notes) if notes else None,
        "note_chars": statistics.fmean(n["chars"] for n in notes) if notes else None,
        "over_cap": share([n["chars"] > OVER * cap for n in notes]) if cap else None,
        "note_ratio": statistics.fmean(ratios) if ratios else None,
        "verbatim": statistics.fmean(verbatims) if verbatims else None,
        "copied_share": share(copies),
    }


def mean_of_runs(per_run: list[dict[str, float | None]]) -> dict[str, float | None]:
    """The mean of each metric over the runs that have a value for it."""
    names = {k for run in per_run for k in run}
    return {k: statistics.fmean(v) if (v := [run[k] for run in per_run if run.get(k) is not None]) else None for k in sorted(names)}


# -- reading the runs ------------------------------------------------------------------------------------


def note_body(wiki: Path, rel: str | None) -> str | None:
    """The body of a note of a kept wiki without frontmatter and See-also; None if there is none or it is not a note."""
    if not rel or not (path := wiki / rel).is_file():
        return None
    try:
        return split_see_also(OKFDocument.parse(path.read_text(encoding="utf-8")).body)[0]
    except OKFDocumentError:
        return None


def read_facts(corpora: list[Path]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """The facts of every document of the corpora (the labels with `facts`) and the text of every document."""
    facts: dict[str, list[dict[str, Any]]] = {}
    texts: dict[str, str] = {}
    for folder in corpora:
        for name, label in json.loads((folder / "labels.json").read_text(encoding="utf-8"))["docs"].items():
            if label.get("facts"):
                facts[name] = label["facts"]
            if (folder / name).is_file():
                texts[name] = (folder / name).read_text(encoding="utf-8")
    if not facts:
        sys.exit("no labels.json with facts in " + ", ".join(str(c) for c in corpora))
    return facts, texts


def score_run(
    wiki: Path, docs: dict[str, dict[str, Any]], facts: dict[str, list[dict[str, Any]]], texts: dict[str, str], base: Path, cap: int | None
) -> dict[str, Any]:
    """One kept wiki: the fact records per document, the notes' records, and the metrics of the run.

    `docs` is the `docs` of the run in the raw JSON of stability.py: `docs[filename]["note"]` is the note of a document.
    """
    sources: dict[str, list[str]] = defaultdict(list)  # note -> the texts filed into it
    for name, record in docs.items():
        if record.get("note") and name in texts:
            sources[record["note"]].append(texts[name])
    notes = []
    for rel in sorted(sources):
        if (body := note_body(wiki, rel)) is not None:
            earlier = base / rel
            references = sources[rel] + ([earlier.read_text(encoding="utf-8")] if earlier.is_file() else [])
            source_chars = sum(len(t) for t in sources[rel])
            verbatim = verbatim_share(body, sources[rel])
            ratio = len(body) / source_chars if source_chars else None
            notes.append(
                {
                    "note": rel,
                    "chars": len(body),
                    "invented": len(invented(body, references)),
                    "source_chars": source_chars,
                    "ratio": ratio,
                    "verbatim": verbatim,
                    "copied": verbatim >= COPIED,
                }
            )
    copied = {n["note"]: n["copied"] for n in notes}
    per_doc: dict[str, list[dict[str, Any]]] = {}
    for name, doc_facts in facts.items():
        if name in docs:
            rel = docs[name].get("note", "")
            per_doc[name] = [{**r, "copied": copied.get(rel, False)} for r in check_facts(note_body(wiki, rel), doc_facts)]
    return {
        "docs": per_doc,
        "notes": notes,
        "metrics": {**recall_metrics([r for records in per_doc.values() for r in records]), **note_metrics(notes, cap)},
    }


def summarize_variant(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean metrics of the runs of a variant, and per document the recall and the superseded values, over the runs."""
    per_doc = {}
    for name in sorted({d for run in runs for d in run["docs"]}):
        mine = [run["docs"][name] for run in runs if name in run["docs"]]
        per_doc[name] = mean_of_runs([recall_metrics(records) for records in mine])
    return {"metrics": mean_of_runs([run["metrics"] for run in runs]), "per_doc": per_doc, "runs": len(runs)}


# -- the report -----------------------------------------------------------------------------------------


def cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.3g}"


def grid(rows: list[list[str]]) -> str:
    width = [max(len(row[c]) for row in rows) for c in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, width, strict=True)) for row in rows]
    return "\n".join([lines[0], "  ".join("-" * w for w in width), *lines[1:]])


def report_text(variants: dict[str, dict[str, Any]]) -> str:
    """A table of metrics with a column per variant, then of the recall per document."""
    names = list(variants)
    present = next(iter(variants.values()))["metrics"]
    metrics = [*(m for m in FIRST if m in present), *(m for m in present if m not in FIRST)]
    out = [grid([["metric", *names], *([m, *(cell(variants[v]["metrics"].get(m)) for v in names)] for m in metrics)])]
    docs = sorted({d for v in variants.values() for d in v["per_doc"]})
    for metric in ("fact_recall", "recall_headline"):
        recall = [[d, *(cell(variants[v]["per_doc"].get(d, {}).get(metric)) for v in names)] for d in docs]
        out += ["", f"{metric} per document", grid([["document", *names], *recall])]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="planted facts that survive into the notes of the kept wikis")
    p.add_argument(
        "--corpus", type=Path, action="append", required=True, help="a corpus folder with labels.json that has facts; repeat it for more"
    )
    p.add_argument("--keep", type=Path, nargs="+", required=True, help="folders written by stability.py --keep")
    p.add_argument("--raw", type=Path, nargs="+", required=True, help="the stability.py JSON of each --keep, in the same order")
    p.add_argument("--base", type=Path, default=DEMO, help="the wiki the runs started from (default the demo)")
    p.add_argument("--note-chars", type=int, default=None, help="the cap of a note: the share of notes over 1.2 times it is reported")
    p.add_argument("--out", type=Path, default=Path("note_facts.json"), help="where the numbers go as JSON")
    args = p.parse_args(argv)
    if len(args.keep) != len(args.raw):
        sys.exit("--keep and --raw must have the same number of paths")
    facts, texts = read_facts(args.corpus)
    variants: dict[str, dict[str, Any]] = {}
    for keep, raw_file in zip(args.keep, args.raw, strict=True):
        raw = json.loads(raw_file.read_text(encoding="utf-8"))
        for variant, record in raw.get("variants", {}).items():
            label = f"{keep.name}:{variant}" if len(args.keep) > 1 else variant
            runs = []
            for k, run in enumerate(record["runs"], 1):
                wiki = keep / variant / f"run{k}"
                if not wiki.is_dir():
                    sys.exit(f"{wiki} is missing: was stability.py run with --keep {keep}?")
                runs.append(score_run(wiki, run["docs"], facts, texts, args.base, args.note_chars))
            variants[label] = summarize_variant(runs)
    if not variants:
        sys.exit("no variants in the raw files")
    print(report_text(variants))
    args.out.write_text(json.dumps(variants, indent=2), encoding="utf-8")
    print(f"\nnumbers: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
