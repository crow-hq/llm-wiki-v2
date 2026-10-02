# Customizing the steps

The flow of an ingest and of a question is fixed. The decisions and the
writing inside it are steps: ten functions, each with a set signature, that you
can replace one by one. Classic mode and CROW mode are two ready-made sets of
them (`CLASSIC`, `CROW`).

The core owns the order of the steps, the lock on the wiki and every write to
disk. Inside a step anything goes: no model, one call, many calls, a rule, a
web service. The core checks what a step returns and stops with `StepError`
before using it.

The API of `Steps`, the ten signatures and the data classes below is stable.

## The two flows

```
ingest:  extract* → summarize → route → (name_folder) → match → consolidate → merge | write → relate → link, index, log
ask:     select → answer
```

`*` `extract` only runs for `ingest_file` (and for the `data` and `fetch` items of `ingest_many`), which turn the bytes of a file into text.

### What runs where

`ingest_many` (and the server, and `sync`) prepare several documents at once, each on a thread of its own:

- `extract` and `summarize` run in parallel, on different threads, for different documents. They must be thread-safe: no shared state without a lock of yours. They do not receive the tree, so they do not need the wiki's lock.
- The others (`route`, `name_folder`, `match`, `consolidate`, `merge`, `relate`) run one document at a time, under the wiki's lock, on a tree read just before. They never overlap, and can rely on that. `select` and `answer` belong to a question and take no lock.

Each document gets its own `StepContext`, so `ctx.decisions` and `ctx.source` are never shared. With `concurrency` 1 everything runs one document after the other, as in a plain loop of `ingest_file`.

```
                        text ──► summarize ──► route ──► match ──► consolidate ──┐
                                   Candidate    Route     Note|None      bool     │
                                                  │                              │
                       Route.new, no create:      │            yes: merge ──► update the note
                       name_folder ──► new folder ┘            no:  write a new note
                                                                       │
                                  relate ──► See-also links, index, log ◄┘

   question ──► select ──► answer
                list[Note]   str, citing [path.md]
```

- `name_folder` runs only when the route says `new`, gives no `create`, and its folder can still have subfolders (depth below `max_depth`).
- `consolidate` and `merge` run only when `match` found a note.
- `relate` runs only when there is another note to relate to.
- `answer` is skipped when `select` returns no notes: the answer is then a fixed "no relevant notes" text.

## The ten steps

Every step takes a `StepContext` first. The output is checked as shown; a bad
output raises `StepError` naming the step.

| Step | Signature | Checked |
|---|---|---|
| `extract` | `(ctx, data: bytes, filename: str) -> str` | a `str` |
| `summarize` | `(ctx, source: Source) -> Candidate` | a `Candidate`; non-empty title; `tags` a list of `str`; `fields` a dict that YAML can write |
| `route` | `(ctx, root: Folder, cand: Candidate) -> Route` | a `Route` whose `folder` and `candidates` are folders of this tree; `create` valid (see [Route.create](#routecreate)) |
| `name_folder` | `(ctx, parent: Folder, cand: Candidate) -> tuple[str, str]` | `(name, description)`, two `str`, the name not empty |
| `match` | `(ctx, pool: list[Note], cand: Candidate) -> Note \| None` | `None` or one of the notes of `pool`, the object itself |
| `consolidate` | `(ctx, note: Note, cand: Candidate) -> bool` | a `bool` |
| `merge` | `(ctx, note: Note, source: Source) -> NoteDraft` | a `NoteDraft`, checked like a `Candidate` |
| `relate` | `(ctx, note: Note, others: list[Note]) -> list[Note]` | notes of `others`, not `note` itself |
| `select` | `(ctx, root: Folder, question: str) -> list[Note]` | notes of the wiki |
| `answer` | `(ctx, question: str, notes: list[Note]) -> str` | a `str` |

What each one is for:

- `extract`: the text of a file. The default reads `.txt`, `.md` and `.pdf`.
- `summarize`: the note an ingest would write. The default asks the LLM (or, with `summarize=False` in the config, keeps the source as it is).
- `route`: where the note goes, and which folders to look in for a note to merge with.
- `name_folder`: the name and one-line description of a new folder.
- `match`: an existing note on the same subject, if any.
- `consolidate`: `True` to merge into the matched note, `False` to write a new one beside it.
- `merge`: the rewritten note, given the matched note and the incoming source.
- `relate`: the notes the new one should link to. The core keeps at most `max_links` of them.
- `select`: the notes to read for a question.
- `answer`: the answer text. To be listed in `Answer.citations`, cite a selected note as `[path.md]`.

Notes and folders are objects of the tree the core loaded for this operation;
return those, not copies. `Note` has `rel` (its bundle path), `title`,
`summary`, `tags`, `body`, `frontmatter` and `folder`. `Folder` has `name`,
`label` (`"/finance/pricing"`), `description`, `notes`, `subfolders`, `depth`
and `walk()`, `find(path)`, `subfolder(name)`, `all_notes()`, `ancestors()`.

### Data

```python
@dataclass
class Source:      # what is being ingested
    text: str; title: str | None; resource: str | None; origin: Origin | None; fields: dict[str, Any]

@dataclass
class Candidate:   # the note an ingest would write
    title: str; summary: str; tags: list[str]; body: str; fields: dict[str, Any]

@dataclass
class Route:       # where it goes
    folder: Folder; new: bool; candidates: list[Folder]; create: list[tuple[str, str]]

class NoteDraft(BaseModel):   # a merged note
    title: str; summary: str; tags: list[str]; body: str; fields: dict[str, Any]
```

### StepContext

The first argument of every step. It holds `llm`, `classifier` (`None` in classic
mode), `prompts`, `cfg` (the `WikiConfig`), `source` (the source being ingested; `None` while asking and
inside `extract`) and `decisions` (the trace that ends up in `IngestResult.decisions`
and `Answer.decisions`). It has no access to the store: a step reads the tree it
is given.

| Member | Use |
|---|---|
| `ask_json(prompt, schema, **values)` | render a packaged prompt (`"librarian/route"`) and get the LLM's JSON as a pydantic `schema` |
| `ask_text(prompt, **values)` | the same, for a plain-text reply |
| `llm.complete(system, user, op=...)` · `llm.json(...)` | a model call with your own text |
| `decide(step, decider, choice, confidence=None, ...)` | add an entry to the trace |
| `can_create(folder)` · `children(folder)` | respect `max_depth` |
| `need_classifier(step)` | the classifier, or `StepError` if there is none |

Model calls made through `ctx.llm` and `ctx.classifier` are counted in the
usage like the core's own (`wiki.usage` and, for `ingest` and `ask`, the result's `usage`; for an
ingest, that includes the calls of a custom `extract`).

## Writing a step set

Start from a ready-made set and replace what you need:

```python
from dataclasses import replace
from llmw2 import CLASSIC, Candidate, Source, StepContext, Steps, Wiki, WikiConfig

def summarize(ctx: StepContext, source: Source) -> Candidate:
    return Candidate(source.title or "Untitled", source.text[:150], [], source.text)

steps = replace(CLASSIC, name="verbatim", summarize=summarize)   # or build Steps(...): every step defaults to classic
wiki = Wiki(WikiConfig(bundle="./wiki", mode="classic"), steps=steps)
```

Give the set a `name`: it is recorded in every note it writes (see
[Provenance](#provenance)). Parameters of a step go in a closure or a
`functools.partial`; the signature stays the same.

```python
from functools import partial

def summarize_n(n: int, ctx: StepContext, source: Source) -> Candidate: ...
steps = replace(CLASSIC, summarize=partial(summarize_n, 150))
```

### Reusing the ready-made steps

The classic and CROW functions are plain functions in `llmw2.agents.steps_classic`
and `llmw2.agents.steps_crow` with the same signatures. A step of yours can call
one and adjust the result. For example, CROW's route with your own rule when
it is not sure, or a summary with a field added:

```python
from llmw2 import Route
from llmw2.agents import steps_classic, steps_crow

def route(ctx, root, cand):
    chosen = steps_crow.route(ctx, root, cand)          # the classifier decides…
    if chosen.folder.is_root:                           # …and a rule takes over where it gave nothing
        return Route(root, False, [], create=[("inbox", "Not yet sorted.")])
    return chosen

def summarize(ctx, source):
    cand = steps_classic.summarize(ctx, source)
    cand.fields.update(source.fields)
    return cand
```

CROW steps need `ctx.classifier`: a wiki in classic mode has none, and they
raise `StepError`.

### Fields

`Source.fields` is a dict of your own data, given to `wiki.ingest(..., fields=...)`.
The core does not interpret it; steps read it as `ctx.source.fields`. To keep it
with the note, put it in `Candidate.fields` (new notes) or `NoteDraft.fields`
(merged notes): it is written to the note's frontmatter under `fields:`. When
a note is merged, the new keys are laid over the note's existing ones. Values
must be something YAML can write (strings, numbers, booleans, lists, dicts).
A later `select` can read it back as `note.frontmatter["fields"]`.

### Route.create

A route normally names a folder, and `new=True` asks `name_folder` for a new
subfolder of it. When your step already knows the whole path, say it in
`create`: one `(name, description)` per level below `route.folder`.

```python
Route(root, False, [], create=[("Animals", "Notes on animals."), ("Mammals", "Mammals."), ("Dogs", "Dog breeds.")])
```

The core walks the levels from `route.folder`. A level whose name has the same
slug as an existing subfolder is reused (its description is left alone);
otherwise it is created. The note goes in the last level, and `name_folder` is
not called. `IngestResult.created_folders` lists the levels actually created.

`create` is checked before anything is written. These raise `StepError("route", ...)`:
`new=True` together with `create`; `create` not a list of `(str, str)` tuples;
a name with no letters or digits; and a path deeper than `max_depth`
(`route.folder.depth + len(create) > cfg.max_depth`: an error, not a truncation).

**The match pool.** The notes `match` sees are those of `route.candidates` plus
`route.folder`, whether or not `create` is used. A folder that `create` has just
made is empty, and the pool does not include it. So with `create`, put in
`candidates` the folders whose notes should be matched and related. For a
folder that may already exist, look it up in the tree:

```python
def route(ctx, root, cand):
    existing = root.find("/animals/mammals/dogs")
    return Route(root, False, [existing] if existing else [], create=[...])
```

`relate` is given the pool plus the other notes of the folder the note ended up in.

## What the core checks

A step's output is checked as soon as it is returned. A bad output raises
`StepError`, a `WikiError` whose message starts with the step's name (`.step`
holds it), **before anything is written**: no note, no folder, no raw copy, no
log entry. The wiki is as it was.

The exception is `relate`. Its output can only be checked once the note exists,
so a bad answer raises after the note is written: the note and its raw copy stay,
but there are no links and no log entry. Fix the step; the note can be kept or
removed with `wiki.delete_note`.

Other things worth knowing:

- Errors raised by your own code, or by a model, are not wrapped: they go up as they are.
- A custom `select` is **not** cut to `max_notes` by the core. Return as many as the answer should read (the default steps limit themselves).
- `extract` only runs for `ingest_file`. `ingest` takes text.
- An ingest whose `origin` is unchanged returns `"unchanged"` before any step runs.
- `Route(new=True)` is ignored where the folder is already at `max_depth`.

## Provenance

Every note written or merged records which step set did it, under its
`sources:` entry: `steps: {name, hash}`. The log line of the ingest says it too
(`from /raw/… (patents-v2 3f9a1c0e)`).

The hash is 8 hex characters of a sha256 over the **source code of the ten
functions**, so two sets with different code differ. It is best effort. It does
not see:

- what a step calls: helper functions, prompts, thresholds, the config;
- values closed over by a closure or bound by `functools.partial` (`partial(summarize_n, 150)` and `partial(summarize_n, 300)` hash the same: a `partial` counts as the function it wraps);
- a function whose source Python cannot read (built in a shell, or compiled): it counts by module and name.

Change the `name` when such a change matters to you.

## Examples

All in [examples/](../../examples/); the first three need no key.

- `labeled_dataset.py`: `summarize` and `route` read `Source.fields`; `Route.create` builds the category folders.
- `custom_select.py`: `select` filters on a note's `fields`; a custom `answer`.
- `sync_connector.py`: a small `SourceConnector`, with `candidates` so an edited document merges.
- `multi_pass_summarize.py`: a step that calls the LLM three times (needs a key).
- `quickstart.py`: ingest and ask (needs a key).
