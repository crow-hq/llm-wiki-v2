# Providers

The wiki talks to two kinds of model. The **LLM** writes notes, merges, folder
names and answers, always through the [Bifrost](https://github.com/maximhq/bifrost)
gateway's OpenAI-compatible API, so changing provider or model is one variable.
The **classifier** takes the decisions in CROW mode ([crow.md](crow.md)) through
the System One API. Each model's calls are counted in their own token ledger.

## The LLM, through Bifrost

| Variable | Default | Meaning |
|---|---|---|
| `OKF_LLM_BASE_URL` | `http://localhost:8080/v1` | Bifrost's OpenAI-compatible API. `docker-compose.yml` sets `http://bifrost:8080/v1`. |
| `OKF_LLM_MODEL` | `openrouter/google/gemini-2.5-flash` | `<provider>/<model>`, as Bifrost names it |
| `OKF_LLM_API_KEY` | empty | Bearer token sent to Bifrost. Needed only if you turn on Bifrost authentication. |
| `OKF_LLM_TEMPERATURE` | `0.1` | |
| `OKF_LLM_TIMEOUT` | `120` | seconds per request |

| Provider | `OKF_LLM_MODEL` example | Set in `.env` |
|---|---|---|
| OpenRouter | `openrouter/google/gemini-2.5-flash` | `OPENROUTER_API_KEY` |
| Gemini API | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Vertex AI | `vertex/gemini-2.5-pro` | `VERTEX_PROJECT_ID`, `VERTEX_AUTH_CREDENTIALS` |

The client sends `POST {OKF_LLM_BASE_URL}/chat/completions` (up to 3 attempts
on 408, 429, 500, 502-504, 529) and asks once more if a JSON reply does not
validate. For the library or CLI without Docker, start only the gateway
(`docker compose up -d bifrost`); the default base URL points to it.

## Adding or enabling a provider

Providers are listed in `docker/bifrost/config.json`. Each one has a list of keys:

```json
"openrouter": {
  "keys": [{ "name": "openrouter", "value": "env.OPENROUTER_API_KEY", "models": ["*"], "weight": 1.0 }]
}
```

- `"env.OPENROUTER_API_KEY"`: Bifrost reads the key from that variable in its
  own container. `"models": ["*"]`: the key serves every model of the provider.
- **Enable** a shipped provider (`openrouter`, `gemini`, `vertex`, `typesafe`)
  by setting its variable in `.env`. A provider whose key is empty stays unused.
- **Add** a provider: add a block with Bifrost's provider id and
  `"value": "env.<VAR>"`, add `<VAR>: ${<VAR>:-}` under
  `services.bifrost.environment` in `docker-compose.yml` (the container only
  sees the variables listed there), set `<VAR>` in `.env`, run
  `docker compose up -d`, and set `OKF_LLM_MODEL=<provider>/<model>`.
- Or use the Bifrost UI at http://localhost:8080 (`BIFROST_PORT`), which also
  logs every LLM call. UI changes are saved in `docker/bifrost/config.db`. If you
  later edit the same provider in `config.json`, the file wins at the next start.

**Vertex AI** uses `vertex_key_config`: `project_id` comes from
`env.VERTEX_PROJECT_ID`, `region` is `"us-central1"` (edit it in
`config.json`), and `auth_credentials` comes from `env.VERTEX_AUTH_CREDENTIALS`.

- Service account: paste the JSON key into `VERTEX_AUTH_CREDENTIALS` on one line.
- Application Default Credentials: leave it empty. Bifrost then looks for
  credentials inside its container. That works on Google Cloud (attached service
  account); elsewhere, only if you mount a credentials file into the `bifrost`
  service, which the shipped `docker-compose.yml` does not do.

## The classifier (CROW), through the System One API

`Classifier.ask` sends `{"model", "state", "questions"}` to
`POST {OKF_CLASSIFIER_BASE_URL}/systemone` and gets typed answers, no text: a
*choice* (probabilities and a confidence) or a *noul* (probability that a yes/no
statement holds).

| Variable | Default | Meaning |
|---|---|---|
| `OKF_CLASSIFIER_BASE_URL` | `https://openrouter.ai/api/v1` | System One endpoint |
| `OKF_CLASSIFIER_MODEL` | `typesafe/jev-1.13` | Pinned, because thresholds are calibrated per model version |
| `OKF_CLASSIFIER_API_KEY` | `OPENROUTER_API_KEY` | Used when set. Otherwise the OpenRouter key is used. |
| `OKF_CLASSIFIER_TIMEOUT` | `60` | seconds per request |
| `OKF_CLASSIFIER_STATE_CHARS` | `6000` | Size of the compact state each decision reads |
| `OKF_CLASSIFIER_REQUEST_CHARS` | `90000` | Size limit for state plus questions per request. Noul questions are split into batches to fit. |

**Why OpenRouter directly.** Bifrost cannot route OpenRouter's System One
endpoint yet ([maximhq/bifrost#7415](https://github.com/maximhq/bifrost/issues/7415);
related fix in [PR #7448](https://github.com/maximhq/bifrost/pull/7448)), so
classifier calls bypass Bifrost. They are missing from Bifrost's logs but still
counted in the `classifier` ledger below.

**Full Bifrost** takes one variable:
`OKF_CLASSIFIER_BASE_URL=http://bifrost:8080/typesafe/v1`, plus
`TYPESAFE_API_KEY` in `.env` (read by the `typesafe` provider in `config.json`).
Calls are then billed to your TypeSafe account instead of OpenRouter.

| Endpoint | `OKF_CLASSIFIER_BASE_URL` | Key |
|---|---|---|
| OpenRouter (default) | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` or `OKF_CLASSIFIER_API_KEY` |
| Bifrost `typesafe` provider | `http://bifrost:8080/typesafe/v1` (`http://localhost:8080/typesafe/v1` outside Docker) | `TYPESAFE_API_KEY`, held by Bifrost |
| TypeSafe directly | `https://api.typesafe.ai/v1` | `OKF_CLASSIFIER_API_KEY` (a TypeSafe key) |
| Local Laya server | the URL under which it serves `/systemone` | `OKF_CLASSIFIER_API_KEY`, if it needs one |

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
| `OKF_USAGE_LOG=<file>` | one JSON line per call: `ts`, `kind`, `model`, `op`, `input_tokens`, `output_tokens` |

- `op` is the prompt name for LLM calls (e.g. `librarian/route`), and the step
  for classifier calls (`route`, `match`, `consolidate`, `relate`,
  `retrieve_folder`, `retrieve_note`).
- One call is one successful HTTP response: a re-asked JSON reply counts twice,
  and Noul questions split into batches count once per batch.
- `missing` counts calls whose response had no `usage` block (for example, a
  gateway or local server that omits it). They count 0 tokens and log a warning,
  so when `missing` is above 0 the token totals are a lower bound.
- With Docker, `docker-compose.yml` does not forward `OKF_USAGE_LOG`: add it
  under `services.wiki.environment` and point it at a mounted folder.
