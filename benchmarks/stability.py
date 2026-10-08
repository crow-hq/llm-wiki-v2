# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Stability of the filing decisions: the same documents filed again and again, and how often the wiki comes out the same.

    python benchmarks/stability.py                              # scenario B, variant base, 5 runs: the growth corpus into the demo wiki
    python benchmarks/stability.py --variant base t0seed pin    # one table per variant, the runs of all of them interleaved
    python benchmarks/stability.py --scenario a --runs 3        # scenario A: the demo's raw documents into an empty wiki
    python benchmarks/stability.py --dry-run --runs 2           # a fake model, free: to try the script

It uses your configured provider, keys and mode (the settings file and OKF_* variables, like the command line) and
costs real money; the estimate is printed before the first run. Each run gets a fresh bundle in a temporary folder.

Scenarios: B (default) copies `src/llmw2/demo` and files `benchmarks/corpus/growth/` into it (`labels.json` beside the
documents says where each belongs); A starts from an empty wiki and files the demo's raw documents.
Variants: `base` is your config; `t0seed` sets temperature 0 and seed 42; `pin` adds `--pin` providers on top;
`vote` is your config with 3 classifier calls averaged on the borderline routing decisions.

Variants of one run: `--votes`, `--route-reads`, `--prompts DIR` (prompts overriding the packaged ones, file by file) and
`--fallback-menu visited tree` (one value: for every variant; several: one variant `menu-<name>` each, alternated),
`--base DIR` (the wiki scenario B starts from), `--merge-reads note|source` (what a merge reads: the incoming note, or
the source as before) and
`--keep DIR` (the final wiki of each run is copied to DIR/<variant>/run<k>/, for benchmarks/note_facts.py). Several corpora:
`--corpus` repeated (each with its labels.json), `--only PATTERN...` keeps the
documents whose "<corpus folder>/<filename>" matches a glob. `--cache llm classifier` replays the answers of a model already
asked the same thing (`--cache-ops` limits it to some operations, `--cache-file` says where they are kept). `--recompute FILE...`
recomputes the metrics of saved results with the labels of `--corpus`, free, and prints their reports.

Between the runs of one variant, over all pairs of runs (mean and min):
- pair_agreement: adjusted Rand index of the folder assignments (a new folder counts by who shares it, not by name);
- outcome_agreement: share of documents with the same outcome (created, merged into a note, merged with an earlier document);
- set_agreement (B): share of documents in an acceptable folder in every run (documents of kind "new" left out).
Against the labels (B), per run: folder, merge, new-folder and group accuracy, the false merges and the folders created
for one document only. Diagnostics: the documents whose
folder changes between runs, with the route's deciding party and confidence, the providers that answered, and the
quality metrics of ingest_parallel.py. The raw numbers go to the JSON file.
"""

from __future__ import annotations

import argparse
import fnmatch
import itertools
import json
import math
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the scripts of this folder import each other, however this one is started
# The library of this same tree, not the one the venv installed as editable (another checkout: a worktree run would
# measure the main tree's code, even while it is being edited).
sys.path.insert(1, str(HERE.parent / "src"))

from ingest_parallel import DEFAULT_MODEL, DryLLM, corpus_items, dry_items, measure  # noqa: E402
from replay import Replay  # noqa: E402

from llmw2 import CLASSIC, CROW, Candidate, Item, ModelError, Origin, Source, StepContext, Wiki  # noqa: E402
from llmw2.agents import steps_classic  # noqa: E402
from llmw2.models.classifier import Classifier  # noqa: E402
from llmw2.models.llm import LLM  # noqa: E402
from llmw2.models.usage import UsageTracker  # noqa: E402

if not Path(sys.modules["llmw2"].__file__ or "").resolve().is_relative_to((HERE.parent / "src").resolve()):
    sys.exit(f"llmw2 comes from {sys.modules['llmw2'].__file__}, not from {HERE.parent / 'src'}: run with PYTHONPATH={HERE.parent / 'src'}")
from llmw2.settings import Settings  # noqa: E402

DEMO = HERE.parent / "src" / "llmw2" / "demo"
MENUS = ("visited", "tree")
GROWTH = HERE / "corpus" / "growth"
DEFAULT_PIN = ["DeepInfra", "Novita"]  # fp8 providers of deepseek-v4-flash that support the seed (OpenRouter's endpoints list, 2026-10)
VARIANTS = ("base", "t0seed", "pin", "vote")
SEED = 42
FIRST_DAY = datetime(2026, 1, 1)
COST_PER_DOC = 0.00017  # USD: about $0.005 for a 30-document run of deepseek-v4-flash in CROW mode (the plan's measure)

QUALITY = ["folders", "near_duplicate_folders", "notes", "merge_rate", "links", "check_problems", "not_done", "seconds", "cost_usd"]

Outcome = tuple[str, ...]  # ("created",), ("merged", "customers/refund-policy.md"), ("merged-with", "04-new-thing.md")


# -- the metrics ---------------------------------------------------------------------------------


def pairs(n: int) -> float:
    return n * (n - 1) / 2


def ari(a: dict[str, str], b: dict[str, str]) -> float:
    """Adjusted Rand index of two folder assignments of the same documents (those in both): 1 for the same partition.

    The labels are arbitrary: only who shares a folder with whom counts. Two partitions that are the same, the
    degenerate ones (everything together, everything apart) included, give 1.0. Fewer than two documents: 1.0.
    """
    docs = sorted(a.keys() & b.keys())
    if len(docs) < 2:
        return 1.0
    together = sum(pairs(c) for c in Counter((a[d], b[d]) for d in docs).values())
    in_a, in_b = sum(pairs(c) for c in Counter(a[d] for d in docs).values()), sum(pairs(c) for c in Counter(b[d] for d in docs).values())
    expected, best = in_a * in_b / pairs(len(docs)), (in_a + in_b) / 2
    return 1.0 if math.isclose(best, expected) else (together - expected) / (best - expected)


def outcome_agreement(a: dict[str, tuple[str, ...]], b: dict[str, tuple[str, ...]]) -> float:
    """Share of the documents in both runs whose outcome is identical."""
    docs = a.keys() & b.keys()
    return sum(tuple(a[d]) == tuple(b[d]) for d in docs) / len(docs) if docs else 1.0


def set_agreement(runs: list[dict[str, str]], labels: dict[str, dict[str, Any]]) -> float:
    """Share of the labelled documents (kind not "new") whose folder is an acceptable one in every run.

    `runs`: one {filename: folder label} per run; `labels`: the "docs" of labels.json.
    """
    docs = [d for d, label in labels.items() if label["kind"] != "new" and all(d in run for run in runs)]
    return sum(all(run[d] in labels[d]["folders"] for run in runs) for d in docs) / len(docs) if docs else 1.0


def names(value: Any) -> list[str]:
    """A label's `merge_into` or `merge_ok` as a list: none is empty, a string is one note."""
    return [] if not value else [value] if isinstance(value, str) else list(value)


def accuracy(run: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, float]:
    """One run against the labels (the "docs" of labels.json).

    `run` is a run record; only these keys are read: `run["docs"][filename]` with `"folder"` (label such as "/customers")
    and `"outcome"` (a tuple as in `Outcome`), and `run["existing_folders"]`, the labels of the folders the wiki had
    before the run. Returns, each from 0 to 1 (`single_doc_folders` is a count):
    - folder_accuracy: documents (kind not "new") in an acceptable folder;
    - merge_accuracy: "update" documents merged into a note of their `merge_into`, or into the note that their
      `merge_with_doc` created in this run, or merged with that document, or into the note it was merged into in
      this run (`run["docs"][merge_with_doc]["note"]`);
    - false_merge_rate: documents not of kind "update" merged where they may not be (the notes of `merge_into` and
      `merge_ok`, or the note of `merge_with_doc`);
    - group_accuracy: groups of documents whose members all share one folder that the wiki did not have before;
    - new_accuracy: "new" documents in a folder that the wiki did not have before (and that is not an error);
    - new_alt_rate: "new" documents in a folder of their `alt_folders` (existing folders accepted, counted apart; 0.0 with no "new");
    - folder_prefix_accuracy: like folder_accuracy, but a folder below an acceptable one is right too (a subfolder the
      run created under it);
    - update_merged_or_linked: "update" documents merged as for merge_accuracy, or created with a note of their
      `merge_into` among the `related` of `run["docs"][filename]` (a record without `related` counts only the merges);
    - single_doc_folders: folders created in the run that hold exactly one document of it.
    A share with nothing to count is 1.0 (0.0 for the false merges).
    """
    docs = {d: run["docs"][d] for d in labels if d in run["docs"]}
    before = set(run["existing_folders"])

    def share(hits: list[bool]) -> float:
        return sum(hits) / len(hits) if hits else 1.0

    def target(d: str) -> tuple[str, str] | None:
        """How a document was merged: ("merged", note) or ("merged-with", document); None if it was not."""
        outcome = docs[d]["outcome"]
        return (outcome[0], outcome[1]) if outcome[0] in ("merged", "merged-with") and len(outcome) > 1 else None

    def fits(d: str, allowed: list[str]) -> bool:
        merged = target(d)
        if merged is None:
            return False
        wanted = labels[d].get("merge_with_doc")
        if merged[0] == "merged-with":
            return merged[1] == wanted
        # a merge into a note: allowed, or the note where the document `merge_with_doc` ended up in this run
        return merged[1] in allowed or bool(wanted and merged[1] == run["docs"].get(wanted, {}).get("note"))

    def created(folder: str) -> bool:
        return folder != "!error" and folder not in before

    folder = [docs[d]["folder"] in labels[d]["folders"] for d in docs if labels[d]["kind"] != "new"]
    below = [
        any(docs[d]["folder"] == f or docs[d]["folder"].startswith(f.rstrip("/") + "/") for f in labels[d]["folders"])
        for d in docs if labels[d]["kind"] != "new"
    ]
    updates = [fits(d, names(labels[d].get("merge_into"))) for d in docs if labels[d]["kind"] == "update"]

    def linked(d: str) -> bool:
        return docs[d]["outcome"][0] == "created" and bool(set(docs[d].get("related", [])) & set(names(labels[d].get("merge_into"))))

    merged_or_linked = [fits(d, names(labels[d].get("merge_into"))) or linked(d) for d in docs if labels[d]["kind"] == "update"]
    others = [target(d) is not None and not fits(d, names(labels[d].get("merge_into")) + names(labels[d].get("merge_ok")))
              for d in docs if labels[d]["kind"] != "update"]
    groups: dict[str, set[str]] = {}
    for d in docs:
        if labels[d].get("group"):
            groups.setdefault(labels[d]["group"], set()).add(docs[d]["folder"])
    grouped = [len(folders) == 1 and not folders & before for folders in groups.values()]
    fresh = [created(docs[d]["folder"]) for d in docs if labels[d]["kind"] == "new"]
    alt = [docs[d]["folder"] in labels[d].get("alt_folders", []) for d in docs if labels[d]["kind"] == "new"]
    per_folder = Counter(v["folder"] for v in run["docs"].values() if created(v["folder"]))
    return {
        "folder_accuracy": share(folder),
        "folder_prefix_accuracy": share(below),
        "merge_accuracy": share(updates),
        "update_merged_or_linked": share(merged_or_linked),
        "false_merge_rate": sum(others) / len(others) if others else 0.0,
        "group_accuracy": share(grouped),
        "new_accuracy": share(fresh),
        "new_alt_rate": sum(alt) / len(alt) if alt else 0.0,
        "single_doc_folders": float(sum(count == 1 for count in per_folder.values())),
    }


def between_runs(runs: list[dict[str, Any]], labels: dict[str, dict[str, Any]] | None) -> dict[str, list[float]]:
    """Each metric as the list of its values, over all pairs of runs (or over the runs, for the per-run ones)."""
    folders = [{d: v["folder"] for d, v in run["docs"].items()} for run in runs]
    outcomes = [{d: tuple(v["outcome"]) for d, v in run["docs"].items()} for run in runs]
    out: dict[str, list[float]] = {
        "pair_agreement": [ari(a, b) for a, b in itertools.combinations(folders, 2)],
        "outcome_agreement": [outcome_agreement(a, b) for a, b in itertools.combinations(outcomes, 2)],
    }
    if labels:
        out["set_agreement"] = [set_agreement(folders, labels)]
        for run in runs:
            for name, value in accuracy(run, labels).items():
                out.setdefault(name, []).append(value)
    for name in QUALITY:
        out[name] = [float(run["quality"][name]) for run in runs]
    return out


def unstable(runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The documents whose folder is not the same in every run: per document, per run, its folder and route decision."""
    out = {}
    for doc in sorted(runs[0]["docs"]):
        if len({run["docs"].get(doc, {}).get("folder") for run in runs}) > 1:
            out[doc] = [{"folder": run["docs"].get(doc, {}).get("folder"), "route": run["docs"].get(doc, {}).get("route")} for run in runs]
    return out


# -- the corpora -----------------------------------------------------------------------------------


def corpus_labels(folder: Path) -> dict[str, dict[str, Any]]:
    """The labels of a corpus folder; a group is prefixed with the folder's name, so those of two corpora never mix."""
    labels = json.loads((folder / "labels.json").read_text(encoding="utf-8"))["docs"]
    return {d: {**v, "group": f"{folder.name}:{v['group']}"} if v.get("group") else v for d, v in labels.items()}


def selected(folder: Path, name: str, only: list[str] | None) -> bool:
    """Whether "<corpus folder>/<filename>" matches one of the `--only` globs (all do, when there are none)."""
    return not only or any(fnmatch.fnmatchcase(f"{folder.name}/{name}", pattern) for pattern in only)


def growth_items(
    folders: list[Path], only: list[str] | None = None
) -> tuple[list[Item], dict[str, dict[str, Any]]]:
    """The documents of the corpora and their labels. Dated one minute apart, so the batch files them in name order."""
    labels: dict[str, dict[str, Any]] = {}
    found: list[tuple[Path, Item]] = []
    for folder in folders:
        mine, items = corpus_labels(folder), corpus_items(folder)
        if missing := sorted({i.filename for i in items} ^ mine.keys()):
            sys.exit(f"documents and labels.json do not match in {folder}: {', '.join(missing)}")
        if twice := sorted(mine.keys() & labels.keys()):
            sys.exit(f"the same filename in two corpora: {', '.join(twice)}")
        labels.update(mine)
        found += [(folder, i) for i in items]
    items = []
    for n, (folder, i) in enumerate(found):  # dated before the selection, so a document keeps its date whatever is chosen
        origin = Origin("benchmark", folder.name, i.filename, modified=(FIRST_DAY + timedelta(minutes=n)).isoformat())
        if selected(folder, i.filename, only):
            items.append(Item(i.filename, text=i.text, origin=origin))
    if not items:
        sys.exit(f"--only {' '.join(only or [])} matches no document")
    return items, {d: labels[d] for d in (i.filename for i in items)}


def raw_items(folders: list[Path], only: list[str] | None = None) -> list[Item]:
    """The documents of the corpora, without labels (scenario A)."""
    items: dict[str, Item] = {}
    for folder in folders:
        for i in corpus_items(folder):
            if i.filename in items:
                sys.exit(f"the same filename in two corpora: {i.filename}")
            if selected(folder, i.filename, only):
                items[i.filename] = i
    if not items:
        sys.exit(f"--only {' '.join(only or [])} matches no document")
    return list(items.values())


def growth_dry_items(items: list[Item], labels: dict[str, dict[str, Any]], run: int) -> list[Item]:
    """The corpus as the fake model reads it: a topic per group or acceptable folder, two documents to a topic.

    One document in seven goes to a topic of its own, a different one in every run, so the runs differ.
    """
    seen: Counter[str] = Counter()
    out = []
    for n, item in enumerate(items):
        label = labels[item.filename]
        slug = (label.get("group") or label["folders"][0].strip("/").split("/")[0] or "root").replace("-", "")
        slug += "x" if (n + run) % 7 == 0 else ""
        out.append(Item(item.filename, text=f"topic: {slug}-{seen[slug] // 2}\n{item.text}", origin=item.origin))
        seen[slug] += 1
    return out


# -- one run ---------------------------------------------------------------------------------------


def outcomes_of(report: Any, existing_notes: set[str]) -> dict[str, dict[str, Any]]:
    """Per document, in the order they were filed: folder, action, note, outcome and the decisions that led to it."""
    docs: dict[str, dict[str, Any]] = {}
    made: dict[str, str] = {}  # note filed in this run -> the first document that created it
    for item, result in sorted(report.results, key=lambda r: r[0].filename):
        name = item.filename
        if isinstance(result, Exception):
            docs[name] = {"folder": "!error", "action": "error", "note": "", "outcome": ["error"], "decisions": [], "error": str(result)}
            continue
        if result.action == "created":
            made[result.note] = name
            outcome = ["created"]
        elif result.action == "merged":
            outcome = ["merged", result.note] if result.note in existing_notes else ["merged-with", made.get(result.note, "?")]
        else:
            outcome = ["unchanged"]
        decisions = [
            {
                "step": d.step, "decider": d.decider, "choice": d.choice, "confidence": d.confidence, "fallback": d.fallback,
                "scores": dict(getattr(d, "scores", {})),  # getattr: main (before task 6) has no scores
                **({"folder": d.choice.split(":", 1)[0]} if d.step == "route_step" else {}),  # where the Choice was asked
            }
            for d in result.decisions
        ]
        route = [d for d in decisions if d["step"] == "route"]
        docs[name] = {
            "folder": result.folder, "action": result.action, "note": result.note, "outcome": outcome, "decisions": decisions,
            "route": route[-1] if route else None,
            "related": list(getattr(result, "related", None) or []),
        }
    return docs


def providers_of(log: Path) -> dict[str, int]:
    """Calls of the LLM per provider that answered, from the usage log."""
    counts: Counter[str] = Counter()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            call = json.loads(line)
            if call["kind"] == "llm":
                counts[call.get("provider") or "unknown"] += 1
    return dict(counts)


def _summary_state(ctx: StepContext, source: Source) -> Candidate:
    """The classic summarize, with the routing state set to the LLM's summary: how routing read before `Candidate.state`."""
    cand = steps_classic.summarize(ctx, source)
    cand.state = cand.compact(ctx.cfg.classifier.state_chars)
    return cand


def _merge_from_source(ctx: StepContext, note: Any, source: Source) -> Any:
    """The classic merge reading the source and not the incoming note: how a merge read before `StepContext.candidate`."""
    saved = getattr(ctx, "candidate", None)
    ctx.candidate = None  # type: ignore[attr-defined]  # a field after the note-to-note merge, a plain attribute before it
    try:
        return steps_classic.merge(ctx, note, source)
    finally:
        ctx.candidate = saved  # type: ignore[attr-defined]


def _without_length_rule(merge: Any) -> Any:
    """A merge step asked without the length rule of `shared/note_rules` (the rule stays everywhere else)."""

    def step(ctx: StepContext, note: Any, source: Source) -> Any:
        values = ctx._values
        ctx._values = lambda v: {**values(v), "length_rule": ""}  # type: ignore[method-assign]
        try:
            return merge(ctx, note, source)
        finally:
            del ctx._values  # back to the method

    return step


def _consolidate_from_source(ctx: StepContext, note: Any, cand: Candidate) -> bool:
    """CROW's consolidate reading the routing state (the source's) and not the candidate's compact form; classic without a classifier."""
    classifier = ctx.classifier
    if classifier is None:  # a dry run (classic mode): the classic step, as CROW falls back on an error
        return steps_classic.consolidate(ctx, note, cand)
    room = max(classifier.request_chars // 2, 1)
    state = f"{ctx.route_state(cand)}\n\n--- Existing note ---\n{note.full_text()[:room]}"
    text = ctx.prompts.render_sections("classifier/consolidate")
    modify, new_note = steps_classic.MODIFY, steps_classic.NEW_NOTE
    try:
        answer = classifier.choice(state, text["instructions"], {modify: text[modify], new_note: text[new_note]}, op="consolidate")
    except ModelError:
        ctx.decide("consolidate", "classifier", "error", fallback=True)
        return steps_classic.consolidate(ctx, note, cand)
    merged = answer.choice == modify and answer.confidence >= ctx.cfg.crow.tau_cons
    ctx.decide("consolidate", "classifier", "modify" if merged else "new", answer.confidence, scores=answer.probabilities)
    return merged


SUMMARY_ROUTED = replace(CROW, name="crow-summary-routed", summarize=_summary_state)


def steps_for(args: argparse.Namespace, mode: str) -> Any:
    """The steps of a run: those of `mode` (CROW's, or the classic ones of a dry run), with the summary as routing state and/or
    the merge reading the source; None when nothing changes."""
    changes: dict[str, Any] = {}
    if args.route_reads == "summary":
        changes["summarize"] = _summary_state
    if args.merge_reads == "source":
        changes["merge"] = _merge_from_source
    if args.consolidate_reads == "source":
        changes["consolidate"] = _consolidate_from_source
    if getattr(args, "merge_no_length", False):
        changes["merge"] = _without_length_rule(changes.get("merge", (CROW if mode == "crow" else CLASSIC).merge))
    if not changes:
        return None
    if set(changes) == {"summarize"} and mode == "crow":
        return SUMMARY_ROUTED
    base = CROW if mode == "crow" else CLASSIC
    return replace(base, name=f"{base.name}-" + "-".join(sorted(changes)), **changes)


def keep_wiki(bundle: Path, keep: Path, variant: str, n: int) -> None:
    """Copy the final wiki of run `n` (from 0) to `keep/<variant>/run<n + 1>`, in place of what was there."""
    target = keep / variant / f"run{n + 1}"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(bundle, target)


def menu_arg(text: str) -> str:
    """A `--fallback-menu` value: one of the menus."""
    if text not in MENUS:
        raise argparse.ArgumentTypeError(f"{text!r}: not one of {', '.join(MENUS)}")
    return text


def menu_variant(value: str) -> str:
    """The variant name of a menu value: `menu-visited`, `menu-tree`."""
    return "menu-" + value


def menu_of(args: argparse.Namespace, variant: str) -> str | None:
    """The fallback menu of a variant, as given (`tree`): its own, the only one asked for, or None (the config's)."""
    if variant.startswith("menu-"):
        return variant.removeprefix("menu-")
    return args.fallback_menu[0] if args.fallback_menu and len(args.fallback_menu) == 1 else None


def run_once(
    scenario: str,
    items: list[Item],
    labels: dict[str, dict[str, Any]] | None,
    variant: str,
    args: argparse.Namespace,
    n: int,
    replay: Replay | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="llmw2-stability-") as tmp:
        bundle = Path(tmp) / "wiki"
        if scenario == "b":
            shutil.copytree(args.base or DEMO, bundle)
        settings = Settings(bundle=bundle, concurrency=args.concurrency)
        cfg = settings.config()
        cfg.usage_log = Path(tmp) / "usage.jsonl"
        if variant != "base" and not variant.startswith("menu-"):
            cfg.llm.temperature, cfg.llm.seed = 0.0, SEED
        if variant == "pin":
            cfg.llm.pin_provider = args.pin
        if variant == "vote":
            cfg.crow.votes = 3
        if args.votes is not None:
            cfg.crow.votes = args.votes
        if args.tau_cons is not None:
            cfg.crow.tau_cons = args.tau_cons
        if args.prompts:
            cfg.prompts_dir = args.prompts
        if menu := menu_of(args, variant):
            cfg.crow.fallback_menu = menu  # type: ignore[assignment]  # checked against MENUS by menu_arg
        tracker = UsageTracker(cfg.usage_log)
        classifier: Classifier | None = None
        if args.dry_run:
            cfg.mode = "classic"
            llm: LLM = DryLLM(tracker, delay=0.01)
            llm.temperature, llm.seed = cfg.llm.temperature, getattr(cfg.llm, "seed", None)  # the variant's, so it shows in the cache key
            items = growth_dry_items(items, labels, n) if labels else dry_items(items)
        else:
            cfg.llm.model = args.model
            llm = LLM(cfg.llm, tracker)
            classifier = Classifier(cfg.classifier, tracker) if cfg.mode == "crow" else None
        if replay:
            replay.wrap(llm, classifier)
        wiki = Wiki(cfg, llm=llm, classifier=classifier, steps=steps_for(args, cfg.mode))
        try:
            with wiki:
                wiki.init()
                existing_notes = {note.rel for note in wiki.tree().all_notes()}
                existing_folders = [f.label for f in wiki.tree().walk() if not f.is_root]
                start = time.perf_counter()
                report = wiki.ingest_many(items)
                quality = measure(wiki, report, time.perf_counter() - start)
        finally:
            if replay:
                replay.save()  # also when the run stops
        if args.keep:
            keep_wiki(bundle, args.keep, variant, n)
        return {
            "docs": outcomes_of(report, existing_notes),
            "existing_folders": existing_folders,
            "providers": providers_of(cfg.usage_log),
            "quality": quality,
            "cache": replay.take() if replay else Replay.empty(),
        }


# -- the report ------------------------------------------------------------------------------------


def table(metrics: dict[str, list[float]]) -> str:
    rows = [["metric", "mean", "min", "max"]]
    for name, values in metrics.items():
        rows.append([name, *(f"{f(values):.4g}" for f in (statistics.fmean, min, max))] if values else [name, "-", "-", "-"])
    width = [max(len(row[c]) for row in rows) for c in range(4)]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, width, strict=True)) for row in rows]
    return "\n".join([lines[0], "  ".join("-" * w for w in width), *lines[1:]])


def route_text(route: dict[str, Any] | None) -> str:
    if not route:
        return "-"
    who = "llm" if route["fallback"] or route["decider"] == "llm" else route["decider"]
    return f"{who} {route['confidence']:.2f}" if route["confidence"] is not None else who


def providers_text(providers: dict[str, int]) -> str:
    return ", ".join(f"{name} {calls}" for name, calls in sorted(providers.items())) or "-"


def report_text(
    scenario: str, variant: str, runs: list[dict[str, Any]], metrics: dict[str, list[float]], wobbly: dict[str, list[dict[str, Any]]]
) -> str:
    out = [f"== scenario {scenario.upper()}, variant {variant}, {len(runs)} runs", "", table(metrics), ""]
    out.append("providers per run: " + "; ".join(f"{i + 1}: {providers_text(r['providers'])}" for i, r in enumerate(runs)))
    out.append(f"unstable documents: {len(wobbly)}")
    for doc, per_run in wobbly.items():
        out.append(f"  {doc}: " + " | ".join(f"{r['folder']} ({route_text(r['route'])})" for r in per_run))
    return "\n".join(out)


def defining(args: argparse.Namespace, variant: str) -> dict[str, Any]:
    """The arguments that make a variant what it is, for its raw record."""
    return {
        "variant": variant, "votes": args.votes, "tau_cons": args.tau_cons,
        "route_reads": args.route_reads, "merge_reads": args.merge_reads, "merge_no_length": getattr(args, "merge_no_length", False),
        "consolidate_reads": args.consolidate_reads,
        "prompts": str(args.prompts) if args.prompts else None,
        "fallback_menu": menu_of(args, variant), "base": str(args.base) if args.base else "demo",
        "cache": args.cache or [], "cache_ops": args.cache_ops,
        "corpora": [str(c) for c in args.corpus or []], "only": args.only,
    }


def recompute(files: list[Path], corpora: list[Path], only: list[str] | None) -> int:
    """The report of saved results with the labels of `corpora`: no model is called and the files are only read."""
    labels: dict[str, dict[str, Any]] = {}
    for folder in corpora:
        labels |= {d: v for d, v in corpus_labels(folder).items() if selected(folder, d, only)}
    for file in files:
        raw = json.loads(file.read_text(encoding="utf-8"))
        print(f"== recomputed from {file}")
        if "variants" not in raw:
            print("  no variants in it (a stopped run?)")
            continue
        for variant, record in raw["variants"].items():
            runs = record["runs"]
            known = labels if raw.get("scenario") == "b" else None
            print()
            print(report_text(raw.get("scenario", "b"), variant, runs, between_runs(runs, known), unstable(runs)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="stability of the filing decisions across runs")
    p.add_argument(
        "--scenario", choices=["a", "b"], default="b",
        help="b: the growth corpus into the demo wiki (default); a: the demo's raw documents into an empty wiki"
    )
    p.add_argument(
        "--variant", action="extend", nargs="+", choices=VARIANTS,
        help="base: your config; t0seed: temperature 0, seed 42; pin: t0seed and --pin; vote: 3 classifier calls on borderline routing"
    )
    p.add_argument("--runs", type=int, default=5, help="runs per variant (default 5)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"the LLM (default {DEFAULT_MODEL}, on the configured provider)")
    p.add_argument(
        "--pin", nargs="+", default=DEFAULT_PIN, metavar="PROVIDER",
        help=f"variant pin: providers allowed to answer (default {' '.join(DEFAULT_PIN)})"
    )
    p.add_argument("--concurrency", type=int, default=None, help="documents prepared at once (default: from the config)")
    p.add_argument(
        "--corpus", type=Path, action="append", default=None,
        help="a folder of .md files (B: with labels.json; default the growth corpus; A: the demo's raw copies); repeat it for more"
    )
    p.add_argument("--only", nargs="+", metavar="PATTERN", help='only the documents whose "<corpus folder>/<filename>" matches a glob')
    p.add_argument("--prompts", type=Path, default=None, help="a folder of prompts that override the packaged ones, file by file")
    p.add_argument(
        "--fallback-menu", nargs="+", type=menu_arg, default=None, metavar="MENU",
        help="CROW fallback_menu (visited or tree): one value for every variant, "
        "or several, each a variant of its own (menu-visited, menu-tree)"
    )
    p.add_argument("--base", type=Path, default=None, help="scenario B: the wiki the run starts from (default: the demo)")
    p.add_argument(
        "--cache", action="extend", nargs="+", choices=["llm", "classifier"], default=None,
        help="replay the answers of this model when it was already asked the same thing, and keep the new ones"
    )
    p.add_argument("--cache-ops", nargs="+", metavar="OP", default=None, help="only these operations are cached (default: all)")
    p.add_argument("--cache-file", type=Path, default=None, help="where the answers are kept (default: beside --out, .cache.json)")
    p.add_argument("--recompute", nargs="+", type=Path, metavar="FILE", help="report of saved results, with the labels of --corpus")
    p.add_argument("--votes", type=int, default=None, help="CROW votes for every variant (default: the variant's or your config's)")
    p.add_argument(
        "--route-reads", choices=["source", "summary"], default="source",
        help="source: the deterministic state of the source (default); summary: the LLM's summary, as before the state existed"
    )
    p.add_argument(
        "--merge-reads", choices=["note", "source"], default="note",
        help="note: a merge reads the incoming note (default); source: it reads the source, as before the note-to-note merge"
    )
    p.add_argument("--merge-no-length", action="store_true", help="the merge is asked without the length rule of shared/note_rules")
    p.add_argument(
        "--consolidate-reads", choices=["summary", "source"], default="summary",
        help="summary: consolidate reads the candidate's state (default); source: it reads the deterministic state of the source"
    )
    p.add_argument("--tau-cons", type=float, default=None, help="CROW tau_cons for every variant (default: the variant's or your config's)")
    p.add_argument("--keep", type=Path, default=None, help="copy the final wiki of every run to DIR/<variant>/run<k>/")
    p.add_argument("--out", type=Path, default=Path("stability.json"), help="where the raw results go as JSON")
    p.add_argument("--dry-run", action="store_true", help="a fake model that costs nothing, to try the script")
    args = p.parse_args(argv)
    if args.recompute:
        return recompute(args.recompute, args.corpus or [GROWTH], args.only)
    if args.fallback_menu:
        args.fallback_menu = list(dict.fromkeys(args.fallback_menu))
        if len(args.fallback_menu) > 1 and args.variant:
            p.error("several --fallback-menu values are the variants: do not give --variant as well")
    if args.base and not args.base.is_dir():
        p.error(f"--base {args.base}: not a folder")

    labels = None
    if args.scenario == "b":
        items, labels = growth_items(args.corpus or [GROWTH], args.only)
    else:
        items = raw_items(args.corpus or [DEMO / "raw"], args.only)
    replay = Replay(args.cache_file or args.out.with_suffix(".cache.json"), set(args.cache), args.cache_ops) if args.cache else None
    if args.fallback_menu and len(args.fallback_menu) > 1:
        args.variant = [menu_variant(m) for m in args.fallback_menu]
    args.variant = list(dict.fromkeys(args.variant or ["base"]))  # repeated or in one list, each once, in order
    total = len(args.variant) * args.runs
    cost = "free (fake model)" if args.dry_run else f"about ${len(items) * total * COST_PER_DOC:.2f} (${COST_PER_DOC} a document)"
    print(f"scenario {args.scenario.upper()}: {len(items)} documents, {args.runs} runs of each of {', '.join(args.variant)}; cost {cost}")
    runs: dict[str, list[dict[str, Any]]] = {v: [] for v in args.variant}
    for n in range(args.runs):  # alternate the variants, so a slow hour at the provider does not fall on one of them
        for variant in args.variant:
            run = run_once(args.scenario, items, labels, variant, args, n, replay)
            runs[variant].append(run)
            q = run["quality"]
            print(f"run {n + 1} {variant}: {q['seconds']}s, ${q['cost_usd']}, {q['notes']} notes, {q['folders']} folders")
            if q["stopped"]:  # agreement over the documents filed would be measured on too few, or none: no result
                print(f"  stopped: {q['stopped']} ({q['not_done']} not done)", file=sys.stderr)
                args.out.write_text(json.dumps({"stopped": q["stopped"], "runs": runs}, indent=2), encoding="utf-8")
                print(f"a run stopped, so there is no result; what ran is in {args.out}", file=sys.stderr)
                return 1
    raw: dict[str, Any] = {"scenario": args.scenario, "model": "fake" if args.dry_run else args.model, "variants": {}}
    for variant, done in runs.items():
        metrics, wobbly = between_runs(done, labels), unstable(done)
        print()
        print(report_text(args.scenario, variant, done, metrics, wobbly))
        raw["variants"][variant] = {"args": defining(args, variant), "runs": done, "metrics": metrics, "unstable": wobbly}
    args.out.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"\nraw results: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
