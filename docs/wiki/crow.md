# CROW mode

CROW (F. Cesarini and M. Sassarini, *CROW: Classifier-Routed Organization of LLM
Wikis*, 2026, [doi:10.5281/zenodo.22900601](https://doi.org/10.5281/zenodo.22900601))
separates deciding from writing: a typed classifier (TypeSafe Jev, over the
System One API) takes every filing and retrieval decision by scoring fixed
options, and the LLM only writes notes, merges, folder names and answers. Each
classifier decision has a threshold, and when the classifier is not confident
enough, or fails, that step goes back to the LLM. Both modes run the same
pipeline and write the same kind of wiki, because `CrowLibrarian` and
`CrowResearcher` override only the decision hooks of `Librarian` and
`Researcher`. It is the default; `OKF_MODE=classic` or `--mode classic` lets the LLM take the
decisions instead. The classifier needs an OpenRouter key: with OpenRouter as provider its key
serves both, otherwise `okf-wiki setup` and the settings page ask for one (or switch to classic).

## Who decides what

The **ingest** steps (`Librarian.ingest`) are listed below. Prompt names are
under `src/okf_wiki/agents/prompts/`.

| Step | Classic decider | CROW decider | Primitive | Threshold | Fallback |
|---|---|---|---|---|---|
| 0 summarize | LLM `librarian/summarize` | same LLM step. `OKF_SUMMARIZE=false` files the source verbatim. | - | - | - |
| 1 route | LLM `librarian/route`, one level at a time | `classifier/route` at each level. Options are the subfolders, `Here`, `New subfolder` (below `OKF_MAX_DEPTH`) and `None of these` (not at the root). A path's score is the geometric mean of its edge probabilities. | Choice | `tau_route`, `beam`, `tau_path` | LLM `librarian/route_fallback` over the folders visited, when no path finishes or the best score is below `tau_path`. On error, LLM `librarian/route`. |
| 2 relevance | LLM `librarian/find_match` | `classifier/note_relevance`: one Noul per note in the candidate folders, batched. The best note wins if its probability is above `tau_ing`. | Noul | `tau_ing` | Below `tau_ing`: no match. On error, LLM `librarian/find_match`. |
| 3 consolidation | LLM `librarian/consolidate` | `classifier/consolidate`: a Choice between `Modify` and `New note`. The note is merged only if the choice is `Modify` with confidence at least `tau_cons`. | Choice | `tau_cons` | When unsure, a new note is written. On error, LLM `librarian/consolidate`. |
| write | LLM `librarian/merge` or `librarian/name_folder` | same LLM step | - | - | - |
| relate | LLM `librarian/relate` | `classifier/relate`: one Noul per candidate. Links go to notes above `tau_link`, at most `OKF_MAX_LINKS` (5). | Noul | `tau_link` | On error, LLM `librarian/relate`. |

In step 2, the candidate folders are every folder on the finished beam paths. In
classic mode, and after a route fallback, they are the chosen folder and its
ancestors.

The **retrieval** steps (`Researcher.ask` = `select` then `answer`):

| Step | Classic decider | CROW decider | Primitive | Threshold | Fallback |
|---|---|---|---|---|---|
| folder routing | LLM `researcher/navigate`. It opens folders from a frontier, at most `OKF_MAX_STEPS` (12), and stops once it has `OKF_MAX_NOTES` (8) notes, or says it is done after selecting at least one (a "done" with nothing selected only closes that folder). | `classifier/retrieval_folder`: one Noul per subfolder, level by level. At each level it keeps the top `retrieval_beam` above `tau_fold`. | Noul | `tau_fold`, `retrieval_beam` | LLM navigation (`Researcher.select`) when no note is selected, or on error |
| note scoring | same `navigate` call, which also selects notes | `classifier/retrieval_note`: one Noul per note in the explored folders (including the root). It keeps the top `k` above `tau_ret`. | Noul | `tau_ret`, `k` | LLM navigation when no note clears `tau_ret`, or on error |
| answer | LLM `researcher/answer`, citing `[path.md]` | same LLM step | - | - | - |

## Thresholds

| Setting | Env var | Default | Rule in the code |
|---|---|---|---|
| `tau_route` | `OKF_TAU_ROUTE` | 0.6 | If Choice confidence is below this, keep the top `beam` options instead of 1 |
| `tau_path` | `OKF_TAU_PATH` | 0.5 | If the best path score is below this, the LLM routes |
| `tau_ing` | `OKF_TAU_ING` | 0.5 | Best note probability must be above this to count as a match |
| `tau_cons` | `OKF_TAU_CONS` | 0.6 | `Modify` confidence must be at least this to merge |
| `tau_fold` | `OKF_TAU_FOLD` | 0.08 | Folder probability must be above this to explore the folder |
| `tau_ret` | `OKF_TAU_RET` | 0.1 | Note probability must be above this to read the note |
| `tau_link` | `OKF_TAU_LINK` | 0.6 | Relatedness must be above this to add a See-also link |
| `beam` | `OKF_BEAM` | 2 | Ingestion: paths kept per uncertain routing step |
| `retrieval_beam` | `OKF_RETRIEVAL_BEAM` | 6 | Retrieval: folders explored per level |
| `k` | `OKF_K` | 8 | CROW retrieval: notes passed to the answer |
| `max_notes` | `OKF_MAX_NOTES` | 8 | Classic retrieval and CROW's navigation fallback: notes read per question |

The ingestion thresholds are the paper's package defaults, never calibrated. The retrieval
values are the ones the paper calibrated on its corpus (§8.4): with 0.5, most right notes
scored below the threshold before their (good) ranking could act, so CROW fell back to LLM
navigation in 79% of questions. With thresholds this low the beam, not the threshold, bounds
exploration. They are still a starting point. As the paper advises, calibrate the
thresholds on a labelled sample of **your own** wiki, and set them again whenever
the classifier model version changes. This is why `OKF_CLASSIFIER_MODEL` is
pinned to `typesafe/jev-1.13`. Choice confidence says how concentrated the
answer is, not whether it is right. With Docker, put these variables in `.env`.

## Calibrating with `result.decisions`

Every `IngestResult` and `Answer` carries a list of `Decision(step, decider,
choice, confidence, fallback)`. You can read it from
`okf-wiki ingest --json`, `okf-wiki ask --json`, or the replies of
`POST /ingest` and `POST /ask`.

| `step` | `choice` | `confidence` holds | Calibrates |
|---|---|---|---|
| `route_step` | options kept at one level | Choice confidence | `tau_route` |
| `route` | `"/folder (Here)"` or `"/folder (New subfolder)"` | path score (`fallback: true` when the LLM chose) | `tau_path` |
| `match` | note path, or `none` | best note probability | `tau_ing` |
| `consolidate` | `modify` or `new` | Choice confidence | `tau_cons` |
| `relate`, `retrieve_folder`, `retrieve_note` | the paths chosen | per-item probabilities in `scores` | `tau_link`, `tau_fold`, `tau_ret` |

To calibrate:

1. Label a sample of sources: the right folder, whether each matches an
   existing note, and whether it should be merged.
2. Ingest the sample into a **copy** of the wiki (ingesting writes files) and
   collect the decisions.
3. For each step, pick the lowest threshold at which the accepted decisions are
   right as often as you need. Set it with the matching `OKF_TAU_*` variable.

For retrieval, the paper's procedure (§8.4): write one question per sampled document
and take the note that cites it as the right note; split them into a calibration and a
held-out set; ask the calibration questions with `OKF_TAU_FOLD=0 OKF_TAU_RET=0` and a wide
`OKF_RETRIEVAL_BEAM`, reading the right folders' and right note's probabilities from
`scores`; set `tau_fold` and `tau_ret` just below a low quantile of them, `retrieval_beam`
to the smallest beam that keeps the right folder, and `k` to the smallest count that holds
the right note; check on the held-out questions, and repeat when the classifier version
changes or the wiki grows.

## Good to know

- **`relate` extends the preprint.** See-also links were outside the preprint;
  the paper's v1.0 describes them (§5.4).
- **Folders are created lazily.** A `New subfolder` route creates the folder,
  named by the LLM with `librarian/name_folder`, only when a new note is written
  into it. If step 3 merges instead, no folder is created, so a merge never
  leaves an empty folder.
- **A classifier outage never fails an ingest or a question** (the LLM must
  still be reachable). Any `ModelError` (HTTP failure after `OKF_CLASSIFIER_ATTEMPTS` tries, default 1, or a
  malformed answer) is logged as a warning and the classic LLM version of that
  hook decides. In the decisions, that step's `decider` is `llm`. A failed route
  also records `choice: "error"`, and a failed retrieval records
  `step: "select"`, both with `fallback: true`.
- **Every decision reads a compact state**, not the full source: the title, the
  summary and the opening passage, up to `OKF_CLASSIFIER_STATE_CHARS` (6000).
  Consolidation also adds the existing note, up to half of
  `OKF_CLASSIFIER_REQUEST_CHARS`. For retrieval, the state is the question.

Known classifier weaknesses:

- **Literal reading.** It matches the wording of folder descriptions and note
  summaries, so vague descriptions route poorly. Keep them specific.
- **Numbers and dates.** It is less reliable at comparing figures, or at telling
  which of two dated statements is newer. That is why the merge text is written
  by the LLM and why an unsure consolidation becomes a new note.
- **Long, irrelevant state.** Extra text lowers accuracy. That is why decisions
  read the compact state instead of the whole source.
