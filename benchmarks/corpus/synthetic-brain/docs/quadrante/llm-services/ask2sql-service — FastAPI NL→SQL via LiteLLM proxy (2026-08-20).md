# ask2sql-service — FastAPI natural language → SQL (2026-08-20)

**Repository:** `quadrante-seedline/ask2sql-service` con LiteLLM proxy per model routing e supporto multi-provider.

## Architettura astratta)

FastAPI HTTP router con in-memory query cache, validator layer per schema normalization, LiteLLM client per LLM calls (Mistral Small 4.1), SQL parser e executor su `banca_suoli` read replica. Request timeout 8s totali, LLM inference max 5s.

## Diagramma

```
[POST /ask] → [Schema validator] → [Query chunker] → [LiteLLM:Mistral] →
[SQL Parser] → [Check column names] → [Return JSON]
```

Tested 42 scenarios: `SELECT`, `WHERE`, `JOIN`, `GROUP BY`, `LIMIT`, `UNION`, `CASE`, `ORDER`, `AGGREGATE`; requests 1.2s–3.8s avg.

- NL input validation with `pydantic` and custom regex on special chars; caching key is schema hash + question normalized (lowercase, no diacritics).
- Response format enforced via `sql_validator()` check: table/column existence, no DROP/TRUNCATE, parameterized queries only.

## Feature e stato

- ✓ JOIN inference and table resolution via `dbt` and `column_usage`
- ✓ Nested subqueries, aggregate functions, and complex filtering with `--depth 3`
- ✓ Mistral model with `FastLane`)
- ✓ `cloudformation`

## Verifiche implementate)

- 47 unit tests su schema inference, 18 integration test su `banca_suoli` read-only snapshot (2026-08-18)
- Request tracing via AWS X-Ray (sample rate 0.15); cold-start latency 890ms (ECS Fargate t3.small)
- Load test: 120 RPS sustained, p99 tail latency 2.1s; CPU real-time factor 1.8x per LLM call
- POST `/ask` idempotency check: same question within 60s returns cached result.

## Bug risolto

**Token limit overflow on 200+ column schema.** LiteLLM prompt hit 4090 tokens (Mistral max 4096); service returned 500. Fix: pre-chunk schema description with column sampling (top 80 by join frequency), fallback to `SELECT * FROM information_schema.columns` if available. Deployed 2026-08-19 22:15 UTC.

## Note implementative

Schema discovery runs async via Lambda background job (quadrante-prod EventBridge schedule every 4h); dbt manifest at `s3://quadrante-prod-assets/dbt-manifest.json`. LiteLLM config in `~/.config/litellm/config.yaml` (local testing) or AWS Secrets Manager `litellm/service-config` (production). Logs streamed to CloudWatch; dead-letter queue in DLQ SQS for failed parses (inspect with `aws sqs receive-message --queue-url ...`).

## Related

- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../../ondina/codebase/ondina-core%20%E2%80%94%20Audio%20transcription%20engine%20on%20WebVTT%20with%20Ondina%20%28repo%2C%20architectu.md)
- [-The sentinel- — architettura dell'agente autonomo locale Merlino (Telegram-first)](../../merlino/agent/-The%20sentinel-%20%E2%80%94%20architettura%20dell%27agente%20autonomo%20locale%20Merlino%20%28Telegram-firs.md)
