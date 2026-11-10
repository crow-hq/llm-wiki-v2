# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Incremental filing: a corpus filed into an empty wiki by the library as it is installed in this tree, and what it did.

    python benchmarks/incremental.py --corpus DIR --out runs/crow-batch --mode crow --flow batch --model MODEL
    python benchmarks/incremental.py --corpus DIR --out runs/crow-single --mode crow --flow single --limit 60
    python benchmarks/incremental.py --corpus DIR --out runs/classic-batch --mode classic --dry-run   # fake models, free
    python benchmarks/incremental.py --corpus DIR --out runs/crow-grow --chunk 200 --stop-projected 1.3 --model MODEL

DIR holds `.md` files, filed in name order (`--limit` keeps the first N; `--order-seed S` files those N in a seeded permutation
instead: `origin.modified` is the rank in it, and the `order` of `docs.jsonl` follows it). `--flow batch` is one `ingest_many`:
the library's own windows, with whatever it does before and after each (CROW plans the folders of a window; `--no-plan` files
with no plan, `plan=False`). `--flow single` is one `ingest` per document, in the same order. The steps are the library's
defaults: no custom route, no prompt overrides, and CROW's configuration is the package's own, whatever the local settings
say. The model is the configured provider's (the settings file and OKF_* variables); the estimate is printed before the first
call and a run stops once the calls that were really made (not replayed) cost more than `--max-usd`.

Like `stability.py` it imports `llmw2` from its own tree, so to measure `main` run a copy of this folder from a worktree of
`main` (README, "Comparing with `main`"). Answers of the models are kept in a replay cache (`--cache`, one JSON file; default
beside `--out`), so an interrupted run or a rerun pays only for the calls it did not make yet; the original cost of a replayed
call is kept in a sidecar beside the cache, so costs stay comparable.

`--chunk N` (batch only) files the documents in successive `ingest_many` of N, each on the tree the one before left, as a user
who adds documents to a wiki that already exists. After each, once the batch has tidied its folders, `ckpt-<documents filed:05d>/`
holds a snapshot of the run folder as it was (meta, the prefixes of `docs.jsonl`, `calls.jsonl` and `usage.jsonl`, `moves.jsonl`
and a copy of `wiki/`) with its `digest` in `meta.json`, so `incremental_score.py` and `retrieval.py` read a checkpoint like a
run, and a rerun with the same cache must give the same digests. `--stop-projected USD` stops at the end of a chunk when the
projected spend of the whole run passes USD: after the first chunk, `c1*N + s*c1*(N^2 - n1^2)/200` (c1 the cost per document of
the first chunk, plans included, n1 its documents, N all of them, s the growth of the cost per document per 100 documents: 0.18
for CROW, 0.07 for classic); from the second chunk on s is the run's own, fitted on the chunks (never below 0). Costs here
are the original cost of every call, a replayed one included.

`--out` must not exist; it holds:
- `meta.json`: library version, the commit of the tree (`git rev-parse HEAD` of this tree; null outside a checkout) and, when
  `src/` or `benchmarks/` differ from it, `dirty` and `diff_sha256`, the hash of their diff and untracked files; mode, flow,
  model, CROW's configuration, the plan setting (`plan`, `no_plan`), the order seed, the chunk, the providers' pin and the calls
  per provider;
- `docs.jsonl`: per document, in filing order, its note, folder, action, created folders, related notes and decisions
  (or its error), and `log_seq`, how many entries `log.md` had once it was filed;
- `calls.jsonl`: per model call, its kind, operation, whether the replay cache served it, and its original cost, tokens and
  seconds; `filed`, the documents filed before the call (the library prepares and files one document at a time here, so the
  calls between the results k and k+1 are those of document k+1, counting from 0; the calls made after the last result are
  `filed` = all of them), and `phase`, `plan` for the calls that lay out folders (`plan`, `plan_more`, `split`), `file` for the rest;
- `moves.jsonl`: the notes moved while filing (the "Moved" entries of `log.md`, in order), so a document's final note can be
  found: `incremental_score.py` follows each note through the moves made after it was filed;
- `usage.jsonl`: the library's own usage log; `wiki/`: the wiki.
Score it with `incremental_score.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the scripts of this folder import each other, however this one is started
# The library of this same tree, not the one the venv installed as editable: a run of a copy of this folder in a worktree
# of `main` would otherwise measure the wrong code.
sys.path.insert(1, str(HERE.parent / "src"))

from ingest_parallel import DEFAULT_MODEL, DryLLM, dry_items  # noqa: E402
from replay import Replay, digest  # noqa: E402

import llmw2  # noqa: E402
from llmw2 import Item, Origin, Wiki  # noqa: E402
from llmw2.bundle.files import title_of  # noqa: E402
from llmw2.config import ClassifierConfig, LLMConfig, WikiConfig  # noqa: E402
from llmw2.models.classifier import Classifier  # noqa: E402
from llmw2.models.llm import LLM  # noqa: E402
from llmw2.models.usage import UsageTracker  # noqa: E402
from llmw2.settings import Settings  # noqa: E402

if not Path(llmw2.__file__ or "").resolve().is_relative_to((HERE.parent / "src").resolve()):
    sys.exit(f"llmw2 comes from {llmw2.__file__}, not from {HERE.parent / 'src'}: run with PYTHONPATH={HERE.parent / 'src'}")

COST_PER_DOC = 0.00017  # USD, for a small model in CROW mode: an order of magnitude for the estimate, not a price list
ENTRY = re.compile(r"^\* \*\*([A-Za-z ]+)\*\*: (.*)$")
MOVED = re.compile(r"from /(\S+) to /(\S+)\s*$")
PLAN_OPS = {"plan", "plan_more", "split"}  # the calls that lay out folders: their phase is "plan", the others' "file"
GROWTH = {"crow": 0.18, "classic": 0.07}  # cost per document per 100 documents filed, as a share of the first chunk's (a prior)
TIMESTAMP = re.compile(r"\d{8}-\d{6}|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?")
NOTE_ID = re.compile(r"(?m)^id: '?[0-9a-f]{12}'?$")


class Overspend(Exception):
    """The calls made for real (not replayed) cost more than `--max-usd`."""


class Calls:
    """Logs every model call around the replay cache, with its original cost.

    The usage record of a call that went to the model is kept in a sidecar under the cache's own key; a replayed call is costed
    from it. Calls of different threads do not mix: the record of a call is read on the thread that made it.
    """

    def __init__(self, replay: Replay, tracker: UsageTracker, path: Path, sidecar: Path, max_usd: float) -> None:
        self.replay, self.file, self.sidecar = replay, path.open("a", encoding="utf-8"), sidecar
        self.costs: dict[str, dict[str, Any]] = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        self.spent, self.max_usd, self.doc = 0.0, max_usd, None
        self.filed, self.total = 0, 0.0  # documents filed so far; the original cost of every call so far
        self.lock, self.local = threading.Lock(), threading.local()
        record = tracker.record

        def capture(kind: str, model: str, input_tokens: int, output_tokens: int, **kw: Any) -> Any:
            self.local.last = {"in": input_tokens, "out": output_tokens, "cost": kw.get("cost") or 0.0, "seconds": kw.get("seconds", 0.0)}
            return record(kind, model, input_tokens, output_tokens, **kw)

        tracker.record = capture  # type: ignore[method-assign]

    def save(self) -> None:
        with self.lock:
            self.sidecar.write_text(json.dumps(self.costs), encoding="utf-8")

    def wrap(self, llm: LLM, classifier: Classifier | None) -> None:
        """Route the models' calls through the log (call it after `Replay.wrap`, so the log sees what the cache served)."""
        chat = llm.chat

        def logged_chat(
            messages: list[dict[str, str]],
            *,
            op: str = "",
            json_mode: bool = False,
            max_tokens: int | None = None,
            seed: int | None = None,
        ) -> str:
            fixed = {} if seed is None else {"seed": seed}
            key = digest(
                {
                    "model": llm.model,
                    "temperature": llm.temperature,
                    "seed": llm.seed if seed is None else seed,
                    "json_mode": json_mode,
                    "messages": messages,
                }
            )
            return str(self._call("llm", op, key, lambda: chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens, **fixed)))

        llm.chat = logged_chat  # type: ignore[method-assign]
        if classifier is not None:
            ask = classifier.ask

            def logged_ask(state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
                key = digest({"model": classifier.model, "state": state, "questions": questions})
                return self._call("classifier", op, key, lambda: ask(state, questions, op=op))  # type: ignore[no-any-return]

            classifier.ask = logged_ask  # type: ignore[method-assign]

    def _call(self, kind: str, op: str, key: str, ask: Callable[[], Any]) -> Any:
        with self.lock:
            before = self.replay.counts[kind]["served"]
        self.local.last = None
        out = ask()
        last = self.local.last
        with self.lock:
            served = self.replay.counts[kind]["served"] > before
            if not served and last is not None:
                self.costs.setdefault(key, last)
                self.spent += last["cost"]
            c = self.costs.get(key, {})
            self.total += c.get("cost") or 0.0
            phase = "plan" if op.rsplit("/", 1)[-1] in PLAN_OPS else "file"
            row = {"doc": self.doc, "filed": self.filed, "phase": phase, "kind": kind, "op": op, "served": served, "key": key[:16]}
            row |= {"cost": c.get("cost"), "in": c.get("in"), "out": c.get("out"), "seconds": c.get("seconds")}
            self.file.write(json.dumps(row) + "\n")
            self.file.flush()
            over = self.spent > self.max_usd
            spent = self.spent
        if over:
            raise Overspend(f"spent ${spent:.3f} > ${self.max_usd}")
        return out


# -- the fake models of --dry-run ----------------------------------------------------------------


def fake_models(tracker: UsageTracker) -> tuple[LLM, Classifier]:
    """Models that answer like the real ones, from the prompt alone and without the network (a CROW dry run)."""

    class FakeLLM(LLM):
        def chat(
            self, messages: list[dict[str, str]],
            *,
            op: str = "",
            json_mode: bool = False,
            max_tokens: int | None = None,
            seed: int | None = None,
        ) -> str:
            self.usage.record("llm", self.model, 100, 10, op=op, seconds=0.0, provider="dry", cost=0.0001)
            user = messages[-1]["content"]
            h = int(hashlib.sha256(user.encode()).hexdigest(), 16)
            if "summarize" in op:
                return json.dumps({"title": f"Note {h % 997}", "summary": "s", "tags": ["x"], "body": user[-400:]})
            if op.endswith(("plan", "plan_more")):
                folders = [
                    {"path": "/area-a", "description": "Area A."},
                    {"path": "/area-a/sub-1", "description": "Sub 1."},
                    {"path": "/area-b", "description": "Area B."},
                ]
                return json.dumps({"folders": folders})
            if op.endswith("split"):
                parts = [
                    {"name": f"part-{h % 7}", "description": "Part.", "notes": [1, 2, 3]},
                    {"name": "one", "description": "One.", "notes": [4]},
                ]
                return json.dumps({"folders": parts})
            if "name_folder" in op:
                return json.dumps({"name": f"folder-{h % 50}", "description": "A folder."})
            if "route_fallback" in op:
                return json.dumps({"action": "select", "folder": "/area-a", "reasoning": ""})
            return json.dumps({"title": "Merged", "summary": "m", "tags": [], "body": "merged body"})

    class FakeClassifier(Classifier):
        def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
            self.usage.record("classifier", self.model, 50, 5, op=op, seconds=0.0, provider="dry", cost=0.00005)
            out: dict[str, dict[str, Any]] = {}
            for k, q in questions.items():
                h = int(hashlib.sha256((state[:200] + k).encode()).hexdigest(), 16)
                if q["type"] == "choice":
                    options = list(q["criteria"])
                    raw = [((h >> (8 * i)) % 100) + 1 for i in range(len(options))]
                    probs = {o: r / sum(raw) for o, r in zip(options, raw, strict=True)}
                    out[k] = {"choice": max(probs, key=lambda o: probs[o]), "probabilities": probs}
                else:
                    out[k] = {"noul": (h % 100) / 100}
            return out

    return FakeLLM(LLMConfig(model="fake/llm"), tracker), FakeClassifier(ClassifierConfig(model="fake/jev", api_key="dry"), tracker)


# -- the log of the wiki, and the rows of the run --------------------------------------------------


def log_entries(bundle: Path) -> list[tuple[str, str]]:
    """(verb, rest of the line) of every entry of `log.md`, oldest first (the file has the newest first)."""
    path = bundle / "log.md"
    if not path.exists():
        return []
    rows = [m.groups() for m in map(ENTRY.match, path.read_text(encoding="utf-8").splitlines()) if m]
    return rows[::-1]


def moves_of(entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The "Moved" entries as {seq, from, to}: `seq` is the entry's position in the log, `from` and `to` are note paths."""
    out = []
    for seq, (verb, rest) in enumerate(entries):
        if verb == "Moved" and (m := MOVED.search(rest)):
            out.append({"seq": seq, "from": m.group(1), "to": m.group(2)})
    return out


def note_retries(result: Any) -> dict[str, int]:
    """How often the writer's note was asked for again (`note_retry`) and fell back to the source text (`note_source`)."""
    decisions = [] if isinstance(result, Exception) else result.decisions
    return {"retries": sum(d.step == "note_retry" for d in decisions), "sources": sum(d.step == "note_source" for d in decisions)}


def row_of(name: str, order: int, result: Any, seconds: float, log_seq: int) -> dict[str, Any]:
    """One document of the run: what its ingest returned, or the error."""
    row: dict[str, Any] = {"doc": name, "order": order, "log_seq": log_seq}
    if isinstance(result, Exception):
        row["error"] = f"{type(result).__name__}: {' '.join(str(result).split())[:200]}"
    else:
        row |= {
            "action": result.action,
            "note": result.note,
            "folder": result.folder,
            "created": list(getattr(result, "created_folders", None) or []),
            "related": list(result.related or []),
            "decisions": [
                {
                    "step": d.step,
                    "decider": d.decider,
                    "choice": d.choice,
                    "confidence": d.confidence,
                    "fallback": d.fallback,
                    "scores": {k: round(v, 4) for k, v in getattr(d, "scores", {}).items()},
                }
                for d in result.decisions
            ],
        }
    row["seconds"] = round(seconds, 2)
    return row


def git(*args: str, root: Path = HERE.parent) -> str:
    """A read-only git command in a tree (this one by default), "" when there is no git or no checkout."""
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def tree_state(root: Path = HERE.parent) -> dict[str, Any]:
    """Where the library of a run comes from: the commit (`git rev-parse HEAD`, None outside a checkout) and whether `src/` or
    `benchmarks/` differ from it, with the sha256 of that difference (the diff against the commit and the untracked files)."""
    commit = git("rev-parse", "HEAD", root=root) or None
    dirty = bool(git("status", "--porcelain", "--", "src", "benchmarks", root=root))
    state: dict[str, Any] = {"commit": commit, "dirty": dirty, "diff_sha256": None}
    if dirty:
        h = hashlib.sha256(git("diff", "HEAD", "--", "src", "benchmarks", root=root).encode())
        for rel in sorted(git("ls-files", "--others", "--exclude-standard", "--", "src", "benchmarks", root=root).splitlines()):
            h.update(rel.encode())
            h.update((root / rel).read_bytes())
        state["diff_sha256"] = h.hexdigest()
    return state


def wiki_digest(bundle: Path) -> str:
    """The sha256 of what a wiki shows (notes, indexes, log, raw copies), with timestamps and note ids taken out; files are
    taken in the order of their cleaned names, as the time in a name says nothing about the order of two runs."""
    files = []
    for p in bundle.rglob("*"):
        rel = p.relative_to(bundle)
        if p.is_file() and rel.parts[0] != ".llmw2":
            text = p.read_text(encoding="utf-8", errors="replace")
            files.append((TIMESTAMP.sub("T", rel.as_posix()), TIMESTAMP.sub("T", NOTE_ID.sub("id: -", text))))
    h = hashlib.sha256()
    for name, text in sorted(files):
        h.update(name.encode())
        h.update(text.encode())
    return h.hexdigest()


def projected_spend(blocks: list[tuple[int, float]], total: int, growth: float) -> float:
    """The spend of a run of `total` documents from its chunks so far, `(documents, cost)` each: with c1 the cost per document
    of the first chunk and n1 its documents, `c1*N + s*c1*(N^2 - n1^2)/200`. `s` is `growth`; from two chunks on, it is the
    growth the chunks show, the slope of their cost per document over the documents filed, as a share of c1 per 100 documents
    (never below 0)."""
    n1, first = blocks[0]
    c1 = first / n1
    if len(blocks) > 1 and c1 > 0:
        xs, ys, filed = [], [], 0
        for n, c in blocks:
            xs.append(filed + n / 2)
            ys.append(c / n)
            filed += n
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        growth = max(0.0, 100 * sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx / c1) if sxx else 0.0
    return c1 * total + growth * c1 * (total**2 - n1**2) / 200


def jsonl_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def hit_rate(counts: dict[str, dict[str, int]]) -> float | None:
    served = sum(c["served"] for c in counts.values())
    total = sum(c["served"] + c["called"] for c in counts.values())
    return round(served / total, 4) if total else None


def bench_thresholds(cfg: WikiConfig, env: Mapping[str, str] | None = None) -> None:
    """The CROW thresholds a run uses: the package's defaults with the preset of the classifier in use and what the
    environment sets (`OKF_TAU_CONS`, ...), whatever the local settings file says. A local classifier gets its own preset,
    not Jev's."""
    fresh = WikiConfig.from_env(os.environ if env is None else env, bundle=cfg.bundle, classifier=cfg.classifier.model_dump())
    cfg.crow = fresh.crow


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="file a corpus into an empty wiki with the library of this tree")
    p.add_argument("--corpus", type=Path, required=True, help="a folder of .md files, filed in name order")
    p.add_argument("--out", type=Path, required=True, help="the run folder (it must not exist)")
    p.add_argument("--mode", choices=["crow", "classic"], default="crow", help="the library's mode (default crow)")
    p.add_argument("--flow", choices=["batch", "single"], default="batch", help="batch: one ingest_many; single: one ingest per document")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"the LLM (default {DEFAULT_MODEL}, on the configured provider)")
    p.add_argument("--limit", type=int, default=None, help="only the first N documents")
    p.add_argument("--cache", type=Path, default=None, help="the replay cache file (default: cache-<model>.json beside --out)")
    p.add_argument("--max-usd", type=float, default=0.20, help="stop once the calls really made (not replayed) cost more (default 0.20)")
    p.add_argument("--chunk", type=int, default=None, metavar="N", help="batch: file N documents at a time, a snapshot (ckpt-*) after each")
    p.add_argument(
        "--stop-projected",
        type=float,
        default=None,
        metavar="USD",
        help="with --chunk: stop after a chunk if the run is projected to cost more",
    )
    p.add_argument("--order-seed", type=int, default=None, metavar="S", help="file the documents in the permutation of this seed")
    p.add_argument("--no-plan", action="store_true", help="batch: no plan of the folders (ingest_many(plan=False))")
    p.add_argument("--dry-run", action="store_true", help="fake models that cost nothing, to try the script")
    a = p.parse_args(argv)
    if a.out.exists():
        p.error(f"{a.out} exists: refusing to overwrite a run")
    if a.flow == "single" and (a.chunk or a.no_plan):
        p.error("--chunk and --no-plan are for --flow batch")
    if a.chunk is not None and a.chunk < 1:
        p.error("--chunk is at least 1")
    if a.stop_projected is not None and not a.chunk:
        p.error("--stop-projected needs --chunk")
    files = sorted(a.corpus.glob("*.md"), key=lambda f: f.name)[: a.limit]
    if not files:
        p.error(f"no .md files in {a.corpus}")
    if a.order_seed is not None:
        random.Random(a.order_seed).shuffle(files)
    single = a.flow == "single"
    tag = "dry" if a.dry_run else a.model.split("/")[-1]
    cache = a.cache or (a.out / "cache-dry.json" if a.dry_run else a.out.parent / f"cache-{tag}.json")
    sidecar = cache.with_name(cache.stem + "-costs.json")
    cost = "free (fake models)" if a.dry_run else f"about ${len(files) * COST_PER_DOC:.2f} if nothing is replayed, at most ${a.max_usd}"
    print(f"{len(files)} documents, {a.mode} {a.flow}, model {'fake' if a.dry_run else a.model}; cost {cost}")

    a.out.mkdir(parents=True)
    bundle = a.out / "wiki"
    cfg = Settings().config()
    cfg.bundle, cfg.mode, cfg.usage_log = bundle, a.mode, a.out / "usage.jsonl"
    cfg.llm.model = a.model
    bench_thresholds(cfg)
    tracker = UsageTracker(cfg.usage_log)
    texts = {f.name: f.read_text(encoding="utf-8") for f in files}
    classifier: Classifier | None = None
    if a.dry_run and a.mode == "crow":
        llm, classifier = fake_models(tracker)
    elif a.dry_run:  # the fake model of the classic benchmarks reads "topic: <group>-<n>" at the head of a document
        llm = DryLLM(tracker, delay=0.0)
        texts = {i.filename: i.text or "" for i in dry_items([Item(n, text=t) for n, t in texts.items()])}
    else:
        llm = LLM(cfg.llm, tracker)
        classifier = Classifier(cfg.classifier, tracker) if a.mode == "crow" else None
    replay = Replay(cache, {"llm", "classifier"})
    replay.wrap(llm, classifier)
    calls = Calls(replay, tracker, a.out / "calls.jsonl", sidecar, a.max_usd)
    calls.wrap(llm, classifier)
    wiki = Wiki(cfg, llm=llm, classifier=classifier)  # the library's own steps
    wiki.init()
    meta = {
        "library": llmw2.__version__,
        **tree_state(),
        "mode": a.mode,
        "flow": a.flow,
        "model": "fake/llm" if a.dry_run else a.model,
        "dry_run": a.dry_run,
        "documents": len(files),
        "window": getattr(sys.modules.get("llmw2.wiki"), "PLAN_BATCH", None),
        "crow": cfg.crow.model_dump(),
        "plan": cfg.plan,
        "no_plan": a.no_plan,
        "order_seed": a.order_seed,
        "chunk": a.chunk,
        "stop_projected": a.stop_projected,
        "pin_provider": list(cfg.llm.pin_provider),
    }
    (a.out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    stopped = None
    order = {f.name: n for n, f in enumerate(files)}  # the position in filing order (the permutation, with --order-seed)
    log = (a.out / "docs.jsonl").open("w", encoding="utf-8")
    noted: dict[str, dict[str, int]] = {}  # per document, the retries of its note and the times it kept the source text

    def filed(name: str, result: Any, t0: float) -> None:
        counts = note_retries(result)
        if counts["retries"] or counts["sources"]:
            noted[name] = counts
        log.write(json.dumps(row_of(name, order[name], result, time.perf_counter() - t0, len(log_entries(bundle)))) + "\n")
        log.flush()
        replay.save()
        calls.save()
        calls.filed += 1

    def checkpoint(n: int, **extra: Any) -> None:
        """The run folder as it is after `n` documents: the logs so far, the moves, a copy of the wiki, and its digest."""
        ck = a.out / f"ckpt-{n:05d}"
        ck.mkdir()
        log.flush()
        calls.file.flush()
        for name in ("docs.jsonl", "calls.jsonl", "usage.jsonl"):
            if (a.out / name).exists():
                shutil.copyfile(a.out / name, ck / name)
        (ck / "moves.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves_of(log_entries(bundle))), encoding="utf-8")
        shutil.copytree(bundle, ck / "wiki")
        done = {"checkpoint": n, "digest": wiki_digest(bundle), "spent_original_usd": round(calls.total, 6), **extra}
        (ck / "meta.json").write_text(json.dumps(meta | done, indent=1), encoding="utf-8")

    try:
        if single:
            for f in files:
                calls.doc = f.name
                t0 = time.perf_counter()
                try:
                    result: Any = wiki.ingest(texts[f.name], title=title_of(f.name), resource=f"~/docs/{f.name}")
                except Overspend:
                    raise
                except Exception as e:  # recorded, the run goes on
                    result = e
                filed(f.name, result, t0)
        else:
            calls.doc = "batch"
            items = []
            for n, f in enumerate(files):
                at = (datetime(2000, 1, 1) + timedelta(seconds=n)).isoformat()  # the rank in the order, as a date the batch sorts by
                origin = None if a.order_seed is None else Origin("benchmark", "", f.name, modified=at)
                items.append(Item(f.name, text=texts[f.name], resource=f"~/docs/{f.name}", origin=origin))
            t = [time.perf_counter()]
            blocks: list[tuple[int, float]] = []  # per chunk: its documents and what it cost

            def on_result(item: Item, result: Any) -> None:
                filed(item.filename, result, t[0])
                t[0] = time.perf_counter()

            step = a.chunk or len(items)
            for start in range(0, len(items), step):
                before = calls.total
                t[0] = time.perf_counter()
                report = wiki.ingest_many(items[start : start + step], concurrency=1, on_result=on_result, plan=not a.no_plan)
                stopped = report.stopped  # ingest_many keeps what stops a batch (an Overspend inside too): reported here
                if stopped or not a.chunk:
                    break
                blocks.append((min(step, len(items) - start), calls.total - before))
                extra: dict[str, Any] = {"filed": calls.filed}
                if a.stop_projected is not None:
                    extra["projected_usd"] = round(projected_spend(blocks, len(items), GROWTH[a.mode]), 6)
                checkpoint(calls.filed, **extra)
                print(f"checkpoint {calls.filed}: " + (f"projected ${extra['projected_usd']:.4f}" if "projected_usd" in extra else "done"))
                if a.stop_projected is not None and extra["projected_usd"] > a.stop_projected and start + step < len(items):
                    stopped = f"projected spend ${extra['projected_usd']:.4f} > ${a.stop_projected} after {calls.filed} documents"
                    break
    except Overspend as e:
        stopped = f"Overspend: {e}"
    finally:
        replay.save()
        calls.save()
        log.close()
        calls.file.close()
    (a.out / "moves.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves_of(log_entries(bundle))), encoding="utf-8")
    totals = {k: sum(c[k] for c in noted.values()) for k in ("retries", "sources")}
    usage = jsonl_lines(a.out / "usage.jsonl")
    providers = {p: sum(u.get("provider") == p for u in usage) for p in sorted({str(u.get("provider")) for u in usage})}
    meta |= {"digest": wiki_digest(bundle), "providers": providers, "note_retries": totals, "stopped": stopped}
    meta |= {"spent_real_usd": round(calls.spent, 4), "cache_hit_rate": hit_rate(replay.counts)}
    (a.out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"done: real spend ${calls.spent:.4f}, cache hit rate {meta['cache_hit_rate']}" + (f", STOPPED: {stopped}" if stopped else ""))
    for name, c in noted.items():
        print(f"note retries: {name}: {c['retries']} retried, {c['sources']} kept the source text")
    print(f"note retries: {totals['retries']} retried, {totals['sources']} kept the source text, in {len(noted)} documents")
    print(f"run: {a.out}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
