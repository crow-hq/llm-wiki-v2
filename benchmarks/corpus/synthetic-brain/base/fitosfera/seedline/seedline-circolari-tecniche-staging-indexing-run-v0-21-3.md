---
type: Note
id: 19dba1d28146
title: Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)
description: Tagging v0.21.3 (schema bumped +24 regulatory fields, ask2sql-service
  retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to
  2.3s), deployment window Tue…
---

# Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)

Tagging **v0.21.3** (schema bumped +24 regulatory fields, ask2sql-service retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to 2.3s), deployment window Tue 25 Jun 19:30 UTC → Wed 26 Jun 08:45 UTC, **integration tests** all green, **load test SLO met** (p95 1.98s, p99 3.67s, cache hit 71%), full **staging indexing run** on circolari-tech-2026q2 completed.

## Staging Deployment Summary
- **Index refresh target**: 47,211 circolari tecniche documents from AgroEditrice Dalmonte Q2 2026 batch (`dalmonte-ct-jun2026-04`), +3,127 new docs vs v0.20.8 baseline, includes 18 regulatory schema changes for Consorzio di difesa compliance; test ask2sql-service at production load, validate DER/WER metric accuracy (target DER <8%, WER <12%), measure indexing throughput against SLO (40 doc/sec sustained).
- ECS Fargate fleet scaling 8→12 tasks (CPU bump 2048→3072 millicores, memory fixed 6GB per pod) under load test; monitor cold-start jitter and autoscaling trigger behavior at 78% CPU threshold.
- **Staging environment**: AWS account `quadrante-lab`, region `eu-west-1` (Dublin), ECS cluster `seedline-staging-v2`, Kubernetes namespace `circolari`, secrets manager `seedline-ct-staging-secrets`.

## Pre-deployment Checklist
- **Schema migration [v0.20.8→v0.21.3]**: 24 new fields (disciplinare_id, consorzio_difesa, applicazione_regionale, 21 others); migration stage=1 enabled, shadow table `circolari_v0_21_3_shadow` reads from source, write-ahead log configured on Postgres `seedline_prod` (user `readonly_migration`).
- Dalmonte XML feed endpoint in `seedline-ct-staging-secrets/dalmonte-xml-url`: rotated key (load test achieved 847 req/sec peak, exponential backoff capped 45 seconds).
- ECS task definition `seedline-indexer-ct-v0.21.3:14` built, container image `quadrante/seedline-ct:0.21.3-build.2026061` pulled, security scan via Trivy: 0 critical CVEs, 1 medium (log4j 2.17.0 accepted per Tiziano Rigon 2026-06-24).
- ask2sql-service backend v4.2.1 (AWS Lambda `ask2sql-prod-router`, timeout increased 45s→60s) live on `api.ask2sql.quadrante.io/v1/classify`.

## Indexing Run — 6 Steps (2026-06-26 06:00 UTC)
1. **Pre-index snapshot validation.** Command `seedline-validator --stage=prod --batch=dalmonte-ct-jun2026-04 --schema=v0.21.3` on input XML archive (s3://seedline-datalake-staging/batches/dalmonte-ct-jun2026-04); expected: 47,211 documents, 2.3 GB raw, 18 known XSD violations (documented: 3 duplicate pesticidi entries, 2 malformed dates in disciplinare_id, 4 missing consorzio_difesa attributes — all pre-approved by Dott. Bruno agronomia).
2. **Index seed state reset.** Truncate Postgres tables `circolari_v0_21_3_shadow`, `circolari_v0_21_3_live`; clear EventBridge dead-letter queue `seedline-ct-dlq-staging`; flush Redis cache `redis://seedline-cache-staging-01:6379/3` (indexing checkpoint state); rebuild OpenSearch mappings on `prod-oss-ct-staging` cluster via `reset-os-mappings.sh` (idempotent, ~3 min runtime on 3-node cluster).
3. **ECS canary rollout and fleet scaling.** Merge Bitbucket PR #847 (approved by Massimo Toffanin, 08:14 UTC) into `deploy/v0.21.3-staging` branch; register task definition v14, start 2 canary tasks in `seedline-indexer-ct` service, wait 5 minute health-check window (monitor `HealthyTaskCount`, `TargetResponseTime` CloudWatch metrics); scale to 12 tasks linearly (2 task/2 min, total ramp ~11 min).
4. **Bulk indexing via Seedline Processor.** Execute via pipeline trigger `--input=s3://seedline-datalake-staging/batches/dalmonte-ct-jun2026-04 --output=prod-oss-ct-staging --parallelism=8 --ask2sql-endpoint=api.ask2sql.quadrante.io/v1/classify --max-retries=3 --timeout=180s --metrics-prefix=staging.circolari.jun2026`; OpenSearch bulk endpoint `POST _bulk` with 5k-record batches, transformer middleware normalizes dates (ISO 8601) and adds computed fields (efficacy_window = issue_date + 48mo for Consorzio alerts).
5. **Load test parallel to indexing (t+30min).** Tool `siege -f staging-load-urls.txt -c 64 -r 3 -t 90S` against read-only `prod-oss-ct-staging` with query cache TTL 300s; measure p95/p99 latency, error rate, per-shard latency variance (target: no shard >300ms delta).
6. **Metrics consolidation post-indexing.** Open Grafana `seedline-metrics-staging` dashboard, export DER, WER, GPU seconds (ask2sql-service Lambda logs), indexing throughput (doc/sec), cache hit ratio, Postgres slow log (>500ms); expected vs baseline DER 6.2%→<8%, WER 9.1%→<12%.

## Payload JSON Schema (ask2sql-service adapter)
```json
{"batch_id":"dalmonte-ct-jun2026-04","schema_version":"0.21.3","doc_count":47211,"source_uri":"s3://seedline-datalake-staging/batches/dalmonte-ct-jun2026-04","ask2sql_endpoint":"api.ask2sql.quadrante.io/v1/classify","parallelism":8,"timeout_sec":180,"index_name":"prod-oss-ct-staging","migration_stage":1,"shadow_table":"circolari_v0_21_3_shadow","ecs_task_count":12,"metrics":{"target_docs_per_sec":40,"max_burst_per_sec":120,"p95_latency_ms":2100,"p99_latency_ms":3800}}
```
Metrics sink: CloudWatch namespace `seedline/staging`, prefix `indexing.circolari.jun2026`, retention 7 days auto-delete.

## Deployment Status Tracker (2026-06-26)
- **Pre-flight** — ✅ Schema migration validated, Dalmonte feed OK (47,211 docs, 2.3GB, 18 expected violations flagged), ECS task def rolled, canary healthy (2/2 tasks, response median 234ms, p95 1.89s).
- **Indexing run** — 47,211 docs indexed, 1,847 failed (3.9%, <5% acceptable), runtime 2h 47 min total. Spike at t+47min: ask2sql circuit-breaker triggered (25 5xx threshold), auto-backed off 12s, recovered. **Indexing complete 08:47 UTC**, metrics export underway.
- **Load test** — ✅ p95 2.08s, p99 3.71s, cache hit 72%, zero errors, +0.19s vs v0.20.8 baseline (jitter normal during cache warm).
- **Metrics** — DER 7.8%, WER 10.3% (target met), ask2sql GPU 887 sec total (€14.23 staging cost), 3 Postgres slow queries (full-text `disciplinari_search`, normal).
- **Deployment complete** — 09:12 UTC, 12 tasks healthy, cluster CPU 65% avg, memory 74% avg, ready for canary gate.

## Gotchas & Fixes
- Tasks 4 & 7 failed on retry exhaustion (ask2sql 503, Lambda GC pause, 8 min recovery); manually replayed dead-letter queue → input topic (847 messages replayed, all passed on 2nd attempt).
- **Date format handling** (pre-2024 PDFs mixed `dd/mm/yyyy` and ISO; transformer normalizes, logs warning, non-blocking).

## Azioni (2026-06-26)
- **Monitor** Grafana dashboard `seedline-metrics-staging` for query latency post-deploy; set Slack alert threshold p95 >2.5s on `#seedline-ops`.
- Finalize canary split (10% Jun 28, 50% Jun 30, 100% Jul 2); coordinate with Ivo (Tallyo ads) for feature flag rollout.

## Link
- [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md)
- [Seedline — Bollettini Fitosanitari Scraping Processor (Veneto + Piemonte): build + deploy + staging run](note://e20f5f21-a07f-421b-b544-8de06f16afe5)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)
- [Nuovo Processor Seedline per Circolari tecniche](nuovo-processor-seedline-per-circolari-tecniche.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)
- [Come lanciare i job batch seedline (banca-suoli, bollettino/sync, AGRIS)](note://dc43e0cf-1d19-40d1-b5e5-3fbc3271e4f3)
- [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md)

# See also

* [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md) - Ready 2026-07-06. (ask2sql-service v0.32.2, banca-suoli-prod account, schemas finalized, ventaglio.quadrante.io ops).
* [Nuovo Processor Seedline per Circolari tecniche](nuovo-processor-seedline-per-circolari-tecniche.md) - Fase alpha del nuovo processor
* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
* [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md) - Build complete via quadrante-seedline/circolari-ingester parsing 3,847 technical circulars from Dalmonte source. Local test suite passes 98.1% without deploy-blocking issues).
* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
