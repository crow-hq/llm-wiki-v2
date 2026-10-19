# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Real retrieval: CROW's `select` step on the final wiki of runs of `incremental.py`, for questions about its documents.

    python benchmarks/retrieval.py --make-cases CORPUS --n 60 --seed 9 --out cases.json     # free: the documents to ask about
    # a model writes one question per case with judgements/retrieval-questions-prompt.md -> questions.json
    python benchmarks/retrieval.py --questions questions.json --cases cases.json RUN [RUN ...]
    python benchmarks/retrieval.py --questions questions.json --cases cases.json RUN --dry-run     # fake models, free

`select` is what the library runs to answer a question: the folder beam (the classifier), the scoring of the notes of
those folders, the top notes, and the LLM navigating the tree only when nothing clears the threshold. No answer is
generated. `--questions` is `[{"id", "question"}]` (a null question is skipped), `--cases` is `[{"id", "doc"}]` with `doc` the
file name of the document the question was written from (`--make-cases` also writes the opening of the document, `text`, which
is what the question writer reads and this script ignores).

Hit: the document's final note (followed through the moves, as `incremental_score.py` does) is among the notes `select`
returns. Per run: hit, hit@1, hit@3, MRR, the share of questions answered through the LLM fallback, the notes returned
and the cost of the calls really made. Questions on a document the run did not file are left out. The wiki of a run is
copied first and the copy is read, so the run folder is not touched. Answers of the models are kept in a replay cache
(`--cache`, one JSON file; default beside the first run), and a run stops asking once the calls really made cost more
than `--max-usd`. The model is the one in the run's `meta.json`; the classifier is the configured one.

The questions of a corpus that is not committed (a handbook, a mailbox) are not committed either: the repository holds
the prompt that writes them, and what is written about a question (the results) names it by id, never by text. For the
same reason the result file has no question texts.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / "src"))  # the library of this same tree (see incremental.py)

import incremental  # noqa: E402  (checks that llmw2 comes from this tree)
import incremental_score as scoring  # noqa: E402
from replay import Replay  # noqa: E402

from llmw2 import CROW, Wiki  # noqa: E402
from llmw2.models.classifier import Classifier  # noqa: E402
from llmw2.models.llm import LLM  # noqa: E402
from llmw2.models.usage import UsageTracker  # noqa: E402
from llmw2.settings import Settings  # noqa: E402

TEXT_CHARS = 3000


def make_cases(corpus: Path, n: int, seed: int, out: Path) -> int:
    """`n` documents of the corpus (seeded, in name order): id, file name and the opening of the text."""
    files = sorted(corpus.glob("*.md"), key=lambda f: f.name)
    if not files:
        sys.exit(f"no .md files in {corpus}")
    pick = sorted(random.Random(seed).sample(files, min(n, len(files))), key=lambda f: f.name)
    cases = [{"id": f"q{i:03d}", "doc": f.name, "text": f.read_text(encoding="utf-8")[:TEXT_CHARS]} for i, f in enumerate(pick)]
    out.write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} cases of {len(files)} documents: {out}")
    return 0


def ask_run(
    run: Path, questions: list[tuple[str, str]], doc_of: dict[str, str], a: argparse.Namespace, replay: Replay | None
) -> dict[str, Any]:
    """The hits of one run: every question asked of `select` on a copy of its wiki."""
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    final = scoring.final_notes(run)
    with tempfile.TemporaryDirectory(prefix="llmw2-retrieval-") as tmp:
        bundle = Path(tmp) / "wiki"
        shutil.copytree(scoring.wiki_of(run), bundle)
        cfg = Settings().config()
        cfg.bundle, cfg.mode, cfg.usage_log = bundle, "crow", None
        cfg.llm.model = meta["model"]
        cfg.crow = type(cfg.crow)()  # the package's defaults, whatever the local settings say
        tracker = UsageTracker(None)
        spent = [0.0]
        record = tracker.record

        def capture(kind: str, model: str, input_tokens: int, output_tokens: int, **kw: Any) -> Any:
            spent[0] += kw.get("cost") or 0.0
            return record(kind, model, input_tokens, output_tokens, **kw)

        tracker.record = capture  # type: ignore[method-assign]
        if a.dry_run:
            llm, classifier = incremental.fake_models(tracker)
        else:
            llm, classifier = LLM(cfg.llm, tracker), Classifier(cfg.classifier, tracker)
        if replay:
            replay.wrap(llm, classifier)
        wiki = Wiki(cfg, llm=llm, classifier=classifier, steps=CROW)
        rows, stopped = [], False
        for qid, question in questions:
            doc = doc_of.get(qid)
            if not doc or not final.get(doc):  # a question on a document this run did not file
                continue
            if spent[0] > a.max_usd:
                stopped = True
                print(f"  stopped: spent ${spent[0]:.3f} > ${a.max_usd}", file=sys.stderr)
                break
            researcher = wiki.researcher()
            got = [n.rel for n in researcher.steps.select(researcher.ctx, researcher.store.load(), question)]
            want = final[doc]
            rows.append(
                {
                    "id": qid,
                    "doc": doc,
                    "want": want,
                    "got": got,
                    "hit": want in got,
                    "rank": got.index(want) + 1 if want in got else None,
                    "fallback": any(d.decider == "llm" and d.step == "select" for d in researcher.ctx.decisions),
                }
            )
            if replay:
                replay.save()
    n = len(rows)
    share = lambda f: sum(map(f, rows)) / n if n else None  # noqa: E731
    return {
        "run": run.name,
        "questions": n,
        "hit": share(lambda r: r["hit"]),
        "hit@1": share(lambda r: r["rank"] == 1),
        "hit@3": share(lambda r: (r["rank"] or 99) <= 3),
        "mrr": share(lambda r: 1 / r["rank"] if r["rank"] else 0.0),
        "llm_fallback": share(lambda r: r["fallback"]),
        "returned_mean": share(lambda r: len(r["got"])),
        "usd_real": round(spent[0], 4),
        "stopped": stopped,
        "rows": rows,
    }


def pct(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.0f}%"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CROW's select step on the final wiki of runs of incremental.py")
    p.add_argument("runs", type=Path, nargs="*", help="run folders written by incremental.py")
    p.add_argument("--questions", type=Path, help="[{id, question}]")
    p.add_argument("--cases", type=Path, help="[{id, doc}]")
    p.add_argument("--make-cases", type=Path, metavar="CORPUS", help="write the cases file from a folder of .md files and stop")
    p.add_argument("--n", type=int, default=60, help="--make-cases: how many documents (default 60)")
    p.add_argument("--seed", type=int, default=9, help="--make-cases: the seed (default 9)")
    p.add_argument("--out", type=Path, default=Path("retrieval.json"), help="where the results go as JSON (--make-cases: the cases file)")
    p.add_argument("--cache", type=Path, default=None, help="the replay cache file (default: retrieval-cache.json beside the first run)")
    p.add_argument("--max-usd", type=float, default=0.05, help="stop a run once the calls really made cost more (default 0.05)")
    p.add_argument("--dry-run", action="store_true", help="fake models that cost nothing, to try the script")
    a = p.parse_args(argv)
    if a.make_cases:
        return make_cases(a.make_cases, a.n, a.seed, a.out)
    if not (a.runs and a.questions and a.cases):
        p.error("give --questions, --cases and at least one run (or --make-cases)")
    doc_of = {c["id"]: c["doc"] for c in json.loads(a.cases.read_text(encoding="utf-8"))}
    questions = [(q["id"], q["question"]) for q in json.loads(a.questions.read_text(encoding="utf-8")) if q.get("question")]
    replay = None if a.dry_run else Replay(a.cache or a.runs[0].parent / "retrieval-cache.json", {"llm", "classifier"})
    print(f"{len(questions)} questions, {len(a.runs)} runs; " + ("free (fake models)" if a.dry_run else f"at most ${a.max_usd} a run"))
    results = {}
    for run in a.runs:
        res = ask_run(run, questions, doc_of, a, replay)
        results[run.name] = res
        mrr = "-" if res["mrr"] is None else f"{res['mrr']:.2f}"
        print(
            f"{run.name}: {res['questions']} questions, hit {pct(res['hit'])}, hit@1 {pct(res['hit@1'])}, hit@3 {pct(res['hit@3'])}, "
            f"MRR {mrr}, LLM fallback {pct(res['llm_fallback'])}, real spend ${res['usd_real']}"
        )
    a.out.write_text(json.dumps({"questions": len(questions), "runs": results}, indent=1), encoding="utf-8")
    print(f"results: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
