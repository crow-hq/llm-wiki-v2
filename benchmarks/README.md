# Benchmarks

Measures of how the CROW filer decides, built during task 6 (stability and reproducibility of the decisions) and task 9
(a three-arm comparison on a larger corpus). They call real models and cost real money: each script prints its estimate
first, and `--dry-run` tries them for free.

## Corpora (`benchmarks/corpus/`)

| corpus | what | used for |
|---|---|---|
| `growth/` | 30 short synthetic documents (≤ 1.6k characters) for the Lumen demo wiki: existing topics, updates, ambiguous, new topics in groups; `labels.json` | stability and accuracy of the decisions on short documents |
| `t9a/`, `t9b/` | 60 short documents each (`a01-…`, `b01-…`), six simulated months of the Lumen roastery written blind from `t9-writing/` by two different models; `labels.json` merged from two blind annotators (`annotator-*.json`, rules in `t9_labels.py`); `manifest.json` gives each document its simulated month | with `growth/`, the 150 short documents of the three-arm comparison: placement, merges, new folders, folders with a single document |
| `t9-writing/` | what the writers and annotators of `t9a`/`t9b` were given: the demo tree, the facts true for every document, story A and story B (one per half), the labelling guide | rewriting or extending the halves the same way |
| `long2/` | 15 long documents (10-55 pages, up to 194k characters): several topics, a misleading native summary, revisions, a mailbox without headings; planted facts by position (`labels.json`: `facts`), written by `make_long2_corpus.py`. Some values are corrupted or contradicted on purpose | long documents: placement, merges, which facts survive into the notes |
| `deep/` | a three-level reorganisation of the demo wiki (`deep/base/`, built by `make_deep_demo.py`, no model) and 52 documents (the growth ones remapped plus 22 new, `make_deep_corpus.py`) | the routing fallback on a deep tree |
| `dup/` | a base wiki (`dup/base/`: the demo plus 66 generated notes) and 40 documents (`dup/docs/`): 15 duplicates, 10 contradictions, 10 traps (a shared value, a different subject) and 5 distractors, each paired with a note of the base; `labels.json` gives the pair, the slot, the values and, for contradictions, a question and the expected answer. Written by `make_dup_corpus.py` | See-also links between duplicates, answers that report both values of a contradiction, no false link on a trap |

Two real corpora are not committed (licence and size): only the script that selects them, the source and a pin.

| corpus | source and pin | selection |
|---|---|---|
| GitLab handbook, marketing (CC BY-SA 4.0) | https://gitlab.com/gitlab-com/content-sites/handbook, head `2c77a1f5b8c8a80cb7b5151ff11cfad84f98bfbb` | `t9_handbook_select.py`: pages first added from 2024-01-01, labels from the folder they were added in, renames as events |
| Enron mail | https://www.cs.cmu.edu/~enron/, `enron_mail_20150507.tar.gz`, sha256 `b3da1b3fe0369ec3140bb4fbce94702c33b7da810ec15d718b3fadf5cd748ca7` | `t9_enron_select.py`: one mailbox, the folders its owner made, labels from the folder |

## Scripts

- `stability.py` — files one or more corpora N times and reports agreement between runs and accuracy against the labels
  (`--corpus` repeated, `--only`, `--runs`, `--model`, `--concurrency`, `--base`, `--variant`, `--votes`, `--fallback-menu`,
  `--prompts`, `--keep`, `--recompute`, `--cache`, …; see its docstring).
- `note_facts.py` — which planted facts of `long2` survive into the kept wikis (recall by kind and position, beyond 100k,
  superseded values, invented numbers). Free.
- `filing_error.py` — filing errors per 150 short documents (E1-E6: wrong area, too high, new subject misfiled,
  superfluous folder, merge, out of place) from the runs of `stability.py` on `growth`, `t9a` and `t9b`, with the blind
  judgements of readers kept in `judgements/`; `cases` and `merge` add the choices of new runs. Free; see its docstring.
- `verdict.py` — the verdict between two series of runs: one-sided permutation tests on the runs (pair agreement as the
  series-mean pairwise ARI). Free.
- `replay.py` — a replay cache of model answers (`stability.py --cache llm classifier`), so a series pays only for the calls a
  variant changes.
- `ingest_parallel.py` — time and quality of `ingest_many` against the serial ingest.
- `make_long2_corpus.py`, `make_deep_demo.py`, `make_deep_corpus.py`, `make_dup_corpus.py` — generators (the corpora are
  committed; they are run only to change them; `--check` is free). `corpus_common.py` holds what they share.
- `t9_labels.py` — merges the two blind annotations of a synthetic half into one `labels.json`.
- `t9_handbook_select.py`, `t9_enron_select.py` — build the two real corpora from their sources (above).

## Comparing with `main`: three arms, three models

A change is measured against **main classic**, **main CROW** and the **branch** (CROW), on **at least three writing models**,
and judged on all of them together: a result seen on one model alone neither promotes nor rejects a change. Run the arms
**at the same time** (providers and prices change during the day), each from its own tree:

    git worktree add --detach ../wt-main origin/main
    cp benchmarks/*.py ../wt-main/benchmarks/
    cp -r benchmarks/corpus ../wt-main/benchmarks/
    C="--corpus benchmarks/corpus/growth --corpus benchmarks/corpus/t9a --corpus benchmarks/corpus/t9b"
    for m in MODEL_1 MODEL_2 MODEL_3; do
      n=${m##*/}
      (cd ../wt-main && OKF_MODE=classic python -u benchmarks/stability.py --runs 2 --model $m $C --keep keep/main-classic-$n --out main-classic-$n.json) &
      (cd ../wt-main && OKF_MODE=crow python -u benchmarks/stability.py --runs 2 --model $m $C --keep keep/main-crow-$n --out main-crow-$n.json) &
      (OKF_MODE=crow python -u benchmarks/stability.py --runs 2 --model $m $C --keep keep/branch-crow-$n --out branch-crow-$n.json) &
    done
    wait

`verdict.py` gives the verdict between two series run on one corpus (default `growth`; `--corpus` for another), e.g.
`python benchmarks/verdict.py main-crow.json branch-crow.json`.

`stability.py` imports `llmw2` from its own tree (and exits otherwise): with an editable install of another checkout a
worktree run would measure the wrong code. For long documents use `--corpus benchmarks/corpus/long2 --keep DIR` and score
the kept wikis with `note_facts.py`. Compare costs as ratios between the arms and in **output tokens** (in the result JSON, per
operation), not in dollars alone: the price per token on OpenRouter can double between days.

On OpenRouter one model is often served by several providers, and they do not behave the same: one provider of a model
looped on merges until the output cap in our runs, while the others answered normally. Every line of the usage log
(`--keep` keeps it) records the provider; when the failures of a series gather on one provider, pin the others
for every arm (`OKF_LLM_PIN_PROVIDER` on the branch; 0.3.0 has no pin, so give main the same providers with
`OKF_LLM_EXTRA_BODY='{"provider": {"order": [...], "allow_fallbacks": false}}'`) rather than read the failures as an effect of the change.

## Filing errors judged by readers

The main measure of the comparison on short documents is `filing_error.py`: errors per 150 documents, summed over six
kinds, with the LLM fallbacks beside them as a cost. Labels cannot say everything (a document filed in an unexpected
folder may still be where a reader would look for it), so the doubtful choices are judged blind by two fresh models with
the prompts of `judgements/` and stored by document and folder: a new run pays only for the choices nobody judged yet.

    python benchmarks/filing_error.py score "main classic=main-classic-{m}.json" "main crow=main-crow-{m}.json" \
        "branch=branch-crow-{m}.json" --models MODEL_1,MODEL_2,MODEL_3
    python benchmarks/filing_error.py cases judge/ branch-crow-MODEL_1.json=keep/branch-crow-MODEL_1 ...
    # two judges, separately: judge/readers.json with judgements/reader-prompt.md, judge/purity.json with purity-prompt.md
    python benchmarks/filing_error.py merge judge/ answers-1.json answers-2.json

The `not judged` column says how many choices of a run still wait for a judgement; judge them before reading the table.
The stored judgements come from blind Opus judges (two per case, a document out of place when either says so); judges
of another family give comparable numbers only once they agree with these on a sample of the stored cases.

## What the repository does not score

Links between duplicates and answers to contradictions on the `dup` corpus, revisions and the real corpora were scored
by a harness that is not in the repository (the `dup` corpus is filed and asked by it, not by `stability.py`). A series
run with `--keep` saves what they read (the final wikis, the usage logs and the raw JSON), so it can be scored later.

## Known limits of the measures

- The synthetic corpora are generated. The facts of `long2` are exact strings, so a note that copies the source (a 72k note
  for a 71k document) finds them all: read `recall_headline_summarized` with `note_ratio` and `copied_share`, not
  `fact_recall` alone. A paraphrased fact ("2.9k €") counts as lost, and a value written differently ("€108.00" and "€108")
  or computed from the source (a percentage, a sum) counts as invented: check `invented_numbers` by hand against the source.
- With 5 runs per side only large effects are visible; 10+10 for decisions on the growth corpus, 4+4 at least on `long2`.
- Labels are strict: `folder_accuracy` counts a subfolder of the right folder as wrong (`folder_prefix_accuracy` does not),
  and `new_accuracy` counts any created folder, single-document ones included. CROW opens subfolders more often than
  classic, so read exact-folder accuracy next to the prefix one.
- The labels of `t9a`/`t9b` come from two annotators; where they disagreed the document is `ambiguous` and either answer
  counts as right.
