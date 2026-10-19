# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Incremental filing: a corpus filed into an empty wiki by the library as it is installed in this tree, and what it did.

    python benchmarks/incremental.py --corpus DIR --out runs/crow-batch --mode crow --flow batch --model MODEL
    python benchmarks/incremental.py --corpus DIR --out runs/crow-single --mode crow --flow single --limit 60
    python benchmarks/incremental.py --corpus DIR --out runs/classic-batch --mode classic --dry-run   # fake models, free

DIR holds `.md` files, filed in name order (`--limit` keeps the first N). `--flow batch` is one `ingest_many`: the library's
own windows, with whatever it does before and after each (CROW plans the folders of a window). `--flow single` is one
`ingest` per document, in the same order. The steps are the library's defaults: no custom route, no prompt overrides, and
CROW's configuration is the package's own, whatever the local settings say. The model is the configured provider's (the
settings file and OKF_* variables); the estimate is printed before the first call and a run stops once the calls that were
really made (not replayed) cost more than `--max-usd`.

Like `stability.py` it imports `llmw2` from its own tree, so to measure `main` run a copy of this folder from a worktree of
`main` (README, "Comparing with `main`"). Answers of the models are kept in a replay cache (`--cache`, one JSON file; default
beside `--out`), so an interrupted run or a rerun pays only for the calls it did not make yet; the original cost of a replayed
call is kept in a sidecar beside the cache, so costs stay comparable.

`--out` must not exist; it holds:
- `meta.json`: library version and commit (when this tree is a git checkout), mode, flow, model, CROW's configuration;
- `docs.jsonl`: per document, in filing order, its note, folder, action, created folders, related notes and decisions
  (or its error), and `log_seq`, how many entries `log.md` had once it was filed;
- `calls.jsonl`: per model call, its kind, operation, whether the replay cache served it, and its original cost, tokens and seconds;
- `moves.jsonl`: the notes moved while filing (the "Moved" entries of `log.md`, in order), so a document's final note can be
  found: `incremental_score.py` follows each note through the moves made after it was filed;
- `usage.jsonl`: the library's own usage log; `wiki/`: the wiki.
Score it with `incremental_score.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
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
from llmw2 import Item, Wiki  # noqa: E402
from llmw2.bundle.files import title_of  # noqa: E402
from llmw2.config import ClassifierConfig, LLMConfig  # noqa: E402
from llmw2.models.classifier import Classifier  # noqa: E402
from llmw2.models.llm import LLM  # noqa: E402
from llmw2.models.usage import UsageTracker  # noqa: E402
from llmw2.settings import Settings  # noqa: E402

if not Path(llmw2.__file__ or "").resolve().is_relative_to((HERE.parent / "src").resolve()):
    sys.exit(f"llmw2 comes from {llmw2.__file__}, not from {HERE.parent / 'src'}: run with PYTHONPATH={HERE.parent / 'src'}")

COST_PER_DOC = 0.00017  # USD, for a small model in CROW mode: an order of magnitude for the estimate, not a price list
ENTRY = re.compile(r"^\* \*\*([A-Za-z ]+)\*\*: (.*)$")
MOVED = re.compile(r"from /(\S+) to /(\S+)\s*$")


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

        def logged_chat(messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
            key = digest(
                {"model": llm.model, "temperature": llm.temperature, "seed": llm.seed, "json_mode": json_mode, "messages": messages}
            )
            return str(self._call("llm", op, key, lambda: chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)))

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
            row = {"doc": self.doc, "kind": kind, "op": op, "served": served, "key": key[:16]}
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
        def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
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


def git(*args: str) -> str:
    """A read-only git command in this tree, "" when there is no git or no checkout."""
    try:
        done = subprocess.run(["git", "-C", str(HERE.parent), *args], capture_output=True, text=True, encoding="utf-8", check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def hit_rate(counts: dict[str, dict[str, int]]) -> float | None:
    served = sum(c["served"] for c in counts.values())
    total = sum(c["served"] + c["called"] for c in counts.values())
    return round(served / total, 4) if total else None


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
    p.add_argument("--dry-run", action="store_true", help="fake models that cost nothing, to try the script")
    a = p.parse_args(argv)
    if a.out.exists():
        p.error(f"{a.out} exists: refusing to overwrite a run")
    files = sorted(a.corpus.glob("*.md"), key=lambda f: f.name)[: a.limit]
    if not files:
        p.error(f"no .md files in {a.corpus}")
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
    cfg.crow = type(cfg.crow)()  # the package's defaults, whatever the local settings say
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
        "commit": git("rev-parse", "--short", "HEAD") or None,
        "dirty": bool(git("status", "--porcelain")),
        "mode": a.mode,
        "flow": a.flow,
        "model": "fake/llm" if a.dry_run else a.model,
        "dry_run": a.dry_run,
        "documents": len(files),
        "window": getattr(sys.modules.get("llmw2.wiki"), "PLAN_BATCH", None),
        "crow": cfg.crow.model_dump(),
    }
    (a.out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    stopped = None
    order = {f.name: n for n, f in enumerate(files)}
    log = (a.out / "docs.jsonl").open("w", encoding="utf-8")

    def filed(name: str, result: Any, t0: float) -> None:
        log.write(json.dumps(row_of(name, order[name], result, time.perf_counter() - t0, len(log_entries(bundle)))) + "\n")
        log.flush()
        replay.save()
        calls.save()

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
            items = [Item(f.name, text=texts[f.name], resource=f"~/docs/{f.name}") for f in files]
            t = [time.perf_counter()]

            def on_result(item: Item, result: Any) -> None:
                filed(item.filename, result, t[0])
                t[0] = time.perf_counter()

            report = wiki.ingest_many(items, concurrency=1, on_result=on_result)
            stopped = report.stopped  # ingest_many keeps what stops a batch (an Overspend inside too): reported here
    except Overspend as e:
        stopped = f"Overspend: {e}"
    finally:
        replay.save()
        calls.save()
        log.close()
        calls.file.close()
    (a.out / "moves.jsonl").write_text("".join(json.dumps(m) + "\n" for m in moves_of(log_entries(bundle))), encoding="utf-8")
    meta |= {"stopped": stopped, "spent_real_usd": round(calls.spent, 4), "cache_hit_rate": hit_rate(replay.counts)}
    (a.out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"done: real spend ${calls.spent:.4f}, cache hit rate {meta['cache_hit_rate']}" + (f", STOPPED: {stopped}" if stopped else ""))
    print(f"run: {a.out}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
