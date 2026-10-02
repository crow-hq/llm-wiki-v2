# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Serial against parallel filing: the same corpus through `ingest_many` at concurrency 1 and 4, on empty wikis.

    python benchmarks/ingest_parallel.py                      # 3 runs per side, DeepSeek V4 Flash, the demo corpus
    python benchmarks/ingest_parallel.py --runs 5 --concurrency 1 8 --out result.json
    python benchmarks/ingest_parallel.py --dry-run            # a fake model, free: to try the script

It uses your configured provider, keys and mode (the settings file and OKF_* variables, like the command line) and
costs real money. Each run gets a fresh bundle in a temporary folder. Per run it measures what the plan asks:
folders, near-duplicate folders, notes, merge rate, links, `check()` problems, wall time, cost and usage by operation.
The table shows the mean and the min-max of each side; the raw numbers go to the JSON file.

The wiki is not deterministic even when filing is serial, so a difference between the sides counts only when it
falls outside the spread of the runs of the serial side.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from llmw2 import Item, Origin, Wiki
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.tree import Folder, slugify
from llmw2.config import LLMConfig
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from llmw2.settings import Settings

HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE.parent / "src" / "llmw2" / "demo" / "raw"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"  # OpenRouter's slug for DeepSeek V4 Flash

# "Near-duplicate" folders: two folders whose slugs are alike (difflib ratio) or whose descriptions say much the same
# (share of their content words in common). Any pair in the tree counts once.
SLUG_RATIO = 0.8
WORDS_SHARED = 0.6
STOP = frozenset({"the", "and", "for", "with", "from", "that", "this", "note", "notes", "about", "into", "their", "them", "are", "were"})

METRICS = ["folders", "near_duplicate_folders", "notes", "merge_rate", "links", "check_problems", "not_done", "seconds", "cost_usd"]


def corpus_items(folder: Path) -> list[Item]:
    """Every .md of the folder as a text item, without its frontmatter and the timestamp in its name."""
    items = []
    for path in sorted(folder.glob("*.md")):
        name = re.sub(r"^\d{8}-\d{6}-", "", path.name)
        body = OKFDocument.parse(path.read_text(encoding="utf-8")).body.strip()
        items.append(Item(name, text=body, origin=Origin("benchmark", "corpus", name)))
    if not items:
        sys.exit(f"no .md files in {folder}")
    return items


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def near_duplicates(folders: list[Folder]) -> int:
    count = 0
    for i, a in enumerate(folders):
        for b in folders[i + 1 :]:
            like_name = difflib.SequenceMatcher(None, slugify(a.name), slugify(b.name)).ratio() >= SLUG_RATIO
            wa, wb = words(a.description), words(b.description)
            like_text = bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= WORDS_SHARED
            count += like_name or like_text
    return count


def measure(wiki: Wiki, report: Any, seconds: float) -> dict[str, Any]:
    folders = [f for f in wiki.tree().walk() if not f.is_root]
    results = [r for _, r in report.results if not isinstance(r, Exception)]
    made = [r for r in results if r.action in ("created", "merged")]
    usage = wiki.usage.to_dict()
    return {
        "folders": len(folders),
        "near_duplicate_folders": near_duplicates(folders),
        "notes": len(wiki.tree().all_notes()),
        "merge_rate": round(sum(r.action == "merged" for r in made) / len(made), 4) if made else 0.0,
        "links": sum(len(r.related) for r in results),
        "check_problems": len(wiki.check()),
        "not_done": len(report.not_done),
        "seconds": round(seconds, 2),
        "cost_usd": round(usage["llm"]["cost_usd"] + usage["classifier"]["cost_usd"], 6),
        "stopped": report.stopped,
        "errors": [f"{item.filename}: {r}" for item, r in report.results if isinstance(r, Exception)],
        "usage_by_op": {
            op: {k: u[k] for k in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd")} for op, u in usage["by_op"].items()
        },
    }


# -- a model that costs nothing, for --dry-run -------------------------------------------------


class DryLLM(LLM):
    """Answers every prompt from the prompt alone (so any order of calls gives the same wiki), after a short delay.

    A source is read as "topic: <group>-<n>": the note is "About <topic>", goes in a folder named after the group, and
    a second source of the same topic is merged into it. Same prompt parsing as the test fake `ContentLLM`.
    """

    def __init__(self, usage: UsageTracker, delay: float) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.delay = delay

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        time.sleep(self.delay)
        self.usage.record("llm", self.model, 100, 10, op=op, seconds=self.delay)
        return json.dumps(getattr(self, "_" + op.rpartition("/")[2])(messages[1]["content"]))

    @staticmethod
    def _between(text: str, start: str, end: str) -> str:
        head = text.rindex(start) + len(start)
        return text[head : text.index(end, head)]

    def _summarize(self, user: str) -> dict[str, Any]:
        text = self._between(user, "<source>\n", "\n</source>")
        name = re.match(r"topic: (\S+)", text).group(1)  # type: ignore[union-attr]
        return {"title": f"About {name}", "summary": f"All on {name}.", "tags": [name.split("-")[0]], "body": text}

    def _route(self, user: str) -> dict[str, Any]:
        group = self._between(user, "Title: About ", "\n").split("-")[0]
        folders = self._between(user, "Its subfolders (name: description):\n", "\n\n<note>").splitlines()
        if any(line.startswith(f"- {group}:") for line in folders):
            return {"action": "descend", "subfolder": group}
        return {"action": "new" if "(root of the wiki)" in user else "here"}

    def _name_folder(self, user: str) -> dict[str, Any]:
        group = self._between(user, "Title: About ", "\n").split("-")[0]
        return {"name": group, "description": f"Notes on {group}."}

    def _find_match(self, user: str) -> dict[str, Any]:
        title = self._between(user, "Title: ", "\n")
        notes = self._between(user, "<notes>\n", "\n</notes>").splitlines()
        return {"match": next((line[2:].split(":", 1)[0] for line in notes if f": {title} — " in line), None)}

    def _consolidate(self, user: str) -> dict[str, Any]:
        return {"decision": "modify"}

    def _merge(self, user: str) -> dict[str, Any]:
        title = self._between(user, "Existing note\nTitle: ", "\n")
        summary = self._between(user, "\nSummary: ", "\n")
        body = self._between(user, "<existing>\n", "\n</existing>")
        return {"title": title, "summary": summary, "tags": [], "body": body + "\n\n" + self._between(user, "<source>\n", "\n</source>")}

    def _relate(self, user: str) -> dict[str, Any]:
        notes = self._between(user, "<notes>\n", "\n</notes>").splitlines()
        return {"related": [line[2:].split(":", 1)[0] for line in notes if line.startswith("- ")]}


def dry_items(items: list[Item]) -> list[Item]:
    """The corpus as the fake model reads it: two documents to a topic, grouped by the first word of their name."""
    return [Item(i.filename, text=f"topic: {i.filename.split('-')[0]}-{n // 2}\n{i.text}", origin=i.origin) for n, i in enumerate(items)]


# -- one run, and the report ---------------------------------------------------------------------


def run_once(items: list[Item], concurrency: int, model: str, dry_run: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="llmw2-bench-") as tmp:
        settings = Settings(bundle=Path(tmp) / "wiki", concurrency=concurrency)
        cfg = settings.config()
        if dry_run:
            cfg.mode = "classic"
            wiki = Wiki(cfg, llm=DryLLM(UsageTracker(), delay=0.05))
            items = dry_items(items)
        else:
            cfg.llm.model = model
            wiki = Wiki(cfg)
        with wiki:
            wiki.init()
            start = time.perf_counter()
            report = wiki.ingest_many(items)
            return measure(wiki, report, time.perf_counter() - start)


def spread(values: list[float]) -> str:
    mean, low, high = statistics.fmean(values), min(values), max(values)
    return f"{mean:.4g} ({low:.4g}-{high:.4g})"


def table(sides: dict[int, list[dict[str, Any]]]) -> str:
    ks = list(sides)
    rows = [["metric", *(f"K={k}  mean (min-max)" for k in ks)]]
    rows += [[m, *(spread([float(r[m]) for r in sides[k]]) for k in ks)] for m in METRICS]
    ops = sorted({op for runs in sides.values() for r in runs for op in r["usage_by_op"]})
    for field in ("calls", "seconds", "cost_usd"):
        rows += [[f"{field} {op}", *(spread([r["usage_by_op"].get(op, {}).get(field, 0) for r in sides[k]]) for k in ks)] for op in ops]
    width = [max(len(row[c]) for row in rows) for c in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, width, strict=True)) for row in rows]
    return "\n".join([lines[0], "  ".join("-" * w for w in width), *lines[1:]])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="serial against parallel filing")
    p.add_argument("--runs", type=int, default=3, help="runs per side (default 3)")
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 4], metavar="K", help="the sides (default: 1 4)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"the LLM (default {DEFAULT_MODEL}, on the configured provider)")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="a folder of .md files (default: the demo's raw copies)")
    p.add_argument("--out", type=Path, default=Path("ingest_parallel.json"), help="where the raw results go as JSON")
    p.add_argument("--dry-run", action="store_true", help="a fake model that costs nothing, to try the script")
    args = p.parse_args(argv)

    items = corpus_items(args.corpus)
    print(f"{len(items)} documents, {args.runs} runs at each of K={args.concurrency}, model {'fake' if args.dry_run else args.model}")
    sides: dict[int, list[dict[str, Any]]] = {k: [] for k in args.concurrency}
    for n in range(args.runs):  # alternate the sides, so a slow hour at the provider does not fall on one of them
        for k in args.concurrency:
            result = run_once(items, k, args.model, args.dry_run)
            sides[k].append(result)
            print(f"run {n + 1} K={k}: {result['seconds']}s, ${result['cost_usd']}, {result['notes']} notes, {result['folders']} folders")
            if result["stopped"]:
                print(f"  stopped: {result['stopped']} ({result['not_done']} not done)", file=sys.stderr)
    print()
    print(table(sides))
    args.out.write_text(json.dumps({str(k): runs for k, runs in sides.items()}, indent=2), encoding="utf-8")
    print(f"\nraw results: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
