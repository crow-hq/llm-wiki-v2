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

The real corpora are not committed (licence and size): only the scripts that select them, the source and a pin.

| corpus | source and pin | selection |
|---|---|---|
| GitLab handbook, marketing (CC BY-SA 4.0) | https://gitlab.com/gitlab-com/content-sites/handbook, head `2c77a1f5b8c8a80cb7b5151ff11cfad84f98bfbb` | `t9_handbook_select.py`: pages first added from 2024-01-01, labels from the folder they were added in, renames as events |
| PostHog handbook (`contents/` is MIT) | https://github.com/PostHog/posthog.com, head `2c6eef5ed782311675b28993df3c70fbbcbf2372` | `posthog_select.py`: 120 pages of the handbook's folders (seed 9, at most 20 000 bytes, none at the root, no `_snippets`), labels from the folder the page is in at the head, `manifest.json` with the sha256 of every file |
| Enron mail | https://www.cs.cmu.edu/~enron/, `enron_mail_20150507.tar.gz`, sha256 `b3da1b3fe0369ec3140bb4fbce94702c33b7da810ec15d718b3fadf5cd748ca7` | `t9_enron_select.py`: one mailbox, the folders its owner made, labels from the folder |
| Office and OpenDocument files (2,629: NapierOne and Common Crawl `CC-MAIN-2026-39`) | per file its sha256 and source: `office/SOURCES.md` | `office/fetch.py` fetches them; `office/measure.py` compares two versions of the readers on them, free |

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
- `t9_handbook_select.py`, `t9_enron_select.py`, `posthog_select.py` — build the real corpora from their sources (above).
- `incremental.py` — files a corpus into an empty wiki with the library of its own tree, in one `ingest_many` or one `ingest`
  per document (`--mode crow|classic`, `--flow batch|single`, `--cache`, `--max-usd`, `--dry-run`; see its docstring).
- `incremental_score.py` — shape, cost and agreement with gold folders (AMI, pairwise F1) of the runs of `incremental.py`. Free.
- `retrieval.py` — CROW's `select` step on the final wiki of a run, for questions written about its documents. Costs a few calls
  per question; `--make-cases` is free.

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

## Incremental filing, retrieval and the PostHog corpus

`stability.py` files documents into the demo wiki and compares runs. `incremental.py` files a whole corpus into an **empty**
wiki, as a user who starts from a folder of documents would, and `incremental_score.py` and `retrieval.py` measure the result.
It uses the library's own steps and defaults, with no custom route and no prompt overrides, so it measures the code of the
tree it sits in. The two flows are the two ways to file: `--flow batch` is one `ingest_many` (the library's own windows, with
whatever it does before each), `--flow single` one `ingest` per document in the same order.

**The corpus.** The PostHog handbook has real nested folders to compare with. It is not committed (licence and size):

    git clone --filter=blob:none --no-checkout https://github.com/PostHog/posthog.com.git posthog-repo
    python benchmarks/posthog_select.py --repo posthog-repo --head 2c6eef5ed782311675b28993df3c70fbbcbf2372 --out posthog-corpus

`posthog-corpus/manifest.json` has the folder of every page (the gold) and the sha256 of every file: check it against a
rerun of the script. Any other folder of `.md` files works too; `--gold` takes `{file: folder}` or any manifest with a `docs`
list of `{file, folder}`.

**Three arms.** As above: main classic, main CROW and the branch (CROW), each from its own tree, the same models, at the same
time. The cache keeps what each arm asked, so an interrupted run goes on without paying twice, and keeps the original cost
of each call, so a rerun still reports what the run cost:

    C=$PWD/posthog-corpus R=$PWD/runs K=$PWD/cache     # absolute: the main arms run from another folder
    for m in MODEL_1 MODEL_2 MODEL_3; do
      n=${m##*/}
      for flow in batch single; do
        (cd ../wt-main && python -u benchmarks/incremental.py --corpus $C --out $R/main-classic-$flow-$n --mode classic --flow $flow --model $m --cache $K/main-classic-$n.json) &
        (cd ../wt-main && python -u benchmarks/incremental.py --corpus $C --out $R/main-crow-$flow-$n --mode crow --flow $flow --model $m --cache $K/main-crow-$n.json) &
        python -u benchmarks/incremental.py --corpus $C --out $R/branch-crow-$flow-$n --mode crow --flow $flow --model $m --cache $K/branch-crow-$n.json &
      done
    done
    wait

`--dry-run` tries any of them for free with fake models. `--limit N` keeps the first N documents; a run stops, and says so, once
the calls really made cost more than `--max-usd`. Each run folder holds `meta.json`, `docs.jsonl`, `calls.jsonl`,
`moves.jsonl` and `wiki/` (see the docstring). A document's final note is the one its ingest wrote, followed through the moves
logged after it, because folders are split and notes move after they are filed.

**Scoring.** Per model and flow, the runs of the three arms side by side, the costs as ratios of main classic:

    python benchmarks/incremental_score.py $R/*-batch-$n --gold $C/manifest.json --baseline $R/main-classic-batch-$n

It prints documents and errors, the share of notes below the first level, the largest folder, folders with one note, notes
at the root, AMI and pairwise co-location F1 against the gold, the cost per document (original cost of every call, replayed
ones included) and the cache hit rate. Reference: in the real folders, 30% of the PostHog pages sit in a folder that has
subfolders.

**Retrieval.** `retrieval.py` asks the library's `select` step, on a copy of a run's final wiki, a question for each of a
sample of its documents, and counts a hit when the document's final note is among the notes returned (also hit@1, hit@3, MRR
and the share answered through the LLM fallback). The questions are written by a model that reads only the document:

    python benchmarks/retrieval.py --make-cases $C --n 60 --seed 9 --out cases.json
    # one question per case, with benchmarks/judgements/retrieval-questions-prompt.md, into questions.json: [{id, question}]
    python benchmarks/retrieval.py --questions questions.json --cases cases.json $R/branch-crow-batch-$n $R/main-crow-batch-$n

Run it from the tree of the arm whose wikis it reads. The repository holds only the prompt: for a corpus that is not
committed, neither the questions (they quote the documents) nor the texts are, and the results name a question by its id.

## Results of 0.6.0: CROW against CROW 0.5.0 and classic

The PostHog corpus above (120 pages, 30 real folders), filed into an empty wiki in one batch (`--flow batch`), with two
writing models and Jev as the classifier. Three arms: **classic** (the LLM routes, one level at a time), **CROW 0.5.0** (the
classifier walks the folders one level at a time) and **CROW 0.6.0** (one Choice among every folder, a plan of the folders
before each batch window). One run per arm and model.

What the columns measure:

- **AMI**: adjusted mutual information between the folder each page's note ends in and the page's real folder. 0 is no
  better than chance, 1 is the real tree; it does not reward many small folders.
- **F1**: pairwise co-location. For every pair of pages, are they in the same folder in the wiki and in the real tree?
  F1 of those pairs; it falls both when one folder swallows unrelated pages and when related pages are scattered.
- **Below the first level**: share of notes in a folder at depth 2 or deeper. In the real tree, 30% of the pages sit in a
  folder that has subfolders.
- **Folders** (in brackets, those with one note) and **largest folder**: notes in the biggest one.
- **Cost**: cost per document, as a ratio of classic with the same model (every call at its original cost).
- **Retrieval**: share of 60 questions, each written blind from one page, for which `select` returns that page's note among
  the 8 it reads. One question is 1.7 points.

| Model | Arm | AMI | F1 | Below the first level | Folders (one note) | Largest folder | Cost | Retrieval |
|---|---|---|---|---|---|---|---|---|
| DeepSeek v4.1 flash | classic | 0.39 | 0.33 | 42% | 40 (19) | 21 | 1 | 97% |
| | CROW 0.5.0 | 0.43 | 0.32 | 3% | 15 (3) | 35 | 0.52× | 98% |
| | **CROW 0.6.0** | **0.51** | **0.45** | 53% | 23 (2) | 16 | **0.45×** | 93% |
| GLM 5.3 flash | classic | 0.41 | 0.30 | 21% | 23 (8) | 42 | 1 | 98% |
| | CROW 0.5.0 | 0.38 | 0.29 | 0% | 11 (0) | 39 | 0.75× | 98% |
| | **CROW 0.6.0** | **0.51** | **0.42** | 29% | 17 (0) | 26 | **0.65×** | 97% |

The differences:

- **Against CROW 0.5.0.** Agreement with the real folders is higher with both models (AMI +0.07 and +0.14, F1 +0.13 with
  both) at 0.86 to 0.87 times the cost. 0.5.0 never opened a second level from an empty wiki (0% to 3% of the notes below
  the first level, the largest folder holding 35 to 39 notes); 0.6.0 puts 29% to 53% there, around the real tree's 30%.
- **Against classic.** Classic also builds a deeper tree, but with DeepSeek half of its 40 folders hold one note; 0.6.0
  agrees more with the real folders (AMI +0.11 to +0.12) at 0.45 to 0.65 times its cost.
- **Retrieval** is between 93% and 98% for every arm. The lowest, CROW 0.6.0 with DeepSeek, is 3 questions of 60 below
  0.5.0, within what one run can show; retrieval at this size is near its ceiling and tells the trees apart little.

On four corpora (the coffee corpus, Enron, the GitLab and PostHog handbooks), on an empty and a full wiki, in batches and
with single documents, CROW 0.6.0 against 0.5.0 is summarised in [docs/wiki/crow.md](../docs/wiki/crow.md#measured-with-the-flat-routing).
To run the table again: the three arms and the scoring above, with `--gold posthog-corpus/manifest.json` and
`--baseline` the classic run of the same model.

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
- The readers' test (a judge sees a document's title and opening and the folder tree, and picks two folders) counts strictly:
  the pick must be the document's folder, or, in the lenient count, one of its ancestors. That favours flat trees, which have
  few folders to choose among; read it next to the placement judgement, never alone.
- True retrieval saturates: on wikis of 120 to 450 notes `select` finds the right note in about 93% to 100% of the questions for
  every arm, so it separates arms only when something goes wrong, not by how well a tree is organised.
- Incremental runs are one run per arm and model, not repeated: only large differences count, and a difference seen on one
  model alone neither promotes nor rejects a change. The agreement with the gold depends on the gold being the only good
  tree: a page can be where a reader would look for it and still count as a miss.
- The labels of `t9a`/`t9b` come from two annotators; where they disagreed the document is `ambiguous` and either answer
  counts as right.
