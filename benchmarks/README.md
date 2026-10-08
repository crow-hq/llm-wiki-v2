# Benchmarks

Measures of how the CROW filer decides, built during task 6 (stability and reproducibility of the decisions). They call
real models and cost real money: each script prints its estimate first, and `--dry-run` tries them for free.

## Corpora (`benchmarks/corpus/`)

| corpus | what | used for |
|---|---|---|
| `growth/` | 30 short synthetic documents (≤ 1.6k characters) for the Lumen demo wiki: existing topics, updates, ambiguous, new topics in groups; `labels.json` | stability and accuracy of the decisions on short documents |
| `long2/` | 15 long documents (10-55 pages, up to 194k characters): several topics, a misleading native summary, revisions, a mailbox without headings; planted facts by position (`labels.json`: `facts`), written by `make_long2_corpus.py` | long documents: placement, merges, which facts survive into the notes |
| `deep/` | a three-level reorganisation of the demo wiki (`deep/base/`, built by `make_deep_demo.py`, no model) and 52 documents (the growth ones remapped plus 22 new, `make_deep_corpus.py`) | the routing fallback on a deep tree |

## Scripts

- `stability.py` — files a corpus N times and reports agreement between runs and accuracy against the labels
  (`--corpus`, `--only`, `--runs`, `--variant`, `--votes`, `--fallback-menu`, `--keep`, `--recompute`, …; see its docstring).
- `note_facts.py` — which planted facts of `long2` survive into the kept wikis (recall by kind and position, beyond 100k,
  superseded values, invented numbers). Free.
- `verdict.py` — the verdict between two series of runs: one-sided permutation tests on the runs (pair agreement as the
  series-mean pairwise ARI). Free.
- `replay.py` — a replay cache of model answers (`stability.py --cache llm classifier`), so a series pays only for the calls a
  variant changes.
- `ingest_parallel.py` — time and quality of `ingest_many` against the serial ingest.
- `make_long2_corpus.py`, `make_deep_demo.py`, `make_deep_corpus.py` — generators (the corpora are committed; they are run
  only to change them; `--check` is free).

## Comparing with `main`

Measure both sides **at the same time** (providers and prices change during the day) and each from its own tree:

    git worktree add --detach ../wt-main origin/main
    cp benchmarks/stability.py benchmarks/replay.py benchmarks/ingest_parallel.py ../wt-main/benchmarks/
    cp -r benchmarks/corpus ../wt-main/benchmarks/
    (cd ../wt-main && python -u benchmarks/stability.py --runs 10 --out main.json) &
    (python -u benchmarks/stability.py --runs 10 --out branch.json) &
    wait
    python benchmarks/verdict.py main.json branch.json

`stability.py` imports `llmw2` from its own tree (and exits otherwise): with an editable install of another checkout a
worktree run would measure the wrong code. For long documents add `--corpus benchmarks/corpus/long2 --keep DIR` and score
the kept wikis with `note_facts.py`. Compare costs in **output tokens** (in the result JSON, per operation) as well as
dollars: the price per token on OpenRouter can double between days.

## Known limits of the measures

- The corpora are synthetic. The facts of `long2` are exact strings, so a note that copies the source (a 72k note for a
  71k document) finds them all: read `recall_headline_summarized` with `note_ratio` and `copied_share`, not `fact_recall`
  alone. A paraphrased fact ("2.9k €") counts as lost.
- With 5 runs per side only large effects are visible; 10+10 for decisions on the growth corpus, 4+4 at least on `long2`.
- Labels are strict: `folder_accuracy` counts a subfolder of the right folder as wrong (`folder_prefix_accuracy` does not),
  and `new_accuracy` counts any created folder, single-document ones included.
