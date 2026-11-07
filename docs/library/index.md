# Using llmw2 as a library

Everything the web page and the command line do is a method of one object,
`Wiki`. This guide covers the public API; how to replace parts of the ingest
and ask flows is in [steps.md](steps.md), and runnable programs are in
[examples/](../../examples/).

## Install

```bash
pip install llmwiki2
```

The HTTP API and the web page come with it.

## Configuration

`WikiConfig` is a pydantic model. Build it yourself, or read it from the
environment:

```python
from pathlib import Path
from llmw2 import Wiki, WikiConfig

cfg = WikiConfig.from_env(bundle=Path("./wiki"))            # OKF_* variables and the provider's key variable
cfg = WikiConfig(bundle=Path("./wiki"), mode="classic")      # or by hand
wiki = Wiki.from_env(bundle=Path("./wiki"))                  # the same as Wiki(WikiConfig.from_env(...))
```

`from_env` reads only the environment (and the keyword overrides you give it),
not the settings file of the CLI. The variables are listed in the
[README](../../README.md#for-developers) and in [providers.md](../wiki/providers.md).
The fields you will touch most:

| Field | Default | Meaning |
|---|---|---|
| `bundle` | required | the wiki folder |
| `mode` | `crow` | `crow` needs a classifier and its key; `classic` lets the LLM decide |
| `summarize` | `True` | `False` files each source verbatim |
| `max_depth` | `4` | deepest folder level the librarian may reach or create |
| `max_top_folders` | `10` | CROW: top-level folders a wiki may have (`OKF_MAX_TOP_FOLDERS`, at least 1): `plan` and `plan_groups` make no more, and a note whose route would open one is filed in the likeliest existing one |
| `max_links` | `5` | See-also links added per ingest |
| `max_notes` | `8` | notes read per question in classic mode |
| `concurrency` | `4` | documents prepared at once by `ingest_many`, `sync` and the server (1 to 16; `OKF_CONCURRENCY`) |
| `classifier.cache` | `False` | keep the classifier's answers in the bundle (`OKF_CLASSIFIER_CACHE`); see [The classifier's answer cache](#the-classifiers-answer-cache) |
| `llm.note_language` | `False` | for small local writers that ignore "write in the language of the source": the prompts that write a note end with a line naming the source's language (Italian, French, German, Spanish or Portuguese; found by function words, no model call) when it is not English (`OKF_LLM_NOTE_LANGUAGE`) |
| `llm.note_steps` | `False` | for small local writers (leave it off with a strong writer, whose single call writes more useful notes): `summarize` (and the final write of a long source) asks for the note body as plain text, then for its title, summary and tags from the body, instead of one JSON reply; falls back to the single call if the body is empty or the second reply is invalid; a body in another language than the source, or with a URL, e-mail address, path, code span or identifier the source does not have (after restoring a near-identical one in place), is asked for once more with another seed (one retry per note) and is the source text if it fails again; the body prompt lists up to 15 such strings of the source; tags of the prompt and an outer code fence are stripped, repeated sections removed; the metadata call sees the document's own heading and the source's proper names, and a title that carries an id from the file name, is cut, or misspells a name of the source is repaired or replaced by the heading or the file name; a summary in another language than the source becomes the first sentence of the body (`OKF_LLM_NOTE_STEPS`) |
| `llm.note_copy_words` | `0` | with `llm.note_steps`: a source of fewer words than this is kept as the note's body, with no body call, and only its title, summary and tags are asked for; `0` writes every body (`OKF_LLM_NOTE_COPY_WORDS`) |
| `llm.seed` | none | a seed sent with every request (`OKF_LLM_SEED`); providers that support it answer the same prompt the same way |
| `llm.pin_provider` | none | OpenRouter only (`OKF_LLM_PIN_PROVIDER`, comma-separated): the providers that may answer, in order, with no fallback to others. Another provider logs a warning and ignores it. `llm.extra_body["provider"]` wins |
| `note_length` | `proportional` | how long a note grows (`OKF_NOTE_LENGTH`): `proportional` about a quarter of its source, `fixed` about `note_chars` (0.3.0's length); see [Note length](#note-length-proportional-or-fixed) |
| `relate_rare_values` | `True` | CROW: `relate` also weighs notes that share rare values (`OKF_RELATE_RARE_VALUES`); `False` keeps 0.3.0's `relate` |
| `answer_conflicts` | `True` | CROW: the answer is asked with a hint when the notes read disagree (`OKF_ANSWER_CONFLICTS`); `False` keeps 0.3.0's answer input |
| `crow.rerank` | `True` | CROW: after `ask` finds its notes with the search (`search`'s own ranking), the classifier reorders all but the first (`OKF_RERANK`, or `crow_rerank` in the settings); `False` reads them in the search's order and asks the classifier nothing. Turn it off for a small local classifier on a wiki where the reordering does not help |
| `strict_new_folders` | `True` | CROW: a new subfolder only when the classifier is sure of it (`OKF_STRICT_NEW_FOLDERS`); `False` keeps 0.3.0's routing |
| `plan` | `writer` | CROW: how a batch on an empty wiki lays out its folders (`OKF_PLAN`): `writer` is the writer's `plan`; `groups` makes them in code with no model call, for a small local writer (about 1B parameters or less), which plans poorly. A capable writer plans better than the groups |
| `embedder` | none | the embedder of `search` and `ask`: `provider` (`openai` for any `/embeddings` server, or `ollama`), `base_url`, `model`, `api_key`, `timeout` (`OKF_EMBEDDER_*`); see [Search without a model](#search-without-a-model) |
| `llm`, `classifier`, `crow`, `embedder` | | the models and the CROW thresholds |

### Note length: proportional or fixed

Both read a long source to the end, in parts when it does not fit one call; they differ only in
how long the note they write may be. On a source shorter than about four times `note_chars`
(40000 characters by default) the two are the same note at the same cost.

- `proportional` (the default): a note of about a quarter of its source, at least `note_chars`,
  at most 45000. More of a long document reaches the note, the part past the first 100k
  characters above all. It costs more on long sources.
- `fixed`: a note of about `note_chars`, whatever the source. Cheaper on long sources, and the
  note stays short to read; much of what sits deep in a long document is lost.

Measured on 15 documents of 10 to 55 pages (up to 198k characters), CROW, three writing models
(2026-10-10; preliminary), going from `fixed` to `proportional`:

| | change, across the three models |
|---|---|
| facts of the source found in the note | +0.07 to +0.12 (on a 0 to 1 scale) |
| facts found past the first 100k characters | +0.20 to +0.86 |
| cost of filing the long documents | +6% to +43% |

The spread comes from the models, which write notes of different lengths; the price itself depends
on the model and the provider you choose. The cost of a short document does not change with `note_length`, and the cost per document
grows little with the wiki: the same documents filed into a wiki of 150 notes instead of 23
cost about 1.2 times as much each, with the same number of model calls.

## Wiki

```python
wiki = Wiki(cfg)                                  # builds the LLM (and in CROW mode the classifier) from cfg
wiki = Wiki(cfg, llm=my_llm, classifier=my_clf)   # or take them ready; both must share one UsageTracker
wiki = Wiki(cfg, steps=my_steps)                  # or replace steps: see steps.md
wiki = Wiki(cfg, author="ada")                    # who the changes are made for, in log.md (below)
```

Without `steps`, `cfg.mode` picks the classic or the CROW set. No key is read
until a model is called, so a wiki whose steps call no model works with none
(see `examples/labeled_dataset.py`).

`Wiki` is a context manager: leaving the block closes the models' connections
(or call `wiki.close()`).

**Who changed what.** Every method that changes the wiki (`ingest`, `ingest_file`, `ingest_many`, `rebuild`,
`mark_removed`, `purge`, `create_folder`, `create_area`, `describe_folder`, `move_note`, `propose_area`, `confirm_proposal`,
`dismiss_proposal`, `rename_root`, `merge_roots`, `delete_note`, `delete_folder`, `clear`, `audit_routes(apply=True)`,
and `sources.sync`) takes an optional `author=`, and `Wiki(cfg, author=...)` sets the default. Each line it writes in
`log.md` then ends with ` · by <author>` (one line of at most 60 characters, without `*`, `[`, `]`, `(` or `)`); the
call's author wins over the wiki's, and it holds for that call only, so threads that share one `Wiki` (the HTTP server's)
never mix theirs. Without an author the log is written as before. The author is **declared, not verified**: there is
no authentication, no signature and no field in the notes, `log.md` is the record. The CLI declares the login name, the
HTTP API the `X-Author` header and the MCP memory the client's name; behind a reverse proxy that authenticates your users,
have it set `X-Author`, and the log then says who, as far as the proxy does.

| Method | What it does |
|---|---|
| `init()` | create the bundle (`index.md`, `log.md`, `raw/`) if missing; ingest does it too |
| `ingest(text, *, title, resource, origin, fields)` | file a text; returns an `IngestResult` |
| `ingest_file(data, filename, *, resource, origin, fields)` | the same for the bytes of a text, Markdown, PDF, Word, PowerPoint, Excel or OpenDocument file (the `extract` step reads it); the file name gives the title. A spreadsheet is filed one note per sheet: the first sheet's result, the others in its `parts` |
| `ingest_many(items, *, concurrency, on_result)` | file a batch, preparing several documents at once; returns a `BatchReport` (see below) |
| `rebuild(items, *, concurrency, on_result)` | file the documents again on the folders the wiki has (see below); returns a `BatchReport`. Cannot be undone |
| `raw_items()` | the wiki's own raw copies as `Item`s for `rebuild`: an origin from its latest copy, a copy with no origin once per text |
| `ask(question, where=None)` | answer from the notes, found by `search` (the first 16, reordered by the CROW classifier unless `crow.rerank` is off) and read up to `crow.k`; returns an `Answer`. `where` (a dict, field to value) restricts it to the notes whose `fields` hold all of them, compared as text (use scalar values) |
| `search(query, k=3, where=None)` | the best `k` notes, with no model call of the library; returns a list of `Hit` (`path`, `title`, `summary`, `rank`). `where` restricts it as in `ask`; an empty query raises `InputError`; see [Search without a model](#search-without-a-model) |
| `tree()` | the folder tree, read from disk: `Folder` objects with `.notes` and `.subfolders` |
| `create_folder(parent, name, description)` | make a folder by hand; `parent` is `"/"` or `"/finance"` |
| `move_note(path, folder)` · `describe_folder(path, description)` | move a note to another folder (its links, and links to it, are rewritten; archived copies follow) · change a folder's one-line description |
| `delete_note(path)` · `delete_folder(path)` · `clear()` | delete; links to what goes are removed, and so are raw copies no remaining note cites and the archived versions of the notes deleted; `clear()` also removes the sync cursors, so the next `sync` files every document again |
| `check()` | lint the bundle; a list of problems (OKF conformance, index drift, broken links) |
| `usage` | token totals so far (a property): `wiki.usage.to_dict()` |
| `origin(key)` · `mark_removed(key)` · `purge(key, *, dry_run)` | see below |

`fields` is a dict of your own data. The core does not interpret it; it reaches the
steps as `ctx.source.fields` and is written to the note as `fields:` in its frontmatter (laid over the note's own when merged)
([steps.md](steps.md#fields)). You choose the keys and the values (`{"team": "ops"}`, `{"project": "x"}`).

A document is never merged into a note when their `fields` have a key in common with different values: it is filed as a
new note, or merged into another note that agrees. The rule holds for any key and needs no setting. A note without `fields`
never conflicts, and neither do `fields` with different keys. `where` in `ask` is a filter on what is read, not an access
control: the page, the See-also links and `index.md` show every note.

An `ingest` with an `origin` whose content (or `version`) was already filed
returns `action == "unchanged"` and calls no model.

```python
result = wiki.ingest("Acme refunds orders within 30 days of delivery.", title="Refund policy")
print(result.action, result.note, result.folder, result.created_folders, result.related)

answer = wiki.ask("How long do customers have to ask for a refund?")
print(answer.text, answer.citations)
```

### Results

`IngestResult`: `action` (`"created"`, `"merged"` or `"unchanged"`), `note`
(bundle path), `title`, `folder`, `created_folders`, `related` (paths of the
See-also links), `decisions` (the trace of who decided what, and with what
confidence) and `usage` (the tokens spent by this ingest).

`Answer`: `text`, `citations` (the notes cited in the text, as `[path.md]`),
`notes` (every note given to the answering model), `decisions` and `usage`.
Both have `to_dict()`.

### Search without a model

`wiki.search("refund deadline", k=3)` returns the best notes for a query, with no call to a language model and nothing
run at ingest. Each note is read whole (title, summary and body, without its See also section) and ranked by BM25;
notes with none of the query's words are left out. The words of a note are counted once and kept while its text is
the same, so a search at thousands of notes reads only the lists of the query's words. A query that quotes something
(text in double quotes, «», “” or backticks) or holds a code-like token (`AB-1001`, `getUser`, `v2.1`) brings first the
notes that hold it, unless it is in more than 3 of the best 16.

Give `Wiki` an `embedder` and the search also goes by meaning, and so does `ask` in CROW mode, which finds its notes with
this search. Set it in the configuration (`OKF_EMBEDDER_PROVIDER=ollama`, `OKF_EMBEDDER_MODEL=...`, plus
`OKF_EMBEDDER_BASE_URL` and `OKF_EMBEDDER_API_KEY` if needed; `openai` is any server with `/embeddings`) and the
command line, the server and the memory MCP use it; or pass a callable, which wins. A callable is any callable from a list of texts to a list of
vectors (the library has no model, provider or dependency for it), and it is called with the query and with the notes
that have no vector yet, 64 texts at a time. A note is embedded as its title, its summary and the first 3000
characters of its body. The result is built from slots: the `k - 1` nearest notes by cosine, then the best by BM25
if it is not among them, then the next nearest to fill up to `k`; no note twice, no threshold, no weights.

```python
wiki = Wiki(cfg, embedder=lambda texts: my_model.encode(texts))
for hit in wiki.search("how long do customers have to ask for a refund?", k=3, where={"team": "ops"}):
    print(hit.rank, hit.path, hit.title, hit.summary)
```

The vectors are kept in `.llmw2/cache/embeddings.sqlite` of the bundle, under the hash of the note's text, so a search
embeds only the notes that are new or changed; the vectors of text that no longer exists are dropped. A vector of
another size than the query's is embedded again. The file does not say which embedder made its vectors: if you change
the embedder for one with the same size, delete the file. `wiki.clear()` deletes it.

### Asking several wikis

Wikis are separate and there is no search across them. To ask several, ask each and compose the answers yourself:

```python
from pathlib import Path
from llmw2 import Wiki, WikiConfig

bundles = {"north": Path("wiki-north"), "south": Path("wiki-south")}
question = "What is the refund policy?"

answers = {}
for name, bundle in bundles.items():
    with Wiki(WikiConfig(bundle=bundle)) as wiki:
        answers[name] = wiki.ask(question)

# Show them side by side, each with its own citations
for name, a in answers.items():
    print(f"## {name}\n{a.text}\ncited: {a.citations}\n")

# Or compose one answer with your own LLM, keeping the wiki of each citation
context = "\n\n".join(f"[{name}] {a.text}" for name, a in answers.items())
```

Citations are note paths inside one bundle, so keep the wiki's name next to them. Limits: no common ranking
between wikis, no permission model between them (whoever can open a bundle can ask it), and `where` filters on
`fields` inside one wiki and is not an access control. To give an MCP client several wikis, see
[More than one wiki](../wiki/memory.md#more-than-one-wiki).

## Your own top-level areas

By default the librarian lays out the top-level folders itself. To give your own, make them with `create_folder`
before the first sync. The description is required: the classifier routes by it.

```python
for name, description in [("finance", "Budgets, invoices and forecasts."), ("people", "Hiring, contracts and reviews.")]:
    if wiki.tree().find(f"/{name}") is None:      # idempotent: run it at every start
        wiki.create_folder("/", name, description)
```

Folders made by hand count in `max_top_folders`. Set it to the number of your areas and the wiki is closed: no top-level
folder is opened, the plan is told the areas are fixed, and a note whose route would open one goes to the area the
classifier finds likeliest. A higher value leaves it open to as many more. `delete_folder` deletes the notes in the
folder too. For different permissions per area, keep one wiki per domain.

### Managing the areas

The wiki never opens or proposes an area by itself: proposals come from you, and `deviations` (the notes the limit sent to
the likeliest area) helps you compose them.

```python
proposal = wiki.propose_area("suppliers", "Who we buy from: contracts and prices.", ["finance/acme-terms.md", note_id])
wiki.proposals()                      # the open ones; proposals("confirmed") and proposals(None) for the rest
wiki.confirm_proposal(proposal.id)    # (proposal, paths of the notes moved); or wiki.dismiss_proposal(proposal.id)
wiki.rename_root("/finance", "money")
wiki.merge_roots("/legal", "/contracts")
```

- `propose_area(label, description, notes)` validates and queues a `Proposal` (`id`, `label`, `description`, `notes`: stable
  note ids, `origin`: `"user"`, `created`, `status`). Notes are given by id or by path. The queue is the file
  `.llmw2/proposals.json` of the wiki: it survives between syncs and reopening, and `clear()` empties it. Nothing moves.
- `confirm_proposal(id, over_limit=False)` creates the root, counted in `max_top_folders`: `InputError` when the wiki
  already has that many, unless `over_limit`. It moves only the proposed notes still in the wiki, writing `moved_from` (the
  folder they came from) in their frontmatter and dropping their `overflow` mark. No rebuild, no other note touched,
  one "Confirmed area" line in `log.md`. `propose_area` writes a "Proposed area" line and `dismiss_proposal(id)` closes a proposal with a "Dismissed proposal" line, changing nothing else.
- `create_area(name, description, over_limit=False)` is `create_folder("/", ...)` refused over the limit.
- `rename_root(path, new_name)` renames a top-level folder with its notes; `merge_roots(source, target)` moves every note
  of one root into another (subfolders keep their structure, one both have is merged), each note with `moved_from`, and
  deletes the source. Links to and from the notes follow, descriptions stay, one `log.md` line each.

The same operations are in the CLI (`llmwiki2 proposals`, `proposals add LABEL --description … NOTE…`, `proposals confirm ID
[--over-limit]`, `proposals dismiss ID`, `rename-root`, `merge-roots`), the HTTP API (`GET/POST /proposals`,
`POST /proposals/{id}/confirm` and `/dismiss`, `POST /areas`, `POST /folder/rename`, `POST /folder/merge`), the page's **Areas** section
(no proposing from the page) and the memory's tools.

## Upgrading: removed retrieval settings

`crow.tau_fold`, `crow.tau_ret` and `crow.retrieval_beam` (`OKF_TAU_FOLD`, `OKF_TAU_RET`, `OKF_RETRIEVAL_BEAM`, and
`crow_tau_fold` and the like in a settings file) no longer exist: `ask` finds its notes with the search and `crow.rerank`
and `crow.k` tune the rest. They are ignored with one `DeprecationWarning`; delete them from your environment and settings.

## Filing many documents

```python
from llmw2 import Item

report = wiki.ingest_many(
    [Item("minutes.pdf", data=pdf_bytes), Item("notes.md", text="…"), Item("big.pdf", fetch=lambda: download("big.pdf"))],
    concurrency=4,                                            # default: cfg.concurrency
    on_result=lambda item, result: print(item.filename, result),
)
for item, result in report.results:
    print(item.filename, result if isinstance(result, Exception) else result.action)
print(report.not_done, report.stopped)
```

An `Item` is a `filename` (it decides the file type and gives the title), the content in **exactly one** of
`text`, `data` (bytes) or `fetch` (a function returning the bytes, called on a worker thread), and optionally
`origin`, `resource` and `fields` as in `ingest_file`. Anything else raises `InputError` when the item is made.

The batch is put in order: oldest `origin.modified` first (items without one last), then `filename`. Up to
`concurrency` documents are prepared at once: fetch, extract, the check for "unchanged" and `summarize`. They
are filed one by one, in that order, each on the tree as the previous one left it, so with `concurrency=1` the
result is the same as calling `ingest_file` for each in turn. Which steps run in parallel is in
[steps.md](steps.md#what-runs-where).

`BatchReport`:

- `results`: `(item, result)` in the order they were filed. `result` is an `IngestResult`, an `InputError` for
  a document that cannot be read, or a `ReplyInvalid` for one whose model replies stayed unusable after the retry
  (the batch goes on; two `ReplyInvalid` in a row stop it, as the model's fault).
- `not_done`: the items that were not filed because the batch stopped.
- `stopped`: why it stopped, `None` if it did not.
- `deviations`: the notes the limit on top-level folders sent to the likeliest existing one instead of a new one, each
  `{"note", "root", "p_none", "p_root"}` (the probabilities are `None` when the folder's words chose, not the
  classifier). `to_dict()` carries them as `deviated` (the count) and `deviations`, only when there are any. The same note has `overflow:
  {p_none, p_root, at}` in its frontmatter, `IngestResult.overflow` has the probabilities, and `log.md` has a "Deviated
  note" line for it: no note is filed in another folder than the one routed to without one of these.

A failure that is not the document's (a model that keeps failing, a `StepError`) stops the batch: nothing new is
started, the documents already being prepared finish, those before the failed one are filed, and the rest are in
`not_done`. A failure while filing stops it at once, and the item that failed is in `not_done` too. Running the
same batch again files what is left (an item with an `origin` that was filed is `"unchanged"`).
`on_result(item, result)` is called after each item is filed or fails.

## Keeping a wiki in step with a source

`sync` files the changes a connector reports and remembers where it stopped, so
the next run only sees what changed:

```python
from pathlib import Path
from llmw2 import LocalFolderSource, sync

report = sync(wiki, LocalFolderSource(Path("~/OneDrive")), concurrency=4)   # default: cfg.concurrency
print(report.to_dict())   # created, merged, unchanged, removed, skipped, errors, stopped
```

`LocalFolderSource` reads every file of a type the wiki reads under a folder (Office's `~$` lock files excepted). A
connector for anything else is any object with:

- `name` and `account` (strings: they identify the source in origin keys and in the cursor file);
- `changes(cursor: str | None) -> ChangeBatch`, what changed since the cursor (`None`: everything);
- `fetch(change: Change) -> bytes`, the content of an item.

A `Change` is `kind` (`"upsert"` or `"remove"`), `origin`, `filename` and, if you give them, `fields` (the document's, passed on
by `sync`). `LocalFolderSource(folder, fields=lambda rel: {"team": rel.parts[0]})` sets them from each file's path relative
to the folder. A
`ChangeBatch` is `changes`, `cursor` (opaque: you get it back next time) and
`more` (`True` to be asked again at once). The cursor is kept in
`.llmw2/sync/` inside the wiki. See `examples/sync_connector.py`.

A connector with thousands of items should page: return a page of changes with `more=True` and, as its `cursor`, the
state after that page. `sync` saves the cursor after each page it files, so a run that stops resumes at the first page
not finished and does at most one page again. `LocalFolderSource` does this with pages of 200 changes: its cursor is the
listing with the pages handed over applied, and asked from it, it gives the next page. A cursor saved by an earlier
version still reads.

**From a service's delta API.** Most services (Microsoft Graph, Google Drive, Dropbox) offer a delta or changes call
that returns what changed since a token, in pages. That maps straight onto `changes`:
- the `cursor` is the service's own delta token, as a string: `changes(None)` starts the listing, `changes(token)` asks
  for what changed since it;
- each page the service returns is one `ChangeBatch`, whose `cursor` is the token to continue from and whose `more` is
  `True` while the service says there are more pages;
- an item that is new or edited is `Change("upsert", Origin(name, account, item_id, version=...), filename)`, and an
  item the service reports as deleted is `Change("remove", Origin(name, account, item_id), filename)`;
- `fetch(change)` downloads the bytes of one item, and `fields` carries what you want filterable on the document.

`examples/sync_connector.py` is this in full over an in-memory drive (it keeps its own state as the cursor instead of a
token); it needs no key.

**Folders synced by a desktop client.** OneDrive, SharePoint (through OneDrive), Google Drive and Dropbox keep a copy
of their files in an ordinary folder on your computer, and `LocalFolderSource` over that folder already works. That is
the source, though, and not the wiki: do not put the wiki's own bundle in such a folder while a batch writes (the
client can change or round the files' times the batch follows). The source can be there; the bundle should not.

**Why there are no ready connectors for those services.** Each needs an OAuth app, an account or tenant to test with, and
upkeep as the service changes, so they are left to whoever runs them; the protocol above is what to implement.

An `Origin(source, account, id, link=None, version=None, modified=None)` says
where an original lives. Its `key` (`"source:account:id"`) ties together the
raw copies, the notes and the original. `wiki.origin(key)` gives an
`OriginRecord` (the latest raw copy a note cites, its hash and version, and the
notes), or `None`. `wiki.mark_removed(key)` records that the original is gone
at its source: the raw copy is marked and its notes stay. `sync(..., purge_removed=True)`
(`llmwiki2 sync --purge-removed`) purges those documents instead (see Purge).

Each page of changes is filed as one `ingest_many` batch: the last change of
each origin counts, removals go first, and an item whose `version` is already
filed is not fetched. An item that cannot be read (a type that is not
supported, a scanned PDF) goes into `report.errors` and the sync goes on. A
document whose model replies stayed unusable goes into `report.errors` too and
the rest of its page is filed; then the sync stops at the end of that page
(`report.stopped` says so) without saving its cursor, so the next run retries
that document alone. Any other model failure stops it: `sync` returns the report so far with `report.stopped`
set (`llmwiki2 sync` prints it and exits with 2), the cursor stays at the last
finished page, and the next run resumes there without paying again for what was
filed. A missing key raises `ConfigError`. The same stop is how `max_cost` ends a sync.

`SyncReport` has the same `deviations` (and `deviated` in `to_dict()`, when there are any).

### Capping and estimating a sync

`sync(wiki, connector, max_cost=5.0)` (`llmwiki2 sync FOLDER --max-cost 5`) caps what one sync spends, in dollars and in
the cost the provider reports (OpenRouter does; Ollama reports none, so a cap never stops a local model). Before every
request to a model, the writer's and the classifier's, retries included, the tracker compares the spending of the sync
with the cap, and once it is reached the request is not sent: `SpendingCap` (a `WikiError`, not a `ModelError`, so no
fallback of a step takes it for a failing model) stops the sync. The report comes back with `stopped` saying "spending
cap", the cursor stays at the last page finished, and a later run (with or without a cap) resumes there without paying
again for what was filed. An answer served from the classifier's cache costs nothing and is never blocked. Without
`max_cost` nothing is checked. The same works for any block of calls: `with wiki.tracker.limit(5.0): ...`. A cap counts and stops only the calls
made inside its block (in its thread and the lanes a batch starts), so other work on the same wiki, such as the
server's answers, is neither counted nor stopped. `wiki.tracker.spent()` is the cost so far.

A document is never filed half way: the cap is not checked while one is being filed (its route, match, links, split), and
it is checked before the next one starts. A document still being prepared (summarized) or a window being planned when the
cap is reached is not filed, and the next run does it again. So the cap is passed by at most the calls in flight in the
`concurrency` lanes plus the calls of the filing of one document (a document being filed is finished, its split included).
A sync takes back, when it ends, the folders its plans made that are still empty (a folder with a note, or one that was there
before, stays): after a stop the next run plans on the tree it would have found had this one never run; `log.md` has a "Removed
folder" line for each. A page of changes is read and planned whole before its first document is filed, so a cap under that
cost files nothing, and each run would pay it again: `stopped` then says "nothing was filed" and what reading and planning
the page cost so far. In the measures, planning a page of 150 documents cost about as much as filing 20 to 25 documents, an
order of magnitude and not a price: give the first sync a cap of at least that many documents' worth, plus what filing the
page costs; after it, `estimate` gives the cost per document.

Every sync, finished or stopped, adds a line to `.llmw2/sync/history.jsonl` of the wiki (one file for the wiki, not for a
connector): the documents it filed (created and merged) and what it spent. `estimate(wiki, connector)`
(`llmwiki2 sync FOLDER --estimate`) lists the connector's changes from the saved cursor without fetching or filing
anything, and returns `{"documents": n, "est_cost": ..., "basis": ...}`: `n` is the documents a sync would file (the ones
already filed at their version do not count), and `est_cost` is `n` times the mean cost per document of the history, or
`None` with `basis` "no history" before the first sync: cap the first one with `max_cost`. The estimate tends to be too low,
more so as the wiki grows (the first sync costs less per document than the ones after it): in the measures the ratio of
the estimate to the real cost went from about 0.7 to 1.3, up to about a third under. Set `--max-cost` to about 1.5 times
the estimate, not to the estimate. Each sync's cost in the history is that of the sync's own calls, not of other work on
the same tracker.

### The classifier's answer cache

With `classifier.cache` on (`OKF_CLASSIFIER_CACHE=true`; off by default), the classifier keeps every answer in
`.llmw2/cache/classifier.sqlite` of the bundle, under the sha256 of the request: the classifier's address and model,
the state and the questions. Only hashes and answers are stored, never the text of a document or an error, and a
request that was answered before costs no call. The report counts those answers as `served` in the classifier's usage
(`wiki.tracker.snapshot()`, and `by_op`), not as calls, with no tokens and no cost. The file is SQLite in WAL mode:
threads and processes of one machine share it. `wiki.clear()` deletes it; `rebuild` keeps it, since a rebuild is what
reuses it.

The cache pays when the same questions come back: a rebuild on the same folders, a sync resumed after a stop (the
documents already prepared), and an audit repeated on the same notes. It freezes the classifier's answers, so a second
rebuild takes the decisions of the first, without the service's own variation. It does not pay on a sync of new
documents: each one is a state never asked before, so every request goes out. Nothing expires; delete the file to
start over.

The backend is any object with two methods, so a store shared by several machines can stand in for the file. Give the
classifier your own when you build the wiki:

```python
import json

import redis
from llmw2 import Wiki, WikiConfig
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker

class RedisCache:                       # get(key) -> answers or None; put(key, answers)
    def __init__(self, url: str) -> None:
        self.db = redis.Redis.from_url(url)

    def get(self, key: str):
        value = self.db.get(f"clf:{key}")
        return json.loads(value) if value else None

    def put(self, key: str, answers) -> None:
        self.db.set(f"clf:{key}", json.dumps(answers))

cfg = WikiConfig(bundle="wiki")
tracker = UsageTracker()
wiki = Wiki(cfg, classifier=Classifier(cfg.classifier, tracker, cache=RedisCache("redis://localhost")),
            llm=LLM(cfg.llm, tracker))
```

The library ships no Redis code and no dependency on it.

### Rebuild

`wiki.rebuild(items)` files documents again on the folders the wiki has. Every note, raw copy, archived version and
sync cursor is deleted; the folders stay with their descriptions. No `plan` is made, and the folders are fixed: no
folder is created or split, a score under `tau_path` files the note in the best path's folder without asking the model
where, and a note that would open a top-level folder goes to the likeliest existing one (listed in `deviations`). It
cannot be undone; a rebuild that stops (`report.stopped`) leaves the notes filed so far, and running it again starts
over. Give it the documents themselves, or `wiki.raw_items()` (read before anything is deleted) to use the wiki's own
raw copies. The documents keep their origins, so a `sync` of the same folder afterwards files nothing again.
`llmwiki2 rebuild [folder] --yes` does it from the terminal.

### Purge

`wiki.purge(key)` takes a document out of the wiki for good, with everything written from it, and returns a `Purged`
report (`None` if no raw copy has the key). It deletes the document's raw copies (a spreadsheet's sheets, `<key>#<sheet>`,
with it), their archived versions and every note that cites one of them. A note fused from several documents is deleted
whole, and the other documents it held are filed again from their own raw copies, the way `rebuild` files them (no plan, on
the folders there are, none made or split). The links to the deleted notes go from the other notes (as in `delete_note`),
index.md is written again, and the lines of log.md that name a deleted note or raw copy keep their verb and author and say
`[purged]` in place of the title, path and link. One `Purged` line is added, with the author and without the document's
title (a short hash of the key stands for it). `wiki.check()` is empty afterwards. `dry_run=True` only reports what would go.

The guarantee that no phrase of the document survives, paraphrased ones included, lies in how it is done, not in a check
on the text: nothing is rewritten "to take the document out". Everything written from it is deleted, and what it shared a
note with is written again from its own raw copy. If filing the others again stops (a model failing), the report has
`stopped` and `errors`; their raw copies stay, cited by no note, and a `rebuild` files them. The classifier cache
(`classifier.cache`) holds hashes and labels, never text, and is not touched. There is no MCP tool for it, as it cannot
be undone. `llmwiki2 purge KEY --yes` does it from the terminal (without `--yes` it says what it would delete and exits
with 2); the key is the one the raw copies carry in their `origin`.

Keeping documents for a time only is left to the caller, in a few lines:

```python
from datetime import UTC, datetime, timedelta

limit = (datetime.now(UTC) - timedelta(days=365)).isoformat()
for key in wiki.store.origin_keys(""):  # every origin key
    record = wiki.origin(key)
    if record and (modified := wiki.store.read_raw(record.raw)[0]["origin"].get("modified")) and modified < limit:
        wiki.purge(key, author="retention")
```

## Errors

Everything llmw2 raises on purpose is a `WikiError`:

| Error | Also a | When |
|---|---|---|
| `InputError` | `ValueError` | an empty source or question, an unreadable file type, invalid settings |
| `ConfigError` | `ValueError` | a broken settings file or environment variable |
| `ModelError` | `RuntimeError` | a model endpoint failed or answered something unusable (`.status` is the HTTP status, if any); a missing key is one |
| `ReplyInvalid` | `ModelError` | the model answered, but nothing usable for one request even after a retry (invalid JSON, no content, a reply cut at the limit): the document's failure, not the endpoint's |
| `StepError` | | a step returned something the flow cannot take (`.step` names it); see [steps.md](steps.md#what-the-core-checks) |

`create_folder` also raises `FileNotFoundError` for a parent that does not
exist.
