# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `wiki.ingest_many(items, concurrency=, on_result=)` files a batch of `Item`s, preparing up to `concurrency` documents at once (fetch, extract, the "unchanged" check and `summarize`) and filing them one at a time, in order of their original's modified time, under the wiki's lock. It returns a `BatchReport` (`results`, `not_done`, `stopped`). A model failure stops the batch: the documents before the failed one are filed, the rest are reported in `not_done`. On a benchmark of 8 runs per side (CROW mode, the demo corpus) filing with 4 at once took half the time of filing one at a time, with folders, notes, merges, links and `check()` within the spread of the serial runs.
- `Item` and `BatchReport` are exported from `llmw2`. `Librarian.prepare` and `Librarian.file` are the two phases of `Librarian.ingest`.
- `concurrency` in `WikiConfig` (`OKF_CONCURRENCY`, the settings page; 1 to 16, default 4) and `llmwiki2 sync --concurrency N`.
- `GET /health` reports `concurrency`. The server reads and summarizes up to that many uploads at once and files them one at a time, and the page's filing queue sends that many at once.
- `SyncReport.stopped`: why a sync ended before the last page.

### Changed

- The steps API is declared stable. `extract` and `summarize` may run in parallel on different threads and must be thread-safe; the other ingest steps run one document at a time, under the lock.
- `sync` files each page of changes with `ingest_many`: one change per origin (the last), removals first, items of a known version not fetched. A model failure no longer raises: `sync` returns the report with `stopped` set and keeps the cursor of the last finished page, and `llmwiki2 sync` exits with 2.
- The calls of a custom `extract` are now counted in the `usage` of the `IngestResult`.
- `ingest` and `ingest_file` no longer hold the wiki's lock while the model summarizes; they take it to file. The wiki's lock also holds between threads of one process, Windows included, so the server no longer wraps ingests in a lock of its own.

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
- `POST /ingest` accepts an optional `origin` object.
- Package metadata on PyPI: authors, keywords, classifiers, and project URLs (homepage, repository, issues, paper, changelog).
- A release workflow that builds the package and publishes it to PyPI with trusted publishing when a `v*` tag is pushed. A manual run publishes to TestPyPI.
- Customizable steps. The ingest and ask flows stay fixed. Each step is a function that a `Steps` set can replace: `extract`, `summarize`, `route`, `name_folder`, `match`, `consolidate`, `merge` and `relate` for ingest, and `select` and `answer` for ask. `CLASSIC` and `CROW` are the two ready-made sets. Pass your own with `Wiki(cfg, steps=replace(CROW, name="...", summarize=...))`. The API is provisional until ingests run in parallel.
- `StepContext`, given to every step: the models, prompts, config and `decide()`. Model calls a custom step makes through it are counted in usage and decisions like the built-in ones.
- `StepError`: a step that returns something the flow cannot take stops the ingest or the question with an error naming the step, before anything is written.
- `fields`: `ingest` and `ingest_file` accept `fields=`, user data the steps read as `ctx.source.fields`. `Candidate.fields` and `NoteDraft.fields` are written to the note frontmatter under `fields:`.
- `Route.create`: a route can ask for a whole folder path in one ingest, one `(name, description)` per level. Existing levels are reused.
- Library documentation in `docs/library/` (the public API and the customizable steps) and runnable examples in `examples/`.

### Changed

- The license is declared as the SPDX expression `AGPL-3.0-or-later`, which requires setuptools 77 or later to build.
- README links are absolute, so they work on the PyPI project page.
- The repository moved to https://github.com/crow-hq/llm-wiki-v2 and all project URLs point there.
- `wiki.origin`, the `unchanged` check and `sync` no longer reread every raw copy and the whole note tree for each item: an in-memory index rereads only the files that changed since the last look.
- Every note source entry records the step set that wrote it (`steps: {name, hash}`), and the ingest log line ends with `(name hash)` instead of `(mode)`.
- `CrowLibrarian` and `CrowResearcher` are gone. CROW is the classic step set with its decision steps replaced, and the steps live in `llmw2.agents.steps_classic` and `llmw2.agents.steps_crow`. Classic and CROW behave as before.
- The tags of a new note are now cleaned the same way as on a merge, whichever `summarize` wrote them.

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
