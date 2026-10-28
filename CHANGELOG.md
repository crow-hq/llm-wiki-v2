# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-10-28

### Added

- The wiki is the long-term memory of Claude Code, Codex or any MCP client, documents already filed included, with the `mcp` extra (`pip install 'llmwiki2[mcp]'`; the Docker image has it): the server answers at `/mcp` (Streamable HTTP, stateless, plain JSON replies, through the official MCP SDK). `recall` answers from the wiki as **Ask** does, citing its notes; `remember` files a text like a note written on the page, through CROW (routed, merged and linked); `read_note` reads one note. It is off by default: **Memory** in the page's header starts and stops it (the setting `mcp`, or `OKF_MCP=1`) and shows the `claude mcp add` and `codex mcp add` lines to copy. While it is off, its tools answer how to turn it on, so a client added before Start needs no reconnecting. `/mcp` sits behind the server's guard (a loopback Host when bound to localhost, no cross-site request); without the extra, it answers 501 with what to install, and the page says the same.
- The client can do the memory's model work, on its own plan, with no key in the wiki: Claude or GPT for everything (the setting `mcp_models = "client"`, or **Claude or GPT for everything** in the Memory page's **Which models do the work**). CROW runs as always, but each model call is handed to the client as a request (`models/relay.py`), and the tool `reply` hands back the answers until the result comes. A `decide` request is the System One questions with the typed-decision prompt of TypeSafe's system-one-adapter (MIT, see NOTICE; `models/typed.py`), which Claude Code runs with a Haiku subagent; a `write` request is the wiki's own prompt, for the client's model. The decision prompt ends with the reply's shape keyed by the question ids, and a reply is read as a light model writes it: a lone answer keyed by its question's text, a reply cut before its last brackets (closed for decisions only, not for the writer's replies), percentages (a value above 1, none above 100), or a choice given as one of its labels (that label, for sure). Any other value outside 0 to 1, or anything else, falls back to the LLM; nothing is clamped. The requests carry the documents' text: the client is told to read it as data and to answer with text alone, using no tool. A request left unanswered for 5 minutes fails the work, and `log.md` names `mcp-client` as the model of what the client filed. `POST /settings/memory` turns the memory on or off and switches its models with no key check.
- New setting `plan` (`OKF_PLAN`): `"writer"` (the default) or `"groups"`. With `"groups"`, `Wiki.ingest_many` (and so `sync`) in CROW mode on a wiki that has no folders when the batch begins plans every window in code, with no model call, instead of asking `librarian/plan` (`Wiki.plan_groups`), with any provider. It is meant for a small local writer (about 1B parameters or less), which plans poorly; a capable writer plans better than the groups. The groups: the documents that share a subject (TF-IDF over their whole text, cosine similarity, average-linkage clustering) are grouped; a group whose documents resemble the sources of an existing folder's notes goes there, any other to a new top-level folder named after the two words that weigh most in its documents and described by the titles of its first three (a group whose words a folder name cannot spell is routed instead). The documents of a group are filed in its folder without routing (decision `route` by `code`); a document in no group is routed as before. Folders made this way end their "Created folder" line with `, by plan`. A wiki that has folders when the batch begins still gets `librarian/plan_more`, and with `"writer"` the writer's `librarian/plan` plans as before.

### Changed

- A `custom` classifier whose model is not one of Laya's checkpoints (`typed-decisions`, `auto`, `english`, `multilingual`), such as a small classifier on Ollama, merges at `tau_cons` 0.7 instead of Laya's 0.26, the value it was measured with. Laya keeps its preset; a `tau_cons` set by the user still wins.
- `librarian/summarize` shows what each field of its JSON reply should hold (`"<a specific title written from the content>"`, `"<one sentence: what the note answers>"`, ...) instead of `"..."`, for every writer.

### Fixed

- A note no longer vanishes when its summary or a field holds a `---` line (the frontmatter ended there, so the note failed to parse and left the tree and its index). A byte order mark no longer hides the frontmatter, and form feeds and U+2028 in a body stay as written. Text written by hand after a note's See-also list is kept when links change: it moves above the list, which stays one section. A note that is not UTF-8 is skipped with a warning and named by `check`, instead of stopping the whole wiki; an `index.md` or `log.md` saved as Latin-1 is read as such and written back as UTF-8.
- A model failure in `relate`, which runs once the note is written, no longer fails the ingest: the note is filed and logged without See-also links, with a `relate` / `error` decision in the trace. Before, the note was left on disk with no log line, the sync stopped, and the next sync skipped it, so the links never came. `{"related": null}` reads as no links, and `"/a.md"` and `"a.md"` count as one link.
- A merge no longer loses the incoming note when it does not fit what is left of the call after the existing note: it was cut to that room (to nothing for a note that filled it), the model merged what was left, and the note was logged as merged. Now no model is asked: the incoming body is added to the note as a section headed by its title, and the tags are joined.
- With a writer on Ollama, a reply to summarize, merge or name_folder that is invalid, cut, or refused by its check no longer stops the ingest: the note keeps the source text (decision `summarize` / `verbatim`), the incoming note is added to the matched one as a section (`merge` / `appended`), or the folder is named after the note, from the first words of its title, summary or file name (`notes` if none has words), with the summary as description. Each is logged as a warning. An endpoint that fails (server down, wrong model, timeout) still stops it, and with any other provider a reply error is raised as before. Ollama's 500 "token repeat limit reached", sent when it stops a model that keeps repeating itself, is an invalid reply after the usual retries, on every provider: in a batch it is that document's result, not an endpoint failure.
- The Ollama preset sends `reasoning_effort: "none"`, and `"low"` once if the model refuses it, as the OpenRouter preset does. Before, a thinking model thought on every call, and every capped reply got the reasoning room on top, which a small model that ran away wrote to the end. When the caller's own `extra_body` is that off switch, an Ollama reply's cap gets no reasoning room; with other providers the cap is computed as before.
- A reply whose first JSON object does not parse is no longer read through an object nested in it, which took a broken plan for one of its folders, or for none: the broken object is skipped whole. A plan or split reply without `folders` is an invalid reply and is retried, not an empty plan; `{"folders": []}` still means none. In classic mode a `find_match` reply broken on the retry too is now an error, where a nested object could read as no match.
- A local classifier server that takes at most 64 questions per request, or whose context a batch exceeds, no longer fails it: when it answers that the questions "must contain 1-64 fields", or that the "context size has been exceeded", the batch is asked again in halves, and halves of those if needed; a single question that exceeds the context still fails. No other request changes.
- With a writer on Ollama, a note's title that is only its source's file name, numbering included (`26 new hire checklist` for `26-new-hire-checklist.md`, or a file name ending in `-md`), or that has no letter or digit in any script, is replaced by the source's first heading, unless that is one too; a summary with no letter or digit (`...`) by the first line of the note's text; and a title or summary made only of words of the summarize prompts (the reply format copied) by the source's or the note's first lines. Each is logged as a warning. A hosted writer's title and summary, and a source filed verbatim (`OKF_SUMMARIZE=false`), are kept as before.
- With a writer on Ollama, a folder name or description copied from the prompt rather than written from the documents (the reply format `/area/subject`, the naming rule, an example such as `engineering`, file-name codes, the words of the parent's path) is not used: a folder of a plan or a split so named or described is left out, a `name_folder` name gives way to the first words of the note (never words only of the prompts), and a `name_folder` description to the note's summary. Documents in a script that folder names cannot spell (Cyrillic, Chinese...) leave nothing to compare with: names and descriptions are taken as before. A hosted writer's names are taken as before.
- With a writer on Ollama, a `route_fallback` call that fails no longer stops the ingest: the note goes where the classifier's best path ended, or into a new top-level folder when there is none (decision `route` by `code`, logged as routed by fallback). With any other provider it is raised as before.
- With a writer on Ollama, the retry of an invalid JSON reply asks for the reply's own JSON schema (`response_format` of type `json_schema`), which Ollama holds the model to, instead of any JSON object. The first request and other providers are unchanged.
- A reply holding a lone UTF-16 surrogate (`\ud8a1`, written as text or as a JSON escape) no longer stops the batch when its note is written as UTF-8: each one becomes U+FFFD.

## [0.6.0] - 2026-10-19

### Changed

- CROW filing is one classifier Choice among every folder of the tree (and `None of these`), not a walk level by level. The per-level routing, with its beam and votes, decides only for an empty tree, on `None of these` or on a classifier error; `tau_route` now also decides whether the second folder's branch joins the match pool, and `votes` applies to the per-level routing only. When one request cannot hold every folder, the BM25 cut applies to this Choice too (decision `route_cut`). New decisions: `route` (the folder picked), `route_direct` (`None of these` or an error) and, with its own prompt, `classifier/route_direct`. Measured on four corpora, two writing models, an empty and a full wiki: 0.74 to 0.94 times the cost per document, 1.2 to 2.5 times the agreement with the real folders, retrieval on a par (about 96% for both), and 14% to 53% of the notes below the first level against 0% to 13%. One run per configuration, and retrieval is near its ceiling.
- `Wiki.ingest_many` (and so `sync`) in CROW mode files in windows of 50 documents. Before a window is filed, when two or more of its documents are to be filed, an LLM call lays out the folders they need (`librarian/plan` for an empty wiki, `librarian/plan_more` for one that has folders: only new folders, never a rename, a move or a new description). A plan that fails is logged and the window goes on with the folders it has. The window and the plan's limits are constants, not settings. `Wiki.plan` can be called on its own and raises on a bad reply.
- After a document filed alone (`ingest`, `ingest_file`, `/ingest`, `/upload`, a window with one document), CROW tries once to split its folder when it holds 12 notes directly, then at 24, 36 and so on: one LLM call (`librarian/split`) opens subfolders for the subjects at least two of the notes share and moves those notes there. The attempt is written in `log.md` as a "Split" line, also when it opens nothing, so a new `Wiki` does not try the same multiple again. A split never fails the ingest; its decision is `split`. Without a batch to plan, it takes the notes below the first level from 0% to 30%-44%, with agreement and retrieval within a few points of what they were. The limit is a constant, not a setting.
- `audit_routes` (`llmwiki2 audit`, `POST /audit`) asks the same Choice among every folder as the filing, instead of the per-level routing, and proposes only a move to another branch: a folder below the note's own, or above it, is no longer a proposal. `--min-confidence` (0.7) is compared with the Choice's top probability, a scale that has not been recalibrated.
- `taxonomy_health` (`llmwiki2 health`, `GET /taxonomy`) does not count folders laid out by a plan, a plan_more or a split as new subfolders: their "Created folder" lines end `, by plan`, `, by plan_more` or `, by split`.
- CROW routing on a level with more subfolders than one classifier request holds (255 options for Jev, the `request_chars` of a classifier with a smaller context) offers the ones a BM25 ranking against the note puts first, as many as fit, instead of failing over to the LLM. A level that fits is asked as before. With Jev on a level of 500 and 1,000 folders the right folder is among those offered for 51 and 49 of 51 documents, and the folder taken is right for 38 and 37, as with about 250 folders all offered at once; asking every folder in groups and a final costs two to four times as much for the same result.

### Fixed

- The Docker image builds again: `.dockerignore` lets in `LICENSE` and `LICENSES/`, which the package build needs; the README and `docker-compose.yml` no longer point to `/docs`, which the server turns off.
- Emptying the wiki removes the sync cursors too, so a `sync` afterwards files every document again instead of none. Deleting a note or a folder, or emptying the wiki, also removes the archived versions of the notes deleted (`Deleted.archived` lists them); the confirmation on the page says so.
- Saving settings with a wiki folder that cannot be made (a file, or not writable) is refused with 422 and saves nothing; before, the bad folder was saved and the server failed on it, then and at every start. A missing key is refused before the folder is made. `~` in `OKF_BUNDLE`, `OKF_USAGE_LOG`, `OKF_PROMPTS_DIR` or the wiki folder on the settings page is the home folder, not a folder named `~`; a usage log's folder is made when it does not exist.

## [0.5.0] - 2026-10-16

### Added

- Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), OpenDocument text (`.odt`) and spreadsheets (`.ods`), read with the standard library only (no new dependency): documents become Markdown with their headings, lists, tables, footnotes and speaker notes; the type is read from the content, and a damaged or hostile file (zip or XML bomb, broken XML) is an input error that does not stop a sync. A slide's heading is its title, `## Results (slide 4)`, so the summary slide of a long deck can be used as its summary; a title repeated on the next slides is not repeated, slide numbers and dates are left out and footers come once at the end. Tables of contents are left out, fill-in lines (`______`, `.......`) become `___`, and an ODT "heading" over 80 characters or with a blank to fill is a paragraph. Excel numbers keep 15 significant digits (`0.0551`, not `5.5100000000000003E-2`), whole numbers all their digits, and percentages read as such (`5.51%`).
- A spreadsheet is filed one note per sheet: the note describes the sheet (written from its first rows), the sheet's rows are kept as CSV in the raw copy (title rows above the header become a `Caption:` line, which the note's writer reads too), and the researcher adds to a sheet's note the rows a question needs (the whole sheet when it fits, otherwise the closest rows and the totals of its numeric columns). The notes of one workbook link to each other; a sheet deleted from the workbook is marked removed. `IngestResult.parts` holds the other sheets' results.
- The local folder source skips Office lock files (`~$name.docx`).
- `benchmarks/office/`: where the 2,629 real Office/ODF files of the readers' bench come from (NapierOne, Common Crawl), with their sha256, the scripts that fetch and select them, the free check between two versions and the criteria of the measures; the files themselves are not committed.

### Changed

- `Wiki.ingest_many` (and so `sync`) reads spreadsheets first, on worker threads, and reports one item per sheet (`"plan, Budget.csv"`); sync counts are per sheet. A workbook has no record under its own origin key (`Wiki.origin` returns None for it), so a sync that lists it fetches it again; sheets whose text is unchanged cost no model call.
- CI runs the tests in parallel (`pytest -n auto`, with `pytest-xdist` among the dev dependencies): the Windows jobs had taken the CI from about 3 to about 8.5 minutes.

### Fixed

- One unusable model reply no longer stops a sync: a document whose reply stays invalid after the retry (malformed JSON, no content, cut at the limit) is that document's error, the rest of its page is filed, and the sync stops at the end of the page without saving its cursor, so the next run retries that document alone (`llmwiki2 sync` still exits with 2). Two in a row still stop at once, as a model that cannot answer. New `ReplyInvalid` (a `ModelError`; `ReplyCut` is one).

## [0.4.4] - 2026-10-15

### Fixed

- Saving settings on Windows with Python 3.11 and 3.12: "Save and start" stopped with `module 'os' has no attribute 'fchmod'` (Windows has `os.fchmod` only from Python 3.13). The file is now made owner-only with `os.chmod`, and a failed save no longer leaves a temporary file behind.

### Changed

- CI runs the tests on Windows too, with Python 3.11, 3.12 and 3.13.

## [0.4.3] - 2026-10-14

### Changed

- `pip install llmwiki2` is the whole install: the web page and the HTTP API (`fastapi`, `uvicorn`) are in the base dependencies, so `llmwiki2` and `llmwiki2 demo` start without an extra. Before, they stopped with `No module named 'fastapi'`. `llmwiki2[server]` still installs, now the same as `llmwiki2`.
- README: two steps, `pip install llmwiki2` first and uv as the alternative for those without Python or who want it kept apart.

## [0.4.2] - 2026-10-14

### Changed

- The README, which PyPI shows: a demo GIF, a "Why not RAG?" section, a FAQ (what runs locally, what Jev is, whose benchmarks these are, why AGPL and a CLA) and the pip install next to uv.
- Releases: the GitHub release is created by the release workflow after PyPI, with the version's CHANGELOG section as notes and a copy of the CLA signatures attached. To release, push the tag only.

## [0.4.1] - 2026-10-14

### Changed

- Published on PyPI as `llmwiki2`, the same name as the command: `uv tool install "llmwiki2[server]"`, or `uvx "llmwiki2[server]" demo` to try it. The module is still imported as `llmw2`.

### Fixed

- NOTICE: the OKF files are licensed by Google LLC under the Apache License 2.0, not AGPL-3.0.

## [0.4.0] - 2026-10-12

### Added

- Move a note to another folder: `wiki.move_note(path, folder)`, `llmwiki2 move NOTE FOLDER`, `POST /note/move` and a Move button on the note page. Every link to and from the note (See-also entries and Markdown links) is rewritten, the indexes of both folders too, and archived copies get the new `archived_from`. Edit a folder's description: `wiki.describe_folder(path, description)`, `llmwiki2 describe FOLDER "DESCRIPTION"`, `POST /folder/describe` and an Edit description button in the sidebar.
- Long documents with the LLM alone. A source that fits in one call is summarized whole, as before; a longer one is read in parts: `librarian/summarize_part` takes notes on each block in turn (plain text, the facts first) and `librarian/summarize_combine` writes the note from them. `split_blocks(text, max_chars)` in `llmw2.agents.state` cuts at sections, then paragraphs, then sentences, losing nothing. A document is still one note. The notes of a part over their share are cut at a sentence end, and a part cut by the output limit keeps what it wrote. A warning is logged when the note lacks values (codes, numbers, two-word names) of its part notes. A part that fails fails the step.
- `read_chars` (`OKF_LLM_READ_CHARS`, "Characters per call" in the settings page, kept per provider): the characters one request may hold. Empty means automatic: 100000 on OpenRouter, OpenAI and Gemini, 16000 on Ollama and custom servers (`Provider.read_chars`, `WikiConfig.effective_read_chars`).
- `note_chars` (`OKF_NOTE_CHARS`, "Note length" in the settings page): about how long a note body may be. Empty means 10000, and at most half of what one call holds. It is asked of the model, never enforced: a body over 1.2 times the length asked is logged, not cut.
- `note_length` (`OKF_NOTE_LENGTH`): `proportional` or `fixed`. A proportional note is about a quarter of its source (`NOTE_RATIO`), never below `note_chars`, up to what a call holds and `NOTE_MAX` (45000); a merge asks for the existing note plus that share (`WikiConfig.target_note_chars`). `fixed` is a note of about `note_chars`.
- `revisions` (`OKF_REVISIONS`): `replace` or `merge`. A source that revises the one document a note cites replaces the note (a summary of the new version, a "Changes from the previous version" section with each changed value and "previously ...", and a "From the previous version" section with the sentences that still hold), and the old body goes to a hidden `.archive/` (`WikiStore.archive_note`, `previous_version` in the frontmatter). When a question asks about an earlier version, the researcher reads the archived copy and answers with the earlier value. When the guard does not pass (not the same document, or a body not written by code alone), the source is merged as usual. The guard is recorded as a decision whose `decider` is the new value `"code"`.
- The web page of a note revised in place has a Versions block that links its archived version; an archived copy is labelled as such, links the current note and the version before it, and has no delete button.
- Every note records `generated.body_hash`, the hash of its main body, on create and on every update.
- `votes` (`OKF_VOTES`, default 1) and `vote_band` (`OKF_VOTE_BAND`, default 0.05) for CROW. With `votes` above 1, a routing decision close to `tau_route`, to a tie between two folders or to `tau_path` is asked `votes` times and the classifier's answers are averaged, so its noise flips the decision less often between runs. The `route_step` trace ends with `(n votes)` when it was averaged.
- `fallback_menu` (`OKF_FALLBACK_MENU`, `visited` or `tree`, default `visited`) for CROW, an extension of the paper. With `tree`, the routing fallback shows the LLM the folders the classifier visited and then the other folders of the wiki (at most 8000 characters; the visited ones are never left out), so it can pick a folder the classifier never reached. It is in the settings page next to `votes`.
- A deterministic state for routing. CROW `route` reads a digest built from the source text (the text whole if short; else its summary or introduction, outline and excerpts) instead of the LLM-written summary, which changes on every run. `Candidate.state` holds it and a custom `summarize` may fill it; `StepContext.route_state(cand)` returns it. For a source longer than twice `note_chars` the digest leaves out the source's own summary. `StepContext.candidate` is the note `summarize` wrote, set before `route`.
- An output cap on every model call made through `StepContext.ask_json` and `ask_text`, after a model ran away to 131072 tokens. Both take `reply_chars` (default `note_chars`; decisions use 2000), and `LLM.chat`, `json` and `complete` take `max_tokens`. With `keep_cut`, `ask_text` and `LLM.complete` return a reply cut by the cap as written, with a warning, and append it to `cut` when given. `LLM.chat` takes `json_mode`, which asks for `response_format` `json_object`. `reply_tokens(chars)` is 3 times the length at 3.5 characters a token, at least 1024, plus 32000 when the model may think. The field is `max_completion_tokens` on OpenAI (`Provider.max_tokens_field`); a provider that refuses a cap is asked again without it, and an `extra_body` cap wins.
- `llm.seed` (`OKF_LLM_SEED`) and `llm.pin_provider` (`OKF_LLM_PIN_PROVIDER`, comma-separated), both on the settings page under "Model parameters". The seed is sent with every request. On OpenRouter the pinned providers answer in order, with no fallback to others and only if they support every parameter sent; other providers ignore the pin with a warning. An `extra_body["provider"]` wins. Neither is set by default.
- Every line of the usage log records the `provider` that served the call (when the response says), the `temperature`, the `seed` and the `finish_reason`.
- `Prompts.fingerprint()` and `decision_hash(steps, cfg, prompts)` in `llmw2.agents.steps`.
- The settings page warns, when testing an Ollama model, if its `num_ctx` is smaller than a call can need.
- A stable `id` in the frontmatter of every new note (12 hex characters). Updates, moves and archived copies keep it; older notes get none; it is never used in links or indexes. `Wiki.find_note(id)`; `check` reports two notes with the same `id`.
- Routing audit (CROW): `wiki.audit_routes(min_confidence=0.7, apply=False)`, `llmwiki2 audit [--min-confidence X] [--apply]` and `POST /audit`. Routes each note of the tree again with `route`'s classifier decisions and never its LLM fallback, and proposes the notes whose folder differs, not deeper in the note's own branch, with a confidence of at least `min_confidence` (default `AUDIT_MIN`); `apply` moves them. No LLM call, never automatic. It returns `RouteProposal`s; `taxonomy_health` below returns `FolderHealth`s. All three are exported from `llmw2`.
- Taxonomy health: the CROW ingest log line ends `, routed by fallback` when the LLM fallback chose the folder, and `wiki.taxonomy_health(min_filed=5, max_share=0.3)`, `llmwiki2 health` and `GET /taxonomy` read `log.md` (no model) for the new subfolders and fallbacks per folder and month, flagging the folders with too many.
- Benchmarks in `benchmarks/` (stability of the filing decisions, filing errors judged blind by readers, facts that survive into long notes, replay of model answers), the corpora they use and the stored judgements, with a README.

### Changed

- **Behaviour change:** the confidence of a classifier Choice is computed by the library from the probabilities, on the scale the CROW thresholds are calibrated on (`llmw2.models.classifier.confidence`), and the server's `confidence` field is ignored. Servers disagree on what that field means (Ollama and Laya send one measure, some OpenRouter providers another), so the same answer took different decisions on different servers; with a classifier on Ollama, CROW never merged an update. One set of thresholds now works on every server. On Jev nothing changes but a decision exactly at a threshold: 27 of 17,558 stored answers, none a merge. A server answer without `confidence` is no longer malformed.
- **Behaviour change:** Laya's preset `tau_cons` is 0.26 instead of 0.05: the same merges on the new scale. A custom classifier's `tau_route` (0.6) is now read on Jev's scale too; if you set `OKF_TAU_CONS` or `OKF_TAU_ROUTE` for a custom server, set them again on it.
- **Behaviour change:** `revisions` defaults to `replace`: a source that revises the document a note cites replaces the note and archives the old body. `OKF_REVISIONS=merge` keeps 0.3.0's merge.
- **Behaviour change:** `note_length` defaults to `proportional`: a note grows with its source, to about a quarter of it. `OKF_NOTE_LENGTH=fixed` keeps 0.3.0's length.
- **Behaviour change:** every note now records `generated.body_hash`.
- **Behaviour change:** the decision hash changes: runs before this one hash differently. It now also covers `read_chars`, `note_chars`, `note_length` (unless `fixed`), `relate_rare_values`, `answer_conflicts` and `strict_new_folders` (only when `false`), the size and version of the routing state, the fallback menu's character limit, `SUMMARY_VERSION`, the seed and the pinned providers, and the text of `note_rules` and `merge` changed. The ingest log line ends with `(name hash, model)` instead of `(name hash)`, plus `, via providers` when providers are pinned.
- **Behaviour change:** a reply cut by the output limit is asked once more, shorter; if it is cut again, the call raises `ReplyCut` (a `ModelError`) and the source fails, where before a long note was filed with a warning (or ran away). Direct `ctx.llm` calls stay uncapped, and the cap is not in the decision hash.
- `summarize` reads a source that fits in one call without a length rule, and its output cap follows the source length. The length rule of `shared/note_rules` is the placeholder `{{length_rule}}`, left empty by `librarian/summarize` and `librarian/merge_source`.
- CROW `route` and `consolidate` read the source, not the LLM-written summary. `route` reads the deterministic state; `consolidate` reads it for a source up to twice `note_chars`, and the candidate's compact form for a longer one.
- `merge` goes from note to note: it reads the existing note and the incoming note (the candidate, with its source's title and location), so it fits the call however long the source is, and keeps the existing note's title, summary, headings and wording where nothing changed. A source longer than twice `note_chars` is merged from its text (`librarian/merge_source`). If the draft is unchanged, or keeps fewer than half of the new values (codes, numbers, names) of the incoming text, it is asked once more from the source; if it drops more than 10% of the values of the existing note, it is asked once more naming up to 80 of them with the newer value next to each ("previously ..."). Its output cap is the existing note plus the share the source adds. A step that calls `classic.merge` without a `ctx.candidate` still gets the source, cut to what the call can hold.
- `source_chars` is deprecated (a `DeprecationWarning` when it is set) and is no longer a cut: when `llm.read_chars` is not set, an explicit `source_chars` is the length above which `summarize` reads in parts.
- `LLM.json` retries once when a reply is not valid JSON or fails the schema. The retry repeats the request with a note on what was wrong and asks for `response_format` `json_object` (on OpenRouter with `require_parameters`); a provider that refuses `response_format` is asked again without it, and the model remembers that.
- **Behaviour change:** CROW `relate` also reads, as candidates, up to 40 notes anywhere in the wiki that share rare values (codes, amounts, dates, names held by few notes) with the new or updated note, skipping notes that already have 12 See-also links. This changes the links CROW adds; classic is unchanged. `OKF_RELATE_RARE_VALUES=false` keeps 0.3.0's `relate`.
- **Behaviour change:** CROW `ask` asks one more typed question (`ask/conflict`) when two or more notes go to the answer: if the notes seem to disagree on the answer, the question gets a hint to report every value, which note gives which and which source is more recent. This changes the CROW answer; classic is unchanged. `OKF_ANSWER_CONFLICTS=false` keeps 0.3.0's answer input.
- **Behaviour change:** CROW routing opens a new subfolder only when the classifier is sure of it (`strict_new_folders`, on by default): a `New subfolder` answer under `tau_route` cannot end a path and its probability goes to `Here` (at the root the other options are renormalised), and `classifier/route` asks whether a folder's description covers the note. Fewer folders with a single note and fewer notes of different subjects sharing a folder, fewer LLM fallbacks; a note on a new subject more often stays in the parent folder. `OKF_STRICT_NEW_FOLDERS=false` keeps 0.3.0's routing, whose prompt is now `classifier/route_open`.
- **Prompt overrides** (`prompts_dir`, `OKF_PROMPTS_DIR`) written for 0.3.0 need a look. `librarian/merge` now receives the incoming note as `incoming_title`, `incoming_summary`, `incoming_tags`, `incoming_body` and `incoming_from`; a 0.3.0 override that reads `{{source_text}}` gets it empty when there is a candidate, so the draft usually misses the new values and the merge is asked again from the source with `librarian/merge_source`, one call more each time. An override of `classifier/route` replaces the strict prompt; with `strict_new_folders` off the routing reads `classifier/route_open`, so a 0.3.0 override of `classifier/route` must be renamed to stay in use. `shared/note_rules` has the `{{length_rule}}` placeholder: `ask_json` and `ask_text` fill it, a direct `ctx.prompts.render` of a template that includes it must pass it.

### Fixed

- `LLM.chat`: the fallback to the least reasoning now holds for requests already in flight. With several threads (`ingest_many`), requests built before the first 400 "Reasoning is mandatory" used to raise once another thread had switched; they are now resent once with the new body.
- Text read from a `.txt` or `.md` file has `\n` line endings: the same file saved on Windows and elsewhere is the same text, with the same hash. A file with `\r\n` line endings filed by 0.3.0 hashes differently once: `ingest_file` and `ingest_many` with an `Origin` that has no version file it again; `sync` with `LocalFolderSource` skips a file whose version is unchanged.
- Files are written with `\n` line endings on every system, and a write that fails on Windows because another thread or process has the file open is retried briefly.

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
- Library documentation in `docs/library/` (the public API and the customizable steps) and runnable examples in `examples/`.

### Changed

- The license is declared as the SPDX expression `AGPL-3.0-or-later`, which requires setuptools 77 or later to build.
- README links are absolute, so they work on the PyPI project page.
- The repository moved to https://github.com/crow-hq/llm-wiki-v2 and all project URLs point there.
- `wiki.origin`, the `unchanged` check and `sync` no longer reread every raw copy and the whole note tree for each item: an in-memory index rereads only the files that changed since the last look.
- Every note source entry records the step set that wrote it (`steps: {name, hash}`), and the ingest log line ends with `(name hash)` instead of `(mode)`.
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

[0.7.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.7.0
[0.6.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.6.0
[0.5.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.5.0
[0.4.4]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.4
[0.4.3]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.3
[0.4.2]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.2
[0.4.1]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.1
[0.4.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.0
[0.3.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.3.0
