---
type: Note
id: 4621c04de050
title: Seedline — Scraping staging indexing run (deploy + EKS + curls + results)
description: Production staging deployment of Seedline aggregation pipeline v0.41.2
  on EKS cluster prod-eu-west-1a completed 47,341 total records across three data
  categories—bollettini…
---

# Seedline — Scraping staging indexing run (2026-06-08)

Production staging deployment of Seedline aggregation pipeline **v0.41.2 on EKS cluster** `prod-eu-west-1a` completed **47,341 total records** across three data categories—bollettini fitosanitari, regional production disciplines, and technical circulars—with 2 processor retries on the Lombardia disciplinari module and full validation via curl probes to OpenSearch cluster. Dalmonte feed delivered 31,844 bulletin records on time; schema divergence in AGRIS tags caused planned skipping of that segment for separate reindex on Jun 10.

## Infrastructure overview)
- **Cluster**: eu-west-1a availability zone, Quadrante AWS account production sandbox, **Kubernetes 9.2.1 upgraded → 9.3.0** (Amazon EKS managed nodes, Fargate capacity pool). Namespace `quadrante-seedline`, standard `kube-system` monitoring.
- **Scraper**: three parallel `seedline-scraper` application containers orchestrated **via EventBridge scheduled rules** at 03:45 UTC, pulling bulletin records from **Dalmonte data APIs** (Campinet feed, AgriNorme normative database, AGRIS regulatory tags on separate schedule).
- **Indexer**: single-pod `indexer-v0.41.2` stateful application, local OpenSearch 7.10.2 search engine with in-memory shard allocation, **40 worker threads, 131KB batch window**, streaming indexed JSON documents to S3 `seedline-staging-data` bucket with gzip compression.
- **Monitoring** stack at `prometheus-side.quadrante.io:9090` **forwards alerts to Slack + Grafana dashboards**, (CloudWatch container logs → Prometheus exporter via sidecar pattern). Pod lifecycle logs stored in **CloudWatch Logs**, queryable via `aws logs tail` CLI with filters.
- **Authentication**: IAM role `seedline-staging-role` with S3 object-level permissions `s3:GetObject` + `s3:PutObject` on staging bucket prefixes, ECR pull credentials provisioned via Kubernetes ServiceAccount binding, **cluster RBAC enforced**. 

## Deploy
1. **Prepare AWS CLI context** (`aws-cli` v2.13.x, docker 24.0.x):
```
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 419348292567.dkr.ecr.eu-west-1.amazonaws.com
export SEEDLINE_TAG="v0.41.2" && export NAMESPACE="quadrante-seedline"
kubectl config use-context arn:aws:eks:eu-west-1:419348292567:cluster/prod-staging-cluster
```
2. **Scale and reconfigure cluster memory** (⚠ first run hit Java heap OOM at 512MB, bumped to 1.2GB per pod):
```
kubectl set resources deployment indexer-v0.41 -n $NAMESPACE --limits=cpu=2000m,memory=1200Mi --requests=cpu=1500m,memory=900Mi
kubectl scale deployment seedline-scraper -n $NAMESPACE --replicas=3
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: indexer-config
  namespace: quadrante-seedline
data:
  batch_size: "131072"
  thread_pool: "40"
  flush_interval_ms: "5000"
EOF
```
3. **Apply infrastructure changes via Bitbucket PR** `seedline-infra` MR #487, **two-stage rollout**: scraper stateless pods first, then indexer application. **Rollout status verification:**
```
kubectl rollout status deployment/seedline-scraper -n $NAMESPACE --timeout=300s
kubectl rollout status deployment/indexer-v0.41 -n $NAMESPACE --timeout=180s
```
4. **Trigger indexing job** (sends Telegram message via `merlino-ops` webhook on completion). **Exact commands:**
```
# Invoke scraper via EventBridge direct API call
aws events put-events --entries '[{"Source":"seedline.cli","DetailType":"scrape.start","Detail":"{\"feed_id\":\"campinet\",\"start_date\":\"2026-06-01\",\"end_date\":\"2026-06-08\"}"}]' --region eu-west-1

# Tail scraper pod logs in real time (split pane)
kubectl logs -n $NAMESPACE deployment/seedline-scraper --tail=50 -f &
# And indexer logs (main pane)
kubectl logs -n $NAMESPACE deployment/indexer-v0.41 --tail=50 -f
```

## Verify)
```
# Health check indexer pod readiness (must return "ok")
kubectl exec -it -n $NAMESPACE $(kubectl get pods -n $NAMESPACE -l app=indexer-v0.41 -o jsonpath='{.items[0].metadata.name}') -- curl -s http://localhost:8081/health | jq '.status'

# Count indexed documents in OpenSearch (staging read replica)
kubectl port-forward -n $NAMESPACE svc/opensearch-staging 9200 &
curl -s "http://localhost:9200/seedline-bollettini-staging/_count" -H "Content-Type: application/json" -d '{"query":{"match_all":{}}}' | jq '.count'

# Check S3 staging bucket write progress (should ~2.1 GB after 3h wall time)
aws s3 ls s3://seedline-staging-data/indexing-runs/2026-06-08/ --recursive --summarize | tail -5
```

## Gotchas)
1. **Regional disciplines processor timeout** (⚠ occurs on first run with fresh data): `disciplinari-processor` microservice hit 180-second maximum wait time on the Emilia-Romagna + Lombardia region stitching step (step 3.2 in the deploy script). Root cause: Dalmonte regional API returns paginated results; the processor was serializing pages instead of streaming. Fixed by bumping internal `conn_pool=20` + `batch_timeout=240s` in ConfigMap. Both failed retries on retry loop recovered successfully, no data loss.
2. **ECR container image tag mismatch in Bitbucket PR** (typo in MR description): Bitbucket pipeline accidentally rolled out `v0.41.1` build instead of pinned `v0.41.2`. Error caught immediately at pod startup event ("image `v0.41.1` not found in ECR registry seedline:staging"). Corrected via hotfix MR `quadrante-seedline` #488, redeployed successfully.
3. **Java Virtual Machine heap memory spike during document merge phase** → keep close watch on Prometheus metric `jvm.memory.heap.used` via Grafana dashboard. If value approaches 950MB during the merge phase (typically 45–60 minutes into the run), disable concurrent batch flushes in the `indexer-config` ConfigMap to reduce GC pressure. Failure mode: pod OOMKilled, entire batch re-queued.
4. **Dalmonte Campinet feed ingestion lag exceeded expectations**: feed was 6 hours behind the expected 2–3 hour lag window. This pushed against the UTC cutover window for next day's run. Remediation: added 4-hour buffer to the `start_date` query parameter. Next run: changed to ingest from yesterday's 18:00 UTC, avoiding collision.
5. **OpenSearch shard replica refresh latency** (read consistency tradeoff): `_refresh=true` on bulk writes is slow (5–8 seconds per 200-document batch). During heavy indexing, disabled replica shard reads to reduce network overhead. Remember to **re-enable post-run via cluster `_settings` API** or indexer will leak old docs to search.
6. **S3 object key collision from previous run**: re-using the old staging prefix from the Jun 3 test caused partial document overwrites in the bucket (old docs with same keys got clobbered). Solution: now using ISO8601 timestamp + random UUID in S3 path (**2026-06-08-7a2e91fc**, never reuse prefixes across runs).

## Results)
- **Documents ingested**: 47,341 total records across all feeds; breakdown: bollettini fitosanitari=31,844 (67.2% of total), disciplinari regionali=12,107 (25.6%), circolari tecniche=3,390 (7.2%). Zero duplicate key collisions.
- **Errors and recovery**: 2 regional processor service retries (both succeeded on second attempt), 47 XML parse errors on malformed bulletin chunks from Dalmonte (logged but not re-attempted), zero OpenSearch indexing write failures, zero data corruption events.
- **Throughput profile**: first 30,000 documents indexed in 1 hour 12 minutes (mean 130 docs/second throughput). Remaining 17,341 documents trickled through over 1 hour 48 minutes (slowing due to thread pool saturation at 40 workers). Async flush window tuning needed for future runs.
- **Storage footprint and retention**: OpenSearch live shard on EKS = 1.9 GB allocated size; compressed backup to S3 using gzip -9 compression = 847 MB storage footprint. Retention policy configured to keep 7 staging run snapshots automatically; older snapshots purged by cron job **every Sunday 04:00 UTC**.
- **AGRIS regulatory tag indexing deferred**: skipped this segment for this run due to upstream schema change—Dalmonte API modified field names on June 5 (changed `codice_agris` → `agris_code_v2`). Separate reindex run planned for **June 10 with schema adapter**, will not block main indexing.
- **Pod cold-start latency analysis**: EKS Fargate cluster took mean 47 seconds for new pod startup (ECR image pull, init container health probes, OpenSearch client bootstrap). No issues, well within SLA targets.

## Links
- [Seedline — bollettini duplicati: PDF scansionati che saturano la coda OCR (17 set 2026)](seedline-bollettini-duplicati-pdf-scansionati-che-saturano.md)
- [Seedline — Fix QA scraping bollettini fitosanitari (branch hotfix/bulletin-qa, 2026-08-18)](seedline-fix-qa-scraping-bollettini-fitosanitari-branch.md)
- [Agrisfera→Fitosfera rebrand — prod ES run IN PROGRESS + seedline MR 312 (Jul 2026)](note://5b49df86-0740-4b06-8e67-495573c0c690)
- [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md)
- [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md)
- [Seedline — Bollettini Fitosanitari Scraping Processor (Veneto + Piemonte): build + deploy + staging run](note://e20f5f21-a07f-421b-b544-8de06f16afe5)
- [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md)
- [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)
- [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md)

# See also

* [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](cf-dalmonte-banca-suoli-go-live-readiness-analysis-deploy.md) - Ready 2026-07-06. (ask2sql-service v0.32.2, banca-suoli-prod account, schemas finalized, ventaglio.quadrante.io ops).
* [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md) - This document covers Seedline Prod and its core dependencies.
* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
* [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](seedline-circolari-tecniche-scraping-processor-built-tested.md) - Build complete via quadrante-seedline/circolari-ingester parsing 3,847 technical circulars from Dalmonte source. Local test suite passes 98.1% without deploy-blocking issues).
* [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md) - Tagging v0.21.3 (schema bumped +24 regulatory fields, ask2sql-service retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to 2.3s), deployment window Tue…
* [Seedline — Fix QA scraping bollettini fitosanitari (branch hotfix/bulletin-qa, 2026-08-18)](seedline-fix-qa-scraping-bollettini-fitosanitari-branch.md) - Data: 18 agosto 2026
* [Seedline — bollettini duplicati: PDF scansionati che saturano la coda OCR (17 set 2026)](seedline-bollettini-duplicati-pdf-scansionati-che-saturano.md) - Seedline ingestion bottleneck, 17 settembre 2026 (pipeline OCR, staging).
* [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md) - Pushing Seedline's backend image to ECR from the M4 Pro requires Docker's buildx cross-compilation on the Mac (which emulates amd64). The build works, but QEMU overhead makes it…
