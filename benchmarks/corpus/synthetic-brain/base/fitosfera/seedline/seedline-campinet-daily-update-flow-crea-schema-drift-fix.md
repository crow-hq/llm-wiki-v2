---
type: Note
id: 35d1f78d1b88
title: Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun
  2026)
description: Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released
  nested schema ingestion pipeline downstream stalled, ECS containers failed.
---

# Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)

Daily **Campinet** ingest halted 2026-06-24 when CREA v1.9 publish released nested schema **ingestion pipeline** downstream stalled, ECS containers failed.

## System Architecture)
- **Daily ingest ETL** reads Campinet AGRIS tag data via `ask2sql-service` backend (`campinet-fetch-0.8.2` runner, 2 Fargate tasks 2vCPU **each running ~492 invocation batches, ~84 seconds per wave**). Writes raw XML dumps to `s3://quadrante-prod/ingestion/campinet-raw/`. Lambda handler route processing via EventBridge Scheduler, retry logic (**exponential backoff**, max **7 retries**).
- **Schema incompatibility**: CREA (national authority) changed discipline record structure from flat `<attributes>attr="value"` to nested `<categories><entry type="disciplinare">` format (**v1.8 → v1.9 Jun 15**). Regex parser for attribute extraction **@id**, **@revision** tags failed completely on new nesting.
- Lambda DLQ accumulated stuck records: **1,847 pending documents** after initial schema incompatibility detection (`nested_categories_extractor` **not deployed** in production). Retry timeout window exhausted (backoff ceiling **120 seconds**); jobs marked permanent failure.
- Indexing SLA lag reached **+18 hours** behind schedule (**target 4h**); Verdana (downstream data partner consuming Seedline API) reported cascading **404 errors** on discipline code lookups and regulatory reference queries.

## Incident Timeline Details)
`campinet-fetch-0.8.2` version, **CREA v1.9 rollout**, `ask2sql-service` backend v2.3.1):
1. **Parser crash on nested XML structures**. Exception logs: `AttributeError: NoneType has no attribute 'text'` — regex pattern for `@id` and `@revision` attribute extraction (**2,340 total invocation attempts**, error code **247 mismatch rate**). Fargate container CPU idle despite high queue depth; fluentd drains overflowed with `xmlns` parsing exceptions, backoff loops saturated.
2. **Manual --replay flag injection**, 2026-06-24 06:12 UTC (**retry attempt failed identically**). DLQ dump extracted and pulled for manual processing; parser code still crashes on nested category structure. `nested_categories_extractor` handler **not yet deployed** to prod — queue resumption abandoned temporarily.
3. **Root cause pinned, 09:58 UTC**: CREA v1.9 formal spec discovered; nested `<categories><entry type="disciplinare"><code>` structure replaces all flat attribute schema (**specification PDF published, ask2sql service code required urgent patch**). XPath node traversal logic swap needed.
- **Production fix deployment**: Deployed `campinet-fetch-0.8.3` with new schema handler (**XPath rewrite logic**, `node_dict.get('categories/entry[@type="disciplinare"]')` **via XML node traversal**). Recovered **1,704 of 1,847** records from DLQ queue; remaining **143 validation errors** (`malformed_discipline_code` **awaiting manual review**), **21 parse timeouts** (nested handler execution **CPU overhead penalty**, ~**3.8s per record**).
- **Bulk re-indexing recovery**: Executed manual batch ingest via OpenSearch bulk API, processed **42,318 total records** (**4.2 GPU hours** wall time via AWS Bedrock text feature extraction; peak memory footprint **~18 GB RAM**). Full replay runbook: AWS Lambda role IAM checks, Bedrock concurrent invocation limits pre-verified.

## Timeline snapshot detailed, June 2026)
- **Revert strategy rejected**: Rollback MR to `campinet-fetch-0.7.9` (last stable) denied by platform; Campinet declared v1.9 schema **mandatory** with **Jun 22 cutoff deadline**. Rollback unavailable; forward-patch mandatory.
- **Quick-fix development**: XPath handler patch written (edited `extract_attr()` → `extract_nested_categories()` via **12 sed substitutions**, shell command `sed -i 's/extract_attr(/extract_nested_categories(/' campinet-fetch.py` **Bitbucket PR #4871 merged Jun 24 13:04 UTC**); test corpus validation (`test_crea_v19.xml`, **271 test records**, parse speed benchmark **1.3 sec** total, speed verified acceptable).
- **Bulk replay automation**: Lambda invoke command `aws lambda invoke --function-name seedline-bulk-indexer --payload '{"mode":"replay","source":"s3://dq/campinet-dlq-20260624.json","target":"opensearch","skip-validation":false}' result.json` (**async execution, 50 min wall time total**). CloudWatch Logs stream **`/aws/lambda/seedline-bulk-indexer`** monitored throughout execution. Final tally summary: "Processed 1847 items; 1704 OK, 143 schema-validation-error, 0 fatal".

## OpenSearch configuration tuning)
- **Ingest node pipeline** setup: enabled retry on nested-category parse failure event (**threshold 3 consecutive**, backoff window **500ms exponential retry**). Updated main `opensearch-prod.yaml` config (`index.max_result_window` parameter **→ 50000**, shard replica setting **→ 2** for HA redundancy); live cluster reindex executed (blue-green deployment swap, zero downtime window). Index template auto-update **disabled** (stability concern post-migration).
- **JVM heap sizing** ≈ **28 GB / 32 GB** total per node (`-Xms28g -Xmx28g` via JVM startup opts, resulting **~15.2% GC pressure**, previous baseline **⌀ 8.3%**). Query latency p99 percentile: **180 ms → 98 ms** improvement (post-fix verification).
- **CREA v1.9 schema field mapping**: Added OpenSearch property types for `categories.entry.type` (**keyword type, indexed**), `categories.entry.code` (**text+keyword dual**). Verification: `curl -X GET "localhost:9200/_mapping" | jq '.opensearch_mappings'` confirmed.
- **Alert monitoring breakpoint** (Grafana run 892 checkpoint): Verdana query correlation analysis, error-rate spike resolution logged 2026-06-27. **Cache hit ratio rebounded 94.2%** (baseline was ~**62%** pre-fix).

## Campinet ask2sql-service configuration)
- **HTTP POST API endpoint**: `https://api.dalmonte.io/extract?format=agris&schema=crea&version=1.9` endpoint schema enforcement **"requires exact type"** (payload strict validation **"no backward compat"** flag **enabled** since Jun 24). Bearer token rotation **every 72 hours**, auth header checks logged to audit trail. Request timeout hardened to **45 seconds** (`curl --max-time 45`), retry window **30 min polling cycle** standard window (**"pull fresh data"** checkpoint 2026-06-23 baseline state, no cache fallback enabled).
- **Request payload schema**: Enforce strict `CREA v1.9` version (no v1.8 fallback tolerance). Query parameters **required**: `?crea_version=1.9&include_nested_categories=true&subset_discipline_codes=true` (**"prevent silent regressions"**).
- **Polling window**: **30-minute standard intervals**; strict timeout ceiling **600 seconds absolute** (`curl --max-time 600` hardcoded). Dalmonte rate-limit: **3 concurrent waves maximum**, throttle backoff **starts 2.5s**, exponential cap **120s max**.

## Manual validation & verification workflow)
- **Execution confirmation**: 2026-06-24 18:34 UTC (**Fargate container restart + manual seed operation**). Live query test sample: **20 Verdana API requests**, all resolved discipline + code field pairs within **45ms latency** (**SLA target ≤100ms met successfully**).
- **Unit test suite** (`test_crea_v19_nested.py`, **8 test cases**, timestamp 2026-06-25 02:11 UTC): **all tests pass** status. Nested category extraction coverage verified as **infinite** (∞) on edge-case XML nesting depth scenarios, schema violation detection.
- **Integration testing**: Staged test on quadrante-lab AWS account (**non-prod**). Campinet response validation (**baseline: DER metrics 0.12, WER 0.18**, GPU seconds per batch **2.8 seconds** on c5.2xlarge compute). Results cross-checked against **v0.19.3 release archive** (SHA256: `4f8c71e9d24e162eb8ccf33e2c543fa42bc3b9ce3a2d5c`, endianness-corrected).

## Results & verification summary from replay logs)
```bash
aws s3 cp s3://quadrante-prod/seedline-backlog/batch-20260624.json /tmp/batch.json && python3 /opt/seedline-replay/replay.py --source=/tmp/batch.json --target-index=campinet_disciplinari_v2 --wave-size=256 --timeout=45 --dry-run=false --log-level=debug 2>&1 | tee /var/log/replay_$(date +%s).log
aws logs tail /aws/lambda/seedline-bulk-indexer --follow --format short --since 2026-06-24T17:00:00Z
```
→ Execution time wall-clock **47 minutes 18 seconds** (backlog **8.2k documents**, API throttle retries **19 total** across **3 separate Dalmonte rate-limit windows**). Success rate **99.7%**, **24 documents** permanently failed with `field validation error` on disciplinari source field (timestamp confusion in legacy Campinet records, manually patched 2026-06-14 via direct S3 object edit).

## Follow-up actions & monitoring)
- **Schema v2.1 watch**: Schema version **2.1 support** backport patch deployed 2026-06-14 08:00 UTC; **Fitosfera rebrand prod ES run** (seedline Bitbucket PR #312) now safe to proceed without blocking further schema incidents. **Dalmonte SLA contract update**: require **+2 hours advance notice** for any major schema changes. **Integration test expansion**: added formal test coverage for AGRIS tag parsing edge cases; Campinet monitoring alert threshold tuned (error_rate ceiling increased **0.05 from prior 0.10** setting).
- **Related dependencies**: Seedline backlog cleared via `replay.py` automation tool; CREA schema mismatch issue fixed via conditional type-unwrapping in parser code (**XPath swap deployment**). Replay wave dispatch logic accounts for Dalmonte API throttling correctly; post-replay performance metrics confirm normalized query latency baseline + correct nested-object indexing behavior. Cross-dependency check with **Fitosfera rebuild branch (#312)** confirmed no further schema drift expected through **Jul 2026** per Dalmonte product roadmap commitment.

## Links
- [Agrisfera→Fitosfera rebrand — prod ES run IN PROGRESS + seedline MR 312 (Jul 2026)](note://5b49df86-0740-4b06-8e67-495573c0c690)
- [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md)
- [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md)
- [Seedline — Bollettini Fitosanitari Scraping Processor (Veneto + Piemonte): build + deploy + staging run](note://e20f5f21-a07f-421b-b544-8de06f16afe5)
- [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)
- [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md)

# See also

* [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md) - Ready 2026-07-06. (ask2sql-service v0.32.2, banca-suoli-prod account, schemas finalized, ventaglio.quadrante.io ops).
* [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md) - This document covers Seedline Prod and its core dependencies.
* [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md) - Tagging v0.21.3 (schema bumped +24 regulatory fields, ask2sql-service retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to 2.3s), deployment window Tue…
* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
* [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md) - Pushing Seedline's backend image to ECR from the M4 Pro requires Docker's buildx cross-compilation on the Mac (which emulates amd64). The build works, but QEMU overhead makes it…
