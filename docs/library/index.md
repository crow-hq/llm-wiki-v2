# Using llmw2 as a library

Everything the web page and the command line do is a method of one object,
`Wiki`. This guide covers the public API; how to replace parts of the ingest
and ask flows is in [steps.md](steps.md), and runnable programs are in
[examples/](../../examples/).

## Install

```bash
pip install "llmw2 @ git+https://github.com/crow-hq/llm-wiki-v2"
```

Add the `[server]` extra only for the HTTP API and the web page.

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
| `max_links` | `5` | See-also links added per ingest |
| `max_notes` | `8` | notes read per question in classic mode |
| `concurrency` | `1` | documents prepared at once by `ingest_many`, `sync` and the server (1 to 16; `OKF_CONCURRENCY`) |
| `llm`, `classifier`, `crow` | | the models and the CROW thresholds |

## Wiki

```python
wiki = Wiki(cfg)                                  # builds the LLM (and in CROW mode the classifier) from cfg
wiki = Wiki(cfg, llm=my_llm, classifier=my_clf)   # or take them ready; both must share one UsageTracker
wiki = Wiki(cfg, steps=my_steps)                  # or replace steps: see steps.md
```

Without `steps`, `cfg.mode` picks the classic or the CROW set. No key is read
until a model is called, so a wiki whose steps call no model works with none
(see `examples/labeled_dataset.py`).

`Wiki` is a context manager: leaving the block closes the models' connections
(or call `wiki.close()`).

| Method | What it does |
|---|---|
| `init()` | create the bundle (`index.md`, `log.md`, `raw/`) if missing; ingest does it too |
| `ingest(text, *, title, resource, origin, fields)` | file a text; returns an `IngestResult` |
| `ingest_file(data, filename, *, resource, origin, fields)` | the same for the bytes of a `.txt`, `.md` or `.pdf` (the `extract` step reads it); the file name gives the title |
| `ingest_many(items, *, concurrency, on_result)` | file a batch, preparing several documents at once; returns a `BatchReport` (see below) |
| `ask(question)` | answer from the notes; returns an `Answer` |
| `tree()` | the folder tree, read from disk: `Folder` objects with `.notes` and `.subfolders` |
| `create_folder(parent, name, description)` | make a folder by hand; `parent` is `"/"` or `"/finance"` |
| `delete_note(path)` · `delete_folder(path)` · `clear()` | delete; links to what goes are removed, and so are raw copies no remaining note cites |
| `check()` | lint the bundle; a list of problems (OKF conformance, index drift, broken links) |
| `usage` | token totals so far (a property): `wiki.usage.to_dict()` |
| `origin(key)` · `mark_removed(key)` | see below |

`fields` is a dict of your own data. The core does not read it; it reaches the
steps as `ctx.source.fields` and the notes as `fields:` in their frontmatter
([steps.md](steps.md#fields)).

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

## Filing many documents

> **Provisional.** `ingest_many`, `Item` and `BatchReport` may change until the benchmark of parallel against
> serial filing is in; `concurrency` stays at 1 until then.

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

- `results`: `(item, result)` in the order they were filed. `result` is an `IngestResult`, or an `InputError` for
  a document that cannot be read (the batch goes on).
- `not_done`: the items that were not filed because the batch stopped.
- `stopped`: why it stopped, `None` if it did not.

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

`LocalFolderSource` reads every `.txt`, `.md` and `.pdf` under a folder. A
connector for anything else is any object with:

- `name` and `account` (strings: they identify the source in origin keys and in the cursor file);
- `changes(cursor: str | None) -> ChangeBatch`, what changed since the cursor (`None`: everything);
- `fetch(change: Change) -> bytes`, the content of an item.

A `Change` is `kind` (`"upsert"` or `"remove"`), `origin` and `filename`. A
`ChangeBatch` is `changes`, `cursor` (opaque: you get it back next time) and
`more` (`True` to be asked again at once). The cursor is kept in
`.llmw2/sync/` inside the wiki. See `examples/sync_connector.py`.

An `Origin(source, account, id, link=None, version=None, modified=None)` says
where an original lives. Its `key` (`"source:account:id"`) ties together the
raw copies, the notes and the original. `wiki.origin(key)` gives an
`OriginRecord` (the latest raw copy a note cites, its hash and version, and the
notes), or `None`. `wiki.mark_removed(key)` records that the original is gone
at its source: the raw copy is marked and its notes stay.

Each page of changes is filed as one `ingest_many` batch: the last change of
each origin counts, removals go first, and an item whose `version` is already
filed is not fetched. An item that cannot be read (a type that is not
supported, a scanned PDF) goes into `report.errors` and the sync goes on. A
model failure stops it: `sync` returns the report so far with `report.stopped`
set (`llmwiki2 sync` prints it and exits with 2), the cursor stays at the last
finished page, and the next run resumes there without paying again for what was
filed. A missing key raises `ConfigError`.

## Errors

Everything llmw2 raises on purpose is a `WikiError`:

| Error | Also a | When |
|---|---|---|
| `InputError` | `ValueError` | an empty source or question, an unreadable file type, invalid settings |
| `ConfigError` | `ValueError` | a broken settings file or environment variable |
| `ModelError` | `RuntimeError` | a model endpoint failed or answered something unusable (`.status` is the HTTP status, if any); a missing key is one |
| `StepError` | | a step returned something the flow cannot take (`.step` names it); see [steps.md](steps.md#what-the-core-checks) |

`create_folder` also raises `FileNotFoundError` for a parent that does not
exist.
