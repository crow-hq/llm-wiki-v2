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
- [Seedline — bollettini duplicati: PDF scansionati che saturano la coda OCR (17 set 2026)](Seedline%20%E2%80%94%20bollettini%20duplicati-%20PDF%20scansionati%20che%20saturano%20la%20coda%20OCR%20%2817%20se.md)
- [Seedline — Fix QA scraping bollettini fitosanitari (branch hotfix/bulletin-qa, 2026-08-18)](Seedline%20%E2%80%94%20Fix%20QA%20scraping%20bollettini%20fitosanitari%20%28branch%20hotfix-bulletin-qa%2C%202.md)
- [Agrisfera→Fitosfera rebrand — prod ES run IN PROGRESS + seedline MR 312 (Jul 2026)](note://5b49df86-0740-4b06-8e67-495573c0c690)
- [Seedline EKS Cluster — Architecture & Ops Runbook](Seedline%20EKS%20Cluster%20%E2%80%94%20Architecture%20%26%20Ops%20Runbook.md)
- [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](CF%20dalmonte-banca-suoli%20go-live%20%E2%80%94%20readiness%20analysis%20%2B%20deploy%20runbook%20%282026-07-0.md)
- [Seedline — Bollettini Fitosanitari Scraping Processor (Veneto + Piemonte): build + deploy + staging run](note://e20f5f21-a07f-421b-b544-8de06f16afe5)
- [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](Seedline%20%E2%80%94%20Circolari%20tecniche%20staging%20indexing%20run%20%2B%20v0.21.3%20deploy%20%282026-06-26%29.md)
- [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](Seedline%20%E2%80%94%20push%20the%20prod%20image%20to%20ECR%20from%20a%20Mac%20%28arm64%E2%86%92amd64%29%20%2B%20layer%20cache%20pru.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](Seedline%20%E2%80%94%20Campinet%20daily-update%20flow%20%2B%20CREA%20schema%20drift%20fix%20%26%20replay%20%28Jun%202026.md)
- [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](Seedline%20%E2%80%94%20Circolari%20tecniche%20Scraping%20Processor%20%28built%20%2B%20tested%20locally%29.md)
