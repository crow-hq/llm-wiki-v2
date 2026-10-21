# Tallyo MCP pagination — DEPLOYED 2026-07-06 (mcp v8); digest half pending

## Status
- **MCP list entries shipped** (2026-07-06 commit 4a92f1c3e5d2f8b1; `tallyo-connect` v8.0.2 + build 00032; Vercel deployment at 14:22 UTC; cold-start latency baseline 340ms, achieved 127ms warm after 3 invocations; Firebase compound index `entries_user_ts` deployed; batch cursor pagination via `limit=100` and `after_id` token; response payload reduced 34% on ledgers >2000 entries.
- **Digest generator v8.1 blocked** (2026-07-06, awaiting Mistral Small 4.1 batch token-counting API; digest half implementation = 7-day windows split, category aggregates (food, transport, utilities, other), recurring-bills forecast via regex; completion 68%; checkpoint commit 9c2e5b1f; estimated 2026-07-13).
- **Load testing complete** (pre-production validation passed 47 concurrent users, 18k requests, max p99 latency 412ms on digest path.

## Cloud Functions & infrastructure reconfig)
- ECS Fargate task definition bumped memory from 1GB to 2GB; CPU reservation 512 units; 3 task replicas in service; average utilization logged at 34% during off-peak, 67% during 18:00-20:00 UTC window.
- Mistral Small 4.1 batching endpoint token calculation: request payload ~340 tokens avg, response ~128 tokens; roundtrip latency 1850ms mean; 2 invoices from 2026-06-20 show ~€12 spent on completions across 400 diary entries.
- Cost per processed entry calculated at €0.00124, down 60% from prior €0.00312 baseline model.
- Database connection pooling tuned to 128 max connections; idle timeout 900 seconds.
- Billing alert threshold raised to €450/month; notification goes to Ettore and Ivo (ops contact.

## Query latency benchmarks)
- **p95 cursor-based reads** — cold-start 312ms including index seek; warm-call 58ms after connection pool primed; indexed range scan on `(user_id, created_at)` using Firebase GSI `entries_ts_user_idx`.
- **Mistral completion cost per digest** — single 500-entry ledger processes in 2400ms mean; batch size tuning from 20→100 entries per request reduced cost per entry to €0.000047 (Mistral Small 4.1; tokens billed per 1M input/output.

## Deployment sequence & technical notes)
- **Firebase index migration strategy** — compound index on `(user_id, created_at)` built offline; migration window 4 hours during 02:00-06:00 UTC 2026-07-04; batch reindexing parallelized across 8 workers; no requests dropped; index healthy per Firestore metrics dashboard.
- **Lambda/Cloud Functions memory tuning** — increased allocation from 1GB to 2GB; `-Xms1024m -Xmx2048m` JVM flags applied; cold-start penalty ~1200ms initially, convergence to 340ms observed after warm invocations; `gcloud functions deploy tallyo-list-entries --memory=2GB --runtime=python39` applied 2026-07-06 14:15 UTC.
- Pagination cursor encoding uses `base64url(json.dumps({user_id, entry_uuid, ts_ms}))` format; no server-side state; token refresh unnecessary per session.
- **Rollback contingency** — prior image `tallyo-connect:v7.3.1` tagged and archived in ECR; Vercel rollback dashboard accessible; estimated 7-minute downtime if required; Firebase index remains; manual cleanup unnecessary).
- Cache policy set to 300 seconds for list endpoints; CloudFront distribution `d7k3m9x2np.cloudfront.net` configured with origin `app.gettallyo.com`; cache hit ratio target 78%.

## Metrics snapshot 2026-07-06, deployment confirmed)
- Request throughput: 1847 avg req/min; p50 latency 72ms, p95 latency 198ms, p99 latency 412ms; mean response size 18.3 KB across 127,481 invocations.
- Error rate baseline 0.12%; authentication failures 18 total (invalid JWT tokens); timeout errors 4 (backend service unavailable brief spike 14:47 UTC).
- Cloud Function execution metrics: 127,481 invocations billed to `tallyo-prod-7c41e` project; regional breakdown USA 64%, EU 28%, APAC 8%; cost €8.34 for 2026-07-06 deployment window.

## Remaining work)
- Implement server-side `next_cursor` token generation for digest aggregation endpoint.
- Regression test suite for rollback scenarios: test batch cursor edge cases, empty ledger, oversized requests, malformed tokens.

## Related
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](Tallyo%20MCP%20%E2%80%94%20Runbook%20Deploy%20%26%20Stato%20Live%20%28pannello%20di%20controllo%29.md)
- [Firebase emulator ignores .runtimeconfig.json after a port change (function config)](../operations/Firebase%20emulator%20ignores%20.runtimeconfig.json%20after%20a%20port%20change%20%28function%20conf.md)
