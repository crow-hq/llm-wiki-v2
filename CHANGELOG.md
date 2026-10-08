# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (round 6, long sources: stable route, less waste)

- The CROW `route` of a source longer than twice `note_chars` reads the deterministic state without the native summary (`source_state(..., native=False)`; sections and openings only), not the candidate's compact state: stable and less misled by a wrong summary. `STATE_VERSION` is 2.
- `summarize_part` asks for `max(MIN_PART, ceil(1.3 * cap))` characters (not `note_chars`); a reply cut shorter than `cap // 2` is asked once more with `note_chars`. `SUMMARY_VERSION` is 7.
- `merge` of a source longer than twice `note_chars` reads the source directly (`librarian/merge_source`, one retry if unchanged while the source has new values), then the lost-values check. `CONSOLIDATE_SOURCE_MAX` now lives in `steps_classic`.

### Changed (round 5, parity with main on long documents)

- `classifier/route` is main's wording again (the "New subfolder" rewording blocked new-topic folders).
- `summarize` of a source that fits in one call has no length rule; `SUMMARY_VERSION` is 5 and `LENGTH_RULE_FROM` is gone.
- The one-call `summarize` output cap follows the source length (`max(note_chars, len(source))`); `SUMMARY_VERSION` is 6. Measured 2026-10-07: with the note cap, doc 06 of long2 kept 0.50-0.58 of its facts, with the higher cap 1.0.
- CROW `consolidate` reads the source state for a source up to twice `note_chars` long, the candidate's compact form for a longer one (`steps_crow.consolidate_state`).
- `merge` no longer shows the origin's modified date. If its draft is unchanged, or keeps fewer than half of the new codes, numbers and names of the incoming text (`steps_classic.incoming_values`), it is asked once more with the new prompt `librarian/merge_source` (main's merge, from the source).
- `merge` keeps the existing note's values: if the draft drops more than `LOST_MAX` (0.1) of the values of the existing body, it is asked once more with the same prompt, naming up to 80 lost values and asking for the newer one next to each ("previously ..."); the draft that loses fewer is kept.
- The CROW `route` (and its fallback) reads the candidate's compact state for a source longer than twice `note_chars`, the deterministic source state otherwise (`steps_crow.routing_state`); a state set by `summarize` is kept.
- Benchmark metric: a thousands group no longer starts right after a letter, a digit or a hyphen.

### Added

- The decision hash includes the fallback menu's character limit. `benchmarks/stability.py` gains `--tau-cons`, the `related` of each document and the metric `update_merged_or_linked`.
- The part notes of a long source (`librarian/summarize_part`) are plain text, not JSON, and a part cut by the output cap keeps what was written (a warning, no retry, no failure); `SUMMARY_VERSION` is 3.
- Long documents with the LLM alone. `summarize` reads a source that fits in one call (`read_chars` less 6000) whole, as before, and a longer one in parts: `librarian/summarize_part` takes notes on each block in turn and `librarian/summarize_combine` writes the note from them. `split_blocks(text, max_chars)` in `llmw2.agents.state` cuts the text at sections, then paragraphs, then sentences, losing nothing. A part that fails fails the step. A document is still one note.
- `read_chars` (`OKF_LLM_READ_CHARS`, "Characters per call" in the settings page, kept per provider): the characters one request to the LLM may hold. Empty means automatic: 100000 on OpenRouter, OpenAI and Gemini, 16000 on Ollama and custom servers (`Provider.read_chars`). `WikiConfig.effective_read_chars`.
- `note_chars` (`OKF_NOTE_CHARS`, "Note length" in the settings page): about how long a note body may be. Empty means 10000, and at most half of what one call holds. `shared/note_rules` asks the model for it; `StepContext.ask_json` and `ask_text` pass `note_chars` to every prompt. A body over 1.2 times it is logged, never cut.
- `StepContext.candidate`: the note `summarize` wrote, set by the `Librarian` before `route`.
- The settings page warns, when testing an Ollama model, if its `num_ctx` is smaller than a call can need.
- `fallback_menu` (`OKF_FALLBACK_MENU`, default `visited`) for CROW, an extension of the paper. With `tree`, the routing fallback shows the LLM the folders the classifier visited and then the other folders of the wiki (at most 8000 characters; the visited ones are never left out), so it can pick a folder the classifier never reached. In the settings page next to `votes`.
- Stability benchmark: more metrics against the labels (`new_accuracy`, `single_doc_folders`, merges into a note created in the same run), several corpora (`--corpus` repeated, `--only`), variants by `--prompts` and `--fallback-menu`, a replay cache of model answers (`--cache`) and `--recompute` to recompute the metrics of a saved result for free.
- `votes` (`OKF_VOTES`, default 1) and `vote_band` (`OKF_VOTE_BAND`, default 0.05) for CROW. With `votes` above 1, a routing decision close to `tau_route`, to a tie between two folders or to `tau_path` is asked `votes` times and the classifier's answers are averaged, so its noise flips the decision less often between runs. The `route_step` trace ends with `(n votes)` when it was averaged. The stability benchmark has a `vote` variant.
- CROW `route` reads a deterministic state built from the source text (the text whole if short; else its summary or introduction, outline and excerpts) instead of the LLM-written summary, which changes on every run. `Candidate.state` holds it and a custom `summarize` may fill it. `StepContext.route_state(cand)` returns it. `OKF_CLASSIFIER_STATE_CHARS` and the state's version are part of the decision hash.

### Changed

- The note-length rule of `shared/note_rules` is now the placeholder `{{length_rule}}`, and `librarian/summarize` leaves it out for a source of at most twice `effective_note_chars` characters (`LENGTH_RULE_FROM`); `summarize_combine` and `merge` keep it. Measured in `benchmarks/results/2026-10-07-merge-check/`: with the rule in phase 5 the summaries of short documents were more compact and the classifier's consolidate confidence fell (growth document 07 merged 2/20 with it, 7/20 without it, 15/20 on `main`; document 14 11/20 against 18/20 on `main`). `SUMMARY_VERSION` is 4.
- The output cap of `merge` is the length of the existing note plus `note_chars` (in characters, then as every cap): a merge rewrites the whole note, and a note of 36k characters failed at the plain note cap.
- `librarian/merge` keeps the existing note's title, summary, headings and order of sections, and its wording where nothing changed, adding the incoming facts in place: a note merged by mistake was being rewritten around the incoming one, and later documents no longer matched it.
- `librarian/summarize_part` lists the facts of the part first (names, numbers, dates, amounts, codes, each with a few words of context), then prose in the space left. Its output cap is the one of a note, not of its `cap`: the model never kept to `cap` (46 part notes of 46 on 2026-10-06, a median of 3.6 times), and `cut_notes` trims them.
- `merge` goes from note to note: it reads the existing note and the incoming note (the candidate, with its source's title, location and modified date), no longer the whole source, so it fits the call however long the source is. The prompt `librarian/merge` has new placeholders (`incoming_*`) and no longer says never to shorten the note: its length comes from `note_rules`. A step that calls `classic.merge` without a `ctx.candidate` still gets the source, cut to what the call can hold.
- `source_chars` is deprecated (a `DeprecationWarning` when it is set) and is no longer a cut: when `llm.read_chars` is not set, an explicit `source_chars` is the length above which `summarize` reads in parts. `examples/multi_pass_summarize.py` still cuts at it by hand.
- The decision hash includes `read_chars`, `note_chars` and `SUMMARY_VERSION`, and the text of `note_rules` and `merge` changed: runs before this one hash differently.
- `LLM.json` retries once when a reply is not valid JSON or fails the schema. The retry repeats the original request with a note on what was wrong instead of sending the broken reply back, and asks for `response_format` `json_object` (on OpenRouter with `require_parameters`, so only providers that support it answer). A provider that refuses `response_format` is asked again without it, and the model remembers that.
- A reply cut by the output limit (`finish_reason` `length`) raises a `ModelError` naming the step and the output tokens at once, with no retry. Every line of the usage log records the `finish_reason`.
- Output cap per call, after a model ran away to 131072 tokens. `LLM.chat`, `json` and `complete` take `max_tokens`; every call through `StepContext.ask_json` and `ask_text` sets it from the new `reply_chars` (default `note_chars`; decisions use 2000): `reply_tokens(chars)` is 3 times the length at 3.5 characters a token, at least 1024, plus 32000 when the model may think (`LLM.may_think`). The field is `max_completion_tokens` on OpenAI (`Provider.max_tokens_field`); a provider that refuses it is asked again without it; an `extra_body` cap wins. **Behaviour change:** a note body is still only asked to stay near `note_chars`, but a reply over about 3 times that is now cut by the provider, asked once more shorter (`ReplyCut`, a `ModelError`), and then fails the source, where before it was filed with a warning (or ran away). Direct `ctx.llm` calls stay uncapped. The cap is not in the decision hash.
- The notes of a part of a long source (`summarize_part`) over their cap are cut at a sentence end, else at a space, keeping at least half the cap (`cut_notes`), where a single early newline used to leave 33 characters of 53377. `SUMMARY_VERSION` is 2, so the decision hash changes.
- Benchmark `note_facts.py`: a thousands group is exactly three digits not followed by another, so "Q2 2026" is no longer read as one number (it had inflated `invented_numbers`).

### Fixed

- `LLM.chat`: the fallback to the least reasoning now holds for requests already in flight. With several threads (`ingest_many`), requests built with the off switch before the first 400 "Reasoning is mandatory" used to raise once another thread had switched; they are now resent once with the new body (the switch is under a lock).

## [0.3.0] - 2026-09-30

### Added

- Provenance on ingest. `ingest` and `ingest_file` accept an `Origin` (source, account, id, and optionally link, version and modified time) that says where a document comes from.
- Raw copies record a content hash, and the origin and a save sequence number when one is given. Notes cite the hash and origin of their sources.
- A new `unchanged` ingest result: when a document with the same origin was already filed with the same content or version, no model call is made and nothing is written.
- `wiki.origin(key)` returns the latest raw copy cited by a note and the notes for one original.
- `wiki.mark_removed(key)` records that an original was deleted at its source. The raw copies are marked and the notes are kept.
- Source connector protocol (`SourceConnector`, `Change`, `ChangeBatch`) and `sync(wiki, connector)`, which files what changed since the last run and resumes from the saved cursor. Unreadable items are listed in the report and the sync continues.
- `LocalFolderSource`, a connector for a local folder (for example an rclone-synced OneDrive). Hidden files, symbolic links and unsupported file types are ignored.
- `llmwiki2 sync FOLDER` files a folder's documents and keeps the wiki in step with it. `--json` prints the full report. The exit code is 1 if any item failed.
- `sync` files each page of changes as one batch: one change per origin (the last), removals first, items of a known version not fetched. A model failure stops it with `SyncReport.stopped` set and the cursor of the last finished page kept, so the next run resumes there; `llmwiki2 sync` then exits with 2.
- `POST /ingest` accepts an optional `origin` object.
- Package metadata on PyPI: authors, keywords, classifiers, and project URLs (homepage, repository, issues, paper, changelog).
- A release workflow that builds the package and publishes it to PyPI with trusted publishing when a `v*` tag is pushed. A manual run publishes to TestPyPI.
- Customizable steps. The ingest and ask flows stay fixed. Each step is a function that a `Steps` set can replace: `extract`, `summarize`, `route`, `name_folder`, `match`, `consolidate`, `merge` and `relate` for ingest, and `select` and `answer` for ask. `CLASSIC` and `CROW` are the two ready-made sets. Pass your own with `Wiki(cfg, steps=replace(CROW, name="...", summarize=...))`. `extract` and `summarize` may run in parallel on different threads and must be thread-safe; the other ingest steps run one document at a time, under the lock.
- `StepContext`, given to every step: the models, prompts, config and `decide()`. Model calls a custom step makes through it, a custom `extract` included, are counted in usage and decisions like the built-in ones.
- `StepError`: a step that returns something the flow cannot take stops the ingest or the question with an error naming the step, before anything is written.
- `fields`: `ingest` and `ingest_file` accept `fields=`, user data the steps read as `ctx.source.fields`. `Candidate.fields` and `NoteDraft.fields` are written to the note frontmatter under `fields:`.
- `Route.create`: a route can ask for a whole folder path in one ingest, one `(name, description)` per level. Existing levels are reused.
- `wiki.ingest_many(items, concurrency=, on_result=)` files a batch of `Item`s, preparing up to `concurrency` documents at once (fetch, extract, the "unchanged" check and `summarize`) and filing them one at a time, in order of their original's modified time, under the wiki's lock. It returns a `BatchReport` (`results`, `not_done`, `stopped`). A model failure stops the batch: the documents before the failed one are filed, the rest are reported in `not_done`. On a benchmark of 8 runs per side (CROW mode, the demo corpus) filing with 4 at once took half the time of filing one at a time, with folders, notes, merges, links and `check()` within the spread of the serial runs.
- `Item` and `BatchReport` are exported from `llmw2`. `Librarian.prepare` and `Librarian.file` are the two phases of `Librarian.ingest`.
- `concurrency` in `WikiConfig` (`OKF_CONCURRENCY`, the settings page; 1 to 16, default 4) and `llmwiki2 sync --concurrency N`.
- `GET /health` reports `concurrency`. The server reads and summarizes up to that many uploads at once and files them one at a time, and the page's filing queue sends that many at once.
- `llm.seed` (`OKF_LLM_SEED`) and `llm.pin_provider` (`OKF_LLM_PIN_PROVIDER`, comma-separated), both on the settings page under "Model parameters". The seed is sent with every request. On OpenRouter the pinned providers answer in order, with no fallback to others and only if they support every parameter sent (`provider: {order, allow_fallbacks: false, require_parameters: true}`); other providers ignore the pin with a warning. An `extra_body["provider"]` wins. Neither is set by default.
- Every line of the usage log records the `provider` that served the call (from the response, when it says), the `temperature` and the `seed`.
- `Prompts.fingerprint()` and `decision_hash(steps, cfg, prompts)` in `llmw2.agents.steps`.
- Library documentation in `docs/library/` (the public API and the customizable steps) and runnable examples in `examples/`.

### Changed

- The license is declared as the SPDX expression `AGPL-3.0-or-later`, which requires setuptools 77 or later to build.
- README links are absolute, so they work on the PyPI project page.
- The repository moved to https://github.com/crow-hq/llm-wiki-v2 and all project URLs point there.
- `wiki.origin`, the `unchanged` check and `sync` no longer reread every raw copy and the whole note tree for each item: an in-memory index rereads only the files that changed since the last look.
- Every note source entry records the step set that wrote it (`steps: {name, hash}`), and the ingest log line ends with `(name hash, model)` instead of `(mode)`, plus `, via providers` when providers are pinned. The hash covers the step code, the text of every prompt in use, the models, temperature, seed, pinned providers, the CROW thresholds, `max_depth`, `max_links`, `mode` and `summarize`; keys, addresses, timeouts and concurrency do not change it. `Steps.hash` is still the hash of the code alone.
- `CrowLibrarian` and `CrowResearcher` are gone. CROW is the classic step set with its decision steps replaced, and the steps live in `llmw2.agents.steps_classic` and `llmw2.agents.steps_crow`. Classic and CROW behave as before.
- The tags of a new note are now cleaned the same way as on a merge, whichever `summarize` wrote them.
- `ingest` and `ingest_file` no longer hold the wiki's lock while the model summarizes; they take it to file. The wiki's lock also holds between threads of one process, Windows included, so the server no longer wraps ingests in a lock of its own.

### Fixed

- A document whose ingest was interrupted after its raw copy was saved is no longer treated as unchanged forever. Only raw copies cited by a note count for `unchanged`, `wiki.origin` and `sync`, and the raw copy is now saved right before the note is written.

## [0.2.0] - 2026-09-24

The first release of the `llm-wiki-v2` repository, built on Google's Open Knowledge Format (OKF). Versions up to 0.2.0 were released under the Apache License 2.0 and stay available under it. Later versions are AGPL-3.0-or-later (see NOTICE).

### Added

- Librarian that files documents (`.txt`, `.md`, `.pdf`) into a wiki of Markdown notes and a researcher that answers questions citing the notes used.
- CROW mode, where a small classifier decides where notes go and which are read, and classic mode, where the LLM decides. A separate provider can be set for the classifier.
- Web UI served by `llmwiki2`, with a settings page for the provider and keys, drag-and-drop upload, writing notes from the page, and a brain view of the folder graph that refreshes as documents are filed.
- Deleting notes, folders or the whole wiki from the page.
- Any OpenAI-compatible provider (OpenRouter, OpenAI, Gemini, Ollama, others).
- `llmwiki2 demo`, an example wiki to browse without a key.
- Retrieval benchmark measuring accuracy, latency and cost as the wiki grows, and a Docker setup.

### Changed

- Relicensed from Apache-2.0 to AGPL-3.0-or-later, with a Contributor License Agreement for contributions.
- The package and module are named `llmw2` and the command `llmwiki2`.
- Dropped the OKF fork (own document parser, no viewer, no reference agent) and the Bifrost gateway.

[0.3.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.3.0
