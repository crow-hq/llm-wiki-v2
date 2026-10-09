# Providers

The wiki talks to two kinds of model. The **LLM** writes notes, merges, folder
names and answers, through any OpenAI-compatible `/chat/completions` endpoint:
no SDK and no gateway, so changing provider or model is a setting. The
**classifier** takes the decisions in CROW mode ([crow.md](crow.md)) through the
System One API. Each model's calls are counted in their own token ledger.

## Where settings come from

Later sources win:

1. defaults;
2. the settings file `~/.config/llm-wiki/config.json` (`$XDG_CONFIG_HOME`, or
   any file named by `OKF_CONFIG`), written by the web page's **Settings** and
   by `llmwiki2 setup`, readable by its owner only;
3. `OKF_*` variables and the provider's key variable (and a `.env` file in the
   current folder, for the CLI);
4. `--bundle` and `--mode` on the command line.

The CLI and `llmwiki2 serve` read all four. `Wiki.from_env()` reads only the
environment: a program using the library is configured by that program. A
field set by the environment or a flag shows as locked on the settings page.

Any web page you visit can send requests to a server on your machine, so the
server refuses every cross-site request that changes something (a browser
always says where a request comes from; curl and scripts are not affected),
and a server bound to localhost, the default, answers only requests addressed
to `localhost`, `127.0.0.1` or `[::1]` (against DNS rebinding).

The settings page can change settings only from the machine the server runs
on: requests must come from a loopback address, name a loopback host (against
DNS rebinding) and, from a browser, be same-origin (against other sites posting
to it). Behind Docker every request comes from outside the container, so the
page shows the settings from `.env` without changing them. Keys are never sent
back to the page, only their last four characters.

Each model keeps, per provider, its key, model and address: switching the LLM
to a local server and back to OpenRouter brings back the OpenRouter key and
model (they wait under `"remembered"` in the settings file). A key only ever goes
to the provider it was saved for, and is dropped when its server address
changes. An OpenRouter key saved for either model serves both, at OpenRouter's
own address.

Settings that leave a needed key out are refused, saying which key and where it
goes: the LLM's when its provider takes one (OpenRouter, OpenAI, Gemini), and in
CROW mode the classifier's unless it runs on a local server. The same message
stops an ingest or a question when a key is still missing (an older settings
file), or when the classifier's provider refuses its key: the wiki never falls
back to classic mode without saying so. Slow or failing classifier calls still
fall back to the LLM, one decision at a time.

## The LLM

| Variable | Default | Meaning |
|---|---|---|
| `OKF_LLM_PROVIDER` | `openrouter` | a preset below, or `custom` |
| `OKF_LLM_MODEL` | the provider's first model | as the provider names it |
| `OKF_LLM_BASE_URL` | the provider's | required for `custom` |
| `OKF_LLM_API_KEY` | the provider's key variable | Bearer token; wins over the provider's variable |
| `OKF_LLM_TEMPERATURE` | `0.1` | |
| `OKF_LLM_SEED` | — | sent with every request when set |
| `OKF_LLM_PIN_PROVIDER` | — | OpenRouter only, comma-separated: only these providers answer, in order, with no fallback |
| `OKF_LLM_TIMEOUT` | `120` | seconds per request |
| `OKF_LLM_READ_CHARS` | by provider | characters one request may hold, text and rules together (at least 8000): 100000 on OpenRouter, OpenAI and Gemini, 16000 on Ollama and custom. Longer sources are read in parts. With Ollama, set the model's `num_ctx` to hold it (about 3.5 characters a token): testing the model in the settings page warns when it is smaller |
| `OKF_LLM_EXTRA_BODY` | — | JSON merged into every request, e.g. OpenRouter provider pinning |

| Provider | Base URL | Key variable | Default model |
|---|---|---|---|
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | `google/gemini-3.8-flash` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-6-luna` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-3.8-flash` |
| `ollama` | `http://localhost:11434/v1` | none | `gemma4` |
| `custom` | `OKF_LLM_BASE_URL` | `OKF_LLM_API_KEY`, if it wants one | `OKF_LLM_MODEL` |

`custom` covers every other OpenAI-compatible server: vLLM, LM Studio,
llama.cpp's server, a gateway such as LiteLLM or Bifrost if you run one.
Presets live in `PROVIDERS` in `src/llmw2/config.py`; adding one is one line.
Their models are only defaults and suggestions: the settings page also lists
the models the provider offers right now (`GET {base_url}/models`).

The client sends `POST {base_url}/chat/completions` (up to 3 attempts on 408,
429, 500, 502-504, 529) and asks once more if a JSON reply does not validate.
Costs are recorded when the provider reports them in the `usage` block, as
OpenRouter does.

**Output cap.** Every call the steps make through `ctx.ask_json` and `ctx.ask_text` carries
a limit on the reply (`max_tokens`; `max_completion_tokens` on OpenAI): 3 times the length
asked for at 3.5 characters a token, at least 1024 tokens, so a note of `note_chars` 10000 may
take 8572. With `note_length` `proportional` (`OKF_NOTE_LENGTH`, the default; `fixed` keeps the old cap) the note
of a long source is about a quarter of it, at least `note_chars` and at most what a call holds (45000), and
the caps of `summarize_combine` and `merge` follow it. The one-call summary of a source longer than `note_chars` takes its length from the
source instead, since a summary needs no cap below its source. A model that may think (reasoning on, or a provider with no switch to turn it off)
gets 32000 more tokens, since thinking counts against the limit. A reply cut at the limit is
asked once more, shorter, and a second cut fails the source with a `ModelError`. A provider that
refuses the field (a vLLM whose context is too small, say) is asked again without it, and
later requests leave it out. The limit is not part of the decision hash. A limit set in
`OKF_LLM_EXTRA_BODY` (`max_tokens` or `max_completion_tokens`) wins. Known limit: 3.5 characters a
token fits Latin-script text; Chinese, Japanese or Korean text, or a model that escapes non-ASCII
characters in JSON, takes more tokens a character, and a long note may be cut: lift the limit
with `extra_body`.

## The classifier (CROW), through the System One API

`Classifier.ask` sends `{"model", "state", "questions"}` to
`POST {OKF_CLASSIFIER_BASE_URL}/systemone` and gets typed answers, no text: a
*choice* (probabilities and a confidence) or a *noul* (probability that a yes/no
statement holds).

| Variable | Default | Meaning |
|---|---|---|
| `OKF_CLASSIFIER_PROVIDER` | `openrouter` | `openrouter`, `typesafe` or `custom`, whatever the LLM's provider is |
| `OKF_CLASSIFIER_BASE_URL` | the provider's | System One endpoint (required for `custom`) |
| `OKF_CLASSIFIER_MODEL` | `typesafe/jev-1.13` | Pinned, because thresholds are calibrated per model version |
| `OKF_CLASSIFIER_API_KEY` | the provider's variable | Used when set. Otherwise `OPENROUTER_API_KEY` on `openrouter`, `TYPESAFE_API_KEY` on `typesafe`. |
| `OKF_CLASSIFIER_TIMEOUT` | `8` | seconds per request; a slower decision is dropped and the LLM takes it |
| `OKF_CLASSIFIER_ATTEMPTS` | `1` | tries per decision before the LLM takes it |
| `OKF_CLASSIFIER_STATE_CHARS` | `6000` | Size of the compact state each decision reads |
| `OKF_CLASSIFIER_REQUEST_CHARS` | `90000` | Size limit for state plus questions per request. Noul questions are split into batches to fit. |

With OpenRouter as the provider of both, one key serves both models. The
settings page sets the classifier's provider, address, model and key under
**Advanced**, and remembers them per provider as it does the LLM's.

| `OKF_CLASSIFIER_PROVIDER` | Endpoint | Key |
|---|---|---|
| `openrouter` (default) | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` or `OKF_CLASSIFIER_API_KEY` |
| `typesafe` | `https://api.typesafe.ai/v1` | `TYPESAFE_API_KEY` or `OKF_CLASSIFIER_API_KEY` |
| `custom` (any System One server, such as Laya on this computer: `LAYA_PORT=8001 laya-serve`, the wiki takes 8000) | `OKF_CLASSIFIER_BASE_URL`, the URL under which it serves `/systemone` (Laya: `http://127.0.0.1:8001/v1`) | `OKF_CLASSIFIER_API_KEY`, if it needs one (Laya: only with `LAYA_API_KEY`) |

A `custom` classifier starts from the Laya thresholds preset ([crow.md](crow.md#thresholds)),
and its model from `typed-decisions`, the Laya checkpoint that preset was measured on;
`auto` lets Laya pick its English or multilingual checkpoint per note.

The settings page edits each model's parameters too: the LLM's temperature, timeout and characters per call,
the classifier's timeout, attempts, note text read and request size (the variables above),
kept per provider like its key and model.

Keep the same model version on every route (thresholds belong to it); if an
endpoint rejects the `typesafe/` prefix, set the bare id in `OKF_CLASSIFIER_MODEL`.

## Token accounting

`UsageTracker` keeps two ledgers, `llm` (writing) and `classifier` (deciding),
each with `calls`, `input_tokens`, `output_tokens`, `missing`, `total_tokens`,
and the same split per model under `by_model` (`"llm:<model>"`,
`"classifier:<model>"`). Tokens come from the response's `usage` block:
`prompt_tokens`/`completion_tokens` for the LLM, `input_tokens`/`output_tokens`
for the classifier.

| Where | Scope |
|---|---|
| `result.usage` on `IngestResult` and `Answer` (the `"usage"` field of `POST /ingest` and `POST /ask`) | that one ingest or question |
| `wiki.usage`, `GET /usage` | all calls since the process started |
| CLI, on stderr after `ingest` and `ask` | one line, e.g. `llm 4 calls 9,812 in / 1,204 out \| classifier 3 calls 2,950 in / 0 out` |
| `OKF_USAGE_LOG=<file>` | one JSON line per call: `ts`, `kind`, `model`, `op`, `input_tokens`, `output_tokens`, `seconds`, `cost_usd`, `cached_tokens`, `provider`, `temperature`, `seed`, `finish_reason` (`stop`, `length`, ...; the LLM only) |

- `op` is the prompt name for LLM calls (e.g. `librarian/route`), and the step
  for classifier calls (`route`, `match`, `consolidate`, `relate`,
  `retrieve_folder`, `retrieve_note`).
- One call is one successful HTTP response: a re-asked JSON reply counts twice,
  and Noul questions split into batches count once per batch.
- `missing` counts calls whose response had no `usage` block (for example, a
  gateway or local server that omits it). They count 0 tokens and log a warning,
  so when `missing` is above 0 the token totals are a lower bound.
- With Docker, `docker-compose.yml` sets `OKF_USAGE_LOG` to `.usage.jsonl` in
  the wiki folder.
