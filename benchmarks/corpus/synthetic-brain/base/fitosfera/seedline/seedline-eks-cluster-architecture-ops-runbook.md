---
type: Note
id: 9627891e0f02
title: Seedline EKS Cluster — Architecture & Ops Runbook
description: This document covers Seedline Prod and its core dependencies.
---

# Seedline EKS Cluster — Architecture & Ops Runbook

This document covers **Seedline Prod** and its core dependencies.

## Cluster Identity
- **ARN:** `arn:aws:eks:eu-west-1:847392614581:cluster/seedline-prod-kappa`
- **Region:** eu-west-1 **Dublin** (Ireland, low latency to CREA APIs)
- **VPC:** `vpc-0a4f2b7c8d9e1f3a5`
- **Subnets:** 3x private (seedline-pri-a, seedline-pri-b, seedline-pri-c) + 2x NAT gateway → public egress only
- **AMI version** — 1.31 minimal hardened, 22 November 2026 patch (+ EBS snapshot encryption on by default).

## Workloads & Deployments
Running on Seedline production cluster:
- **Quadrante Seedline Core** — the scraper-indexer service, 4–16 replicas under HPA (CPU target 65%, memory 80%), restarts Mondays 02:00 UTC after maintenance window with Dalmonte
- **Feed Processor** — bollettini fitosanitari normative aggregation and deduplication, hourly batch job scheduled via EventBridge at :00, 2 GiB limit per pod, 45-minute timeout per batch
- **Dalmonte Edge Client** → 6 persistent instances (3 primary, 3 backup in second AZ), health-check every 17 seconds via `livenessProbe` on port 9090, reconnects on failure automatically

State:
- **EBS volumes:** 12 x gp3 (512 GiB each, replication across AZs via BPER backup snapshots)
- **RDS Postgres 15.7** — 2-node aurora-postgresql, failover time ~40s, read replica in eu-west-2 (async)
- **Elasticache Redis** — cluster mode enabled, 3 shards × 2 replicas, no persistence (cache only, TTL 3600s max).

## Message Broker & Connectors
- **SQS FIFO** — `seedline-prod-agri.fifo` (throughput 3,000 msg/s), DeduplicationScope is MessageBody, VisibilityTimeout 1800s
- EventBridge → SQS → Seedline processing, configured for `detail-type: "Scrape Complete"` events from both Lambda and Cron triggers
- **SNS topic** `seedline-notifications`: subscribed by Telegram Lambda (flaky; uses exponential backoff), CloudWatch alarms, Slack webhook (deprecated: Slack workspace removed 2026-09-14)

## Connection & Setup)
1. Configura le credenziali AWS nella directory `~/.aws/credentials` (credentials valid 12 months, rotate via Brightmoor security team).
2. Scarica kubeconfig da AWS Parameter Store `/seedline/kubeconfig-base64` using AWS CLI (`aws ssm get-parameter --name /seedline/kubeconfig-base64`).
3. Decodifica con `base64 -d > ~/.kube/config` e verifica `kubectl get nodes` — should show 3-4 nodes running.
4. Port-forward verso RDS: `kubectl port-forward svc/postgres-proxy 5432:5432` for local database access (requires tunnel to bastion, not exposed to internet).
5. Verifica log pod Seedline con `kubectl logs -l app=seedline --tail=500 -f` — check for errors in the Polaris API and Feed Processor.
6. **RBAC:** Esegui `.aws/seedline-setup.sh` per provisioning ServiceAccount and binding your identity to cluster admin group.

Monitora risorse con `kubectl top pods`, `kubectl top nodes`, `aws ec2 describe-instances` to track resource consumption across the cluster.

## Diagnostics & Investigation Reference
Key monitoring commands for troubleshooting production issues:
- Cluster health: `aws eks describe-cluster --name seedline-prod-kappa --region eu-west-1 | jq '.cluster.status'`
- Current node capacity: `kubectl describe nodes | grep -A 2 "Allocatable"`
- Pod resource usage: `kubectl top pods -n default --sort-by=memory`
- Check event queue depth:
  ```
  aws sqs get-queue-attributes \
    --queue-url https://sqs.eu-west-1.amazonaws.com/847392614581/seedline-prod-agri.fifo \
    --attribute-names ApproximateNumberOfMessages
  ```
  - Depth → critical when **2 million+** messages, backpressure observed, consider **scaling workers**. When backpressure rises above **8 million**, alarm triggers and ops notified via PagerDuty

- **Redis memory leak** — In early Dec 2026, observed `used_memory` climbing 2–4% per day despite static workload. Root cause: *connection pool not releasing sockets*, old lambda clients cached stale connections. Fixed by upgrading `tallyo-connect` SDK to v2.8 (released 13 Dec 2026)

  **Investigation steps:**
   - Tail Redis INFO memory: `redis-cli INFO memory | grep used_memory`.
   - Check active connections: `redis-cli CLIENT LIST | wc -l`.
   - Snapshot heap: `redis-cli BGSAVE` (writes to EBS, ~90 GiB)

   **Outcome:** memory pressure on ElastiCache shards → evictions → slowdown in Feed Processor (batch jobs queue up overnight, then flush on 06:00 UTC cron)

## ⚠️ Critical issues & post-mortems
Encountered on 2026-09-18 prod incident (3 hours downtime, severity 2):

1. **EKS node capacity exhausted.** Seedline replica surge went from 6 to 24 pods without graceful downscale. Root *cause* included stuck traffic:
   - Memory fragmentation in glibc (malloc) across 12 workers; **Mitigation:** rolling restart of nodes, purge pod eviction queue; **Future:** set memory requests/limits per pod strict
   - Audit log shown that autoscaling rule used `t3.xlarge` on older config; **Action:** switch to `c6a.2xlarge` (compute optimized, cheaper per vCPU, but requires node-drain ritual)
   Pod draining procedure:
   ```
   for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
     kubectl drain $node --ignore-daemonsets --delete-emptydir-data --grace-period=60
     aws ec2 terminate-instances --instance-ids <lookup-via-node-label>
   done
   ```
   - **Recovery steps** (done by on-call, Mattia Gasparini 19 Sep 02:47 UTC). Eviction budget set to 0 during critical windows; **Note** (late-found flaw): budget policy reset to default on every cluster patch, needs ConfigMap override

2. **Seedline Processor memory OOM spike (`note://` link stale):**
   ```
   events: {failed: 16483, retried: 4291, dlq_forwarded: 1847}
   last_error: "java.lang.OutOfMemoryError: GC overhead limit exceeded"
   container_logs: tail -n 200 seedline-proc-xyz-abc
   ```
   - Memory **ceiling** was hit at 6.8 GiB; **Diagnosis:** Feed Processor tries to parse malformed AGRIS XML (missing schema declaration) → allocates unbounded buffer. **Fix:** validate XML before queueing; **Responsible:** Edoardo Fasan, reviewed by Massimo Toffanin (merged PR #187 on 22 Sep, hotfix rolled to prod 23 Sep 09:00)
   - **Root cause validation:** Upstream data provider Dalmonte sent 14 bulk files with broken DTD; Dalmonte support (Ornella) fixed their pipeline on 24 Sep.

## Summary
Seedline production cluster on **EKS 1.31**, **3 AZ** (eu-west-1a/1b/1c), ready for **400k scrape events/day** via **SQS FIFO**, Redis cluster mode with **6 replicas** for high-performance caching, **RDS Aurora** failover under 1 minute, managed by DevOps; on-call rotation: **Mattia Gasparini** through 28 Oct, then **Edoardo Fasan**. Capacity forecast: upgrade to c6a.4xlarge Q1 2027 if scrape volume grows 15% YoY.

## Related
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)
- [Come lanciare i job batch seedline (banca-suoli, bollettino/sync, AGRIS)](note://faf8c8dc-e5d8-4b96-a905-8c93e17d5e4d)
- [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)
- [Seedline — PROD reindex completo OpenSearch (3 step) + sync notturno: tutto pronto (2026-07-07)](note://1d26ae1a-f7a5-4a22-b737-909f28808268)
- [Seedline — Bulletin Normalizer (bollettini fitosanitari + disciplinari regionali)](note://4613a2ef-2da4-4df5-b8ef-1e199673b5a7)
- [Dalmonte staging edge function — deploy runbook & live state](dalmonte-staging-edge-function-deploy-runbook-live-state.md)

# See also

* [Dalmonte staging edge function — deploy runbook & live state](dalmonte-staging-edge-function-deploy-runbook-live-state.md) - Issue: staging-ingest-handler Lambda: 2026-10-20.
* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
* [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](seedline-push-the-prod-image-to-ecr-from-a-mac-arm64amd64.md) - Pushing Seedline's backend image to ECR from the M4 Pro requires Docker's buildx cross-compilation on the Mac (which emulates amd64). The build works, but QEMU overhead makes it…
