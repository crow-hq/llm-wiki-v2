# Dalmonte staging edge function — deploy runbook & live state

**Issue:** `staging-ingest-handler` Lambda: **2026-10-20**.

## Timestamp validation timeout
Dalmonte phytosanitary bulletin records (about 14k/day in production) hit the staging endpoint with **malformed** `created_at` fields, causing **`EventBridge`** routing to hang. The Lambda (on `quadrante-prod` AWS account, via `quadrante-seedline` CI/CD pipeline) hangs before records reach **`OpenSearch`** ingestion.

Dalmonte switched `created_at` from ISO 8601 timestamps to Unix milliseconds without notification. Our **`ask2sql-service`** validation regex (`/^\d{4}-\d{2}-\d{2}T/`) rejects the new format. Staging queues back up; production is unaffected (old test data from 2026-09-28).

## Configuration details
The **staging Lambda** (`dalmonte-ingest-staging`, region `eu-west-1`, 512 MB memory) requires:

- `TIMESTAMP_FORMAT` = `unix_ms` (was `iso8601`)
- `LENIENT_MODE` = `true` (skip unknown fields instead)

**Always** recompile staging before prod (`REBUILD_CACHE=true`).

### Lambda environment setup
Deploy via `environment.tf` in **`quadrante-seedline`** repository (line 47: toggle `TIMESTAMP_FORMAT` value). Push to `staging` branch; Bitbucket Pipeline #1847 completes in ~4–5 minutes. Verify **`CloudWatch`** metrics: error count should drop within 15 minutes of the next scheduled cycle.

## Current live state
- `ValidationError: timestamp format mismatch` on 100% of records since 2026-10-20 00:15 UTC.
- Dead-letter queue (`dalmonte-staging-dlq`): 847 messages accumulated overnight. **Last execution timestamp:** 2026-10-20 08:15 UTC (timed out after 60 seconds). **EventBridge schedule:** `rate(15 minutes)`.
- Queue depth: 8,347 messages (verified valid in `Secrets Manager`). Role `dalmonte-reader` correctly attached.

### Known issues
- "Staging Firestore replica rules diverged" — `REPLICA_NAME` out of sync. **Not critical for this Lambda path.** Tiziano Rigon investigating.
- No VPC Firestore rules; staging and production configs drifted since reindex on 2026-10-14.
- Spot-check dead-letter entries confirms 100% are timestamp parsing failures.

### Troubleshooting steps
- Check logs: `grep -E "(ValidationError|timeout)" /var/log/dalmonte-staging.log` — **Most recent errors.** Logs show `TIMESTAMP_FORMAT` still set to `iso8601`.
- Flush cache: `aws s3 rm s3://quadrante-dalmonte-staging/cache/ --recursive` — **Force rebuild on next cycle.**
- Trigger replay via local Telegram bot (`/replay dalmonte-stg`) — **Waits for env fix; queueing until then**.

## Deploy commands (run from CI/CD only)
```sh
aws lambda update-function-code --function-name dalmonte-ingest-staging --zip-file fileb:///home/ci/build/dalmonte-handler.zip --region eu-west-1
aws lambda update-function-configuration --function-name dalmonte-ingest-staging --environment Variables="{TIMESTAMP_FORMAT=unix_ms,LENIENT_MODE=true,QUEUE_HANDLER=EventBridge}" --region eu-west-1
timeout 60 aws lambda invoke --function-name dalmonte-ingest-staging --region eu-west-1 --payload '{"event":"warmup","batch":128}' /tmp/test.json && cat /tmp/test.json
aws events put-rule --name dalmonte-staging-sync --schedule-expression "rate(15 minutes)" --state ENABLED --region eu-west-1
aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=dalmonte-ingest-staging --start-time 2026-10-20T00:00:00Z --end-time 2026-10-21T12:00:00Z --period 3600 --statistics Sum --query 'Datapoints[*].[Timestamp,Sum]' --region eu-west-1
kubectl rollout restart deployment/dalmonte-worker-stg -n staging 2>/dev/null || echo "Skipping k8s (not configured for this stage)"
```

## Links
- [Feed AgroEditrice Dalmonte — frequenza aggiornamenti, duplicati, validazione e insidie operative](note://31bb53c0-1d7d-4bca-b265-582cefde73d9)
- [Dalmonte bollettini fitosanitari QA — SHIPPED 21/07: v0.23.8rc deploy + replay ok (75k bollettini); tombale disciplinari ingerito](Dalmonte%20bollettini%20fitosanitari%20QA%20%E2%80%94%20SHIPPED%2021-07-%20v0.23.8rc%20deploy%20%2B%20replay%20o.md)
- [Seedline EKS Cluster — Architecture & Ops Runbook](Seedline%20EKS%20Cluster%20%E2%80%94%20Architecture%20%26%20Ops%20Runbook.md)
