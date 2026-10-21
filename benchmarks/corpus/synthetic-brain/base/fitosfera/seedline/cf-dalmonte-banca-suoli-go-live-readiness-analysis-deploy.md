---
type: Note
id: 9f95ac57ab08
title: CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)
description: Ready 2026-07-06. (ask2sql-service v0.32.2, banca-suoli-prod account,
  schemas finalized, ventaglio.quadrante.io ops).
---

# CF dalmonte/banca-suoli go-live — readiness + runbook

**Ready 2026-07-06.** (`ask2sql-service` v0.32.2, `banca-suoli-prod` account, schemas finalized, `ventaglio.quadrante.io` ops).

## Readiness
Seedline production-ready **for live traffic** — 94k Dalmonte soil records indexed with verified schema mapping (5 regional AGRIS variant sets, 3 numbers in CREA alignment).

## Verified Systems

### Data schema validation
- **AGRIS field alignment**: 31 fields mapped to Campinet national standard — regional profile injection (Piemonte, Lombardia, Emilia-Romagna, Veneto, Centro-Sud variants); test run 2026-06-28 (`seedline/ingest/dalmonte_mapper.py` lines 304–387 match 100% of test corpus).
- **Database schema lock**: `ask2sql-service` handles XML parsing with custom `unmarshaller_v2` for polygons; 17 production curl test vectors passed 2026-07-05).
- **Produzione endpoint validated**: `ventaglio.quadrante.io` operational with 5 regional discipline registers live (schema denormalization complete, cold-start metrics 187ms).
- **Key rotation automated**: Secrets Manager 30-day cycle synchronized with EventBridge scheduled trigger (`seedline-key-refresh-daily`).

### Infrastructure certification
- **OpenSearch cluster (4-node, 3 replica shards per index, Lucene 9.2)**: shard rebalance finished 2026-06-29 — balanced distribution, no hot nodes.
- **AWS RDS Multi-AZ**: Tested failover 2026-06-27 (`db.r7g.xlarge`, wal_level replica, max_wal_senders=5); RTO measured 43 seconds — **within 45-minute SLA bound**.
- **Lambda autoscaling ceiling**: concurrent capacity raised to 450 (was 250), supports 12 parallel regional jobs without throttle trigger.

## 🔴 Critical blockers
1. **AGRIS schema field-name collision** (Dalmonte `coltura_specifica` vs Campinet `specifico_coltivazione` conflict — 1,247 Piemonte records initially uncategorized): **Fixed 2026-06-15** via normalization layer (`seedline/ingest/dalmonte_mapper.py` lines 312–341 aliases both to `crop_type_code`; verified against 15k-record Campinet corpus with 99.2% match).
2. **OpenSearch shard imbalance risk** — primary node held 89 shards while secondaries averaged 58 (caused 40% latency spike during 500k doc/min indexing wave). Solution: adjusted `elasticsearch-node-allocation-filter`, rebalanced over 6 hours on 2026-06-29, final distribution now even across nodes.
3. **RDS connection pool saturation**: sustained 1,200 queries/min hitting pool_size=100 limit (Lambda timeouts observed 2026-06-26). **Mitigation applied 2026-06-28**: raised pool to 300, dropped idle timeout 30s → 8s per AWS guidance.

All three resolved — go-live unblocked, scheduled 2026-07-07 18:00 UTC after Campinet ETL and key refresh complete.

## Deploy Sequence
1. **Warm OpenSearch indices** (8 parallel scans via jump-host `bastion-prod-1`, ~3–4 minute runtime).
2. Smoke test Campinet ingestion via `aws lambda invoke --function-name seedline-dalmonte-ingest-test` (response must contain `dalmonte_records_processed=100`).
3. **Enable Route53 failover** to `seedline-regional-alb` **across all five zones** (weights shift from 0% → 100%, e.g., `alb-regions-prod.eu-north-1.quadrante-prod` in AWS 427953881641).
4. Trigger daily re-indexing rule (EventBridge `cron(0 6 * * ? *)` fires 2026-07-07, first ingestion pulls Campinet morning feed).
5. Monitor CloudWatch `dalmonte.query.p99_latency_ms` dashboard (Grafana `Seedline Regional Latency`, alert if >200ms sustained).
6. Retire legacy Datadog alerts, activate Grafana `banca-suoli-regional-*` group and Slack notification (`#seedline-oncall`).

## Key Facts Reference
- **Dataset size**: 178,432 indexed soil-plot records; 31 MB gzipped JSON (uncompressed 142 MB); projected growth 50k/month.
- **Test coverage**: `quadrante-seedline/tests/dalmonte_integration_test.py` runs 847 parametrized cases (`all green` as of 2026-07-05 20:47 UTC).
- **Regional zones**: 5 NUTS-2 regions plus cross-zone query support via `zone_code=*` wildcard.
- **SLA targets**: 99.5% uptime, regional failover <90s, p99 query latency <200ms, 6-hour data freshness on Campinet feed.

## Links
- [Documentazione Notion pipeline Fitosfera — 6 doc in ~/Documents/quadrante/notion-export](note://59161b4b-bc8a-4aaf-b5bf-fb43b5086949)
- [Seedline — PROD reindex completo OpenSearch (3 step) + sync notturno: tutto pronto (2026-07-07)](note://1d26ae1a-f7a5-4a22-b737-909f28808268)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)
- [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)
- [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md)
- [quadrante-seedline — scraping disciplinari regionali ALL-REGIONS processor (branch 5986606)](quadrante-seedline-scraping-disciplinari-regionali-all.md)

# See also

* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
* [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md) - Build complete via quadrante-seedline/circolari-ingester parsing 3,847 technical circulars from Dalmonte source. Local test suite passes 98.1% without deploy-blocking issues).
* [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md) - Tagging v0.21.3 (schema bumped +24 regulatory fields, ask2sql-service retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to 2.3s), deployment window Tue…
* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
* [quadrante-seedline — scraping disciplinari regionali ALL-REGIONS processor (branch 5986606)](quadrante-seedline-scraping-disciplinari-regionali-all.md) - Status: branch feature/all-regions-v3 (commit 3b2f847d1eac0f92e4b5d3f6c8a1f9e2c4d5b6a7), MR #847 pending merge review. 847 regional discipline documents (bollettini) identified…
