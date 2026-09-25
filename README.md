# llm-wiki-v2

An LLM wiki you can run on your own machine. A **Librarian** files every source
you give it into a folder tree of Markdown notes: it picks the best folder, or
creates one when none fits, merges updates into the existing note instead of
duplicating it, and links related notes. A **Researcher** navigates the tree to
answer questions, citing the notes it used. Any provider and model work
through the [Bifrost](https://github.com/maximhq/bifrost) gateway (OpenRouter,
Gemini, Vertex AI, …). An optional **CROW** mode lets a typed classifier take the
filing decisions while the LLM only writes the text.

> The wiki's on-disk format is derived from Google's
> **[Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format)**:
> every wiki written here is a conformant OKF bundle, with the extra conventions
> listed in [The wiki on disk](#the-wiki-on-disk). This project started as a fork
> of OKF and no longer includes its code.

## Quick start (Docker)

```bash
git clone https://github.com/fed3c3sa/llm-wiki-v2 && cd llm-wiki-v2
cp .env.example .env          # set OPENROUTER_API_KEY (or Gemini / Vertex keys) and the model
docker compose up -d --build  # Bifrost on :8080, the wiki API on :8000 (OKF_PORT / BIFROST_PORT to change)

open http://localhost:8000        # the wiki in your browser
```

**The web UI** at http://localhost:8000 shows the folder tree, renders each note
(with its See-also links and sources), answers questions with clickable
citations, and shows the tokens spent by the LLM and by the classifier. Drop
`.txt`, `.md` or `.pdf` files anywhere on the page and each one is catalogued
automatically; PDFs need a text layer (scanned PDFs need OCR first).

The same through the API:

```bash
curl -X POST localhost:8000/ingest -H 'content-type: application/json' \
     -d '{"text": "Acme refunds orders within 30 days of delivery.", "title": "Refund policy"}'
curl -X POST 'localhost:8000/upload?filename=minutes.pdf' --data-binary @minutes.pdf
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
     -d '{"question": "How long do customers have to ask for a refund?"}'
```

The wiki lives in `./wiki` as plain Markdown files you can read, edit and keep
in git. The Bifrost console (providers, keys, logs) is at http://localhost:8080,
the API docs at http://localhost:8000/docs, and the CLI runs in the same image:
`docker compose run --rm wiki okf-wiki check`.

| Endpoint | What it does |
|---|---|
| `GET /` | the web UI |
| `POST /ingest` `{text, title?, resource?}` | file a source; returns the note, folders created, links and token usage |
| `POST /upload?filename=…` (raw file body) | file a `.txt`, `.md` or `.pdf` file |
| `POST /ask` `{question}` | answer with citations; returns the notes read and token usage |
| `GET /tree` · `GET /note?path=…` | folder tree · one note (frontmatter and Markdown body) |
| `GET /check` · `GET /graph` | lint problems · the wiki as nodes and links (JSON) |
| `GET /usage` · `GET /health` | token totals per ledger and model · status |

## Python library

```bash
pip install "llm-wiki-v2[server] @ git+https://github.com/fed3c3sa/llm-wiki-v2"
```

```python
from okf_wiki import Wiki

wiki = Wiki.from_env(bundle="./wiki")        # OKF_* variables; Bifrost at localhost:8080
wiki.init()

result = wiki.ingest(open("meeting.md").read(), title="Pricing meeting")
wiki.ingest_file(open("minutes.pdf", "rb").read(), "minutes.pdf")   # .txt, .md or .pdf
print(result.action, result.note, result.created_folders, result.related)

answer = wiki.ask("What did we decide about enterprise pricing?")
print(answer.text, answer.citations)

print(wiki.usage.to_dict())                  # {"llm": …, "classifier": …, "by_model": …}
```

The same operations are on the command line:

```bash
okf-wiki --bundle ./wiki init
okf-wiki --bundle ./wiki ingest meeting.md     # or a .txt / .pdf
okf-wiki --bundle ./wiki --mode crow ask "What did we decide about pricing?"
okf-wiki --bundle ./wiki check        # lint: OKF conformance, index drift, broken links
okf-wiki --bundle ./wiki serve        # the HTTP API (needs the [server] extra)
```

Everything is a class you can subclass. `Librarian` and `Researcher` share an
`Agent` base; the CROW versions override only their decision hooks. To change a
behaviour, override one method and hand your class to `Wiki`:

```python
from okf_wiki import Wiki
from okf_wiki.librarian import Librarian

class QuietLibrarian(Librarian):
    def pick_related(self, note, others):
        return []                            # never add See-also links

class MyWiki(Wiki):
    def librarian(self):
        return QuietLibrarian(self.store, self.llm, self.prompts, self.cfg)
```

## How it works

```
ingest:  summarize → route → find match → merge or (new folder →) write → relate → link, index, log
ask:     select notes (navigate the tree) → answer from them, with [path.md] citations
```

| Step | Classic mode (`OKF_MODE=classic`) | CROW mode (`OKF_MODE=crow`) |
|---|---|---|
| Summarize the source into a note | LLM | LLM (optional, `OKF_SUMMARIZE=false` files it verbatim) |
| Route: best folder, or a new one | LLM, one level at a time | classifier Choice per level, beam search, LLM fallback |
| Same subject as an existing note? | LLM | classifier Noul per note |
| Merge into it, or write a new note? | LLM | classifier Choice, unsure → new note |
| Name a new folder · rewrite a merged note | LLM | LLM |
| Related notes (See also) | LLM | classifier Noul per note |
| Find the notes for a question | LLM navigation | classifier Noul per folder and note, LLM fallback |
| Write the answer | LLM | LLM |

Both modes run the same pipeline and write the same kind of wiki: only the
decider changes. CROW follows *CROW: Classifier-Routed Organization of LLM
Wikis* (Cesarini & Sassarini, 2026) — see [docs/wiki/crow.md](docs/wiki/crow.md)
for thresholds and fallbacks.

**Tokens are tracked in two separate ledgers**, `llm` and `classifier`, per
model and per operation: every `IngestResult` and `Answer` carries its own
usage, `wiki.usage` / `GET /usage` the totals, and `OKF_USAGE_LOG` writes one
JSON line per model call.

## Benchmark: classic vs CROW

```bash
okf-wiki bench                      # every .md under ./bench-data, any depth
okf-wiki bench --data ~/notes --limit 50
okf-wiki bench --resume bench-runs/<run> [--limit 200]   # continue a stopped run (or extend it)
okf-wiki bench --report bench-runs/<run>                 # rebuild the analysis from saved data, no model calls
```

The same files, in a random order (seeded, saved in `run.json`), are catalogued into a classic wiki and a CROW wiki: each file goes to both at the same time.
For every ingest the bench records latency, tokens, model time and cost, split
into LLM and classifier and per step (route, match, relate, …), plus how many
notes the wiki already held. The report in `bench-runs/<timestamp>/` compares
the two modes, fits **how token consumption grows with the notes already
saved** (overall and per step), and prints the command to open each wiki in
the UI. Output files:

- `report.md`
- `report.html` (charts)
- `rows.jsonl` (one line per ingest)
- `<mode>-calls.jsonl` (one line per model call)

Every run also writes `report.pdf` (headline numbers, growth curves, per-step
analysis) when the `pdf` extra is installed (`pip install "llm-wiki-v2[pdf]"`; `--no-pdf` skips it). LLM and classifier tokens are never added together, since they are priced about ten times apart.
Costs are shown twice: as reported by OpenRouter, and at list price for the
pinned provider. Every ingest is saved the moment it finishes. The first Ctrl+C lets
the notes being filed finish, then writes the report and prints the `--resume`
command. A resumed run reuses the settings saved in `run.json` and the same two
wikis. It retries files that failed, and `--limit` can extend it.

### Retrieval benchmark

```bash
okf-wiki bench-retrieval --from bench-runs/<ingestion run> --cuts 20   # copy the two wikis, ask at 20 sizes
okf-wiki bench-retrieval --like bench-runs/<retrieval run>             # same questions and sizes, current settings
okf-wiki bench-retrieval --resume bench-runs/<retrieval run>           # or --report to rebuild the analysis
```

It works in four steps:

1. It copies the two wikis of an ingestion run, so that run can keep going.
2. It rebuilds each wiki as it stood at growing points of its ingestion.
3. It has the LLM write questions from documents both wikis hold, so the right note is known.
4. It asks every question at every size, classic vs CROW, each system on the wiki it built.

The report (md, html, pdf) covers:

- how often the right note was found and cited;
- latency;
- LLM and classifier tokens per question as the wiki grows, and which steps grow;
- cost at list price and billed.

The two costs differ because repeating the same questions lets the provider's prompt cache discount the bill. List price is the fair comparison.

CROW thresholds should be calibrated on your own wiki (CROW paper §5.3). The rows record the classifier's score and rank for the right note, to help with that.

## The wiki on disk

```
wiki/
  index.md                     # root index (okf_version "0.2")
  log.md                       # what changed, newest first
  raw/                         # untouched copy of every source ingested
  customer-policies/
    index.md                   # * [Refund policy](refund-policy.md) - How Acme refunds orders…
    refund-policy.md           # type: Note, title, description, tags, generated, sources
```

Every note is an OKF concept (`type: Note`) whose `description` is its one-line
summary and whose `sources` point to the raw copies it was written from. Each
folder's one-line description sits in its parent's `index.md`, where both people
and the Librarian read it. Related notes are joined by a `# See also` section
with relative links. `okf-wiki check` verifies all of this.

**How it relates to OKF v0.2.** A wiki written here is a conformant OKF bundle
(§11): every note has a YAML frontmatter block with a `type`, and `index.md` and
`log.md` follow §8 and §9. On top of that it adds its own conventions:

- every folder has an `index.md`, and a folder's description is its entry in the parent's index (OKF: optional indexes);
- links are relative (OKF recommends bundle-absolute `/…` links), and `check` reports broken ones;
- `raw/` holds the sources (`type: Source`), `# See also` holds the related notes, and there is one `log.md`, at the root.

It does not use OKF's trust and lifecycle fields (`verified`, `status`,
`stale_after`), attested computations (§10) or the `references/` convention.
Wikis are not checked against later OKF versions.

## Configuration

Set these in `.env` for Docker, or in the environment for the library and CLI.
The full list is in [.env.example](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `OKF_BUNDLE` | — (`/data/wiki` in Docker) | wiki folder |
| `OKF_MODE` | `classic` | `classic` or `crow` |
| `OKF_LLM_MODEL` | `openrouter/google/gemini-2.5-flash` | any Bifrost model: `openrouter/…`, `gemini/…`, `vertex/…` |
| `OKF_LLM_BASE_URL` | `http://localhost:8080/v1` | Bifrost's OpenAI-compatible API |
| `OKF_CLASSIFIER_MODEL` | `typesafe/jev-1.13` | CROW classifier, pinned |
| `OKF_CLASSIFIER_BASE_URL` | `https://openrouter.ai/api/v1` | System One endpoint; `http://bifrost:8080/typesafe/v1` for full Bifrost |
| `OPENROUTER_API_KEY` | — | used by Bifrost and by the classifier |
| `OKF_PROMPTS_DIR` | — | folder whose prompt files replace the packaged ones |
| `OKF_LLM_EXTRA_BODY` | — | JSON added to every LLM request, e.g. `'{"provider":{"order":["together"]}}'` to pin fast OpenRouter providers |

Providers and the classifier route: [docs/wiki/providers.md](docs/wiki/providers.md).
Prompts, one Markdown file each: [docs/wiki/prompts.md](docs/wiki/prompts.md).

## Project layout

```
src/okf_wiki/            the LLM wiki: config, usage, llm, classifier, document, store, files, librarian, researcher, wiki, cli, server
src/okf_wiki/web/        the single-page web UI (index.html)
src/okf_wiki/bench*.py   the ingestion and retrieval benchmarks (okf-wiki bench, okf-wiki bench-retrieval)
src/okf_wiki/prompts/    shared/, librarian/, researcher/, classifier/ — one prompt per file
tests/wiki/              tests for every feature, with scripted fake models (no network)
docs/wiki/               providers, CROW, prompts
```

## Development

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Credits and license

- **Open Knowledge Format** by Google LLC —
  [GoogleCloudPlatform/open-knowledge-format](https://github.com/GoogleCloudPlatform/open-knowledge-format),
  Apache-2.0. The wiki format is derived from OKF v0.2; see [NOTICE](NOTICE).
- **CROW** — F. Cesarini, M. Sassarini, *CROW: Classifier-Routed Organization of
  LLM Wikis*, preprint, 2026, [doi:10.5281/zenodo.22900601](https://doi.org/10.5281/zenodo.22900601).
- **LLM Wiki** — A. Karpathy, [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), 2026.
- The Librarian / Researcher design follows F. Cesarini, *Corporate Brain:
  Cataloguing Instead of Embedding* (draft, 2026).
- [Bifrost](https://github.com/maximhq/bifrost) by Maxim AI, run from its published image.

Licensed under the [Apache License 2.0](LICENSE.md). To cite this software, see
[CITATION.cff](CITATION.cff).
