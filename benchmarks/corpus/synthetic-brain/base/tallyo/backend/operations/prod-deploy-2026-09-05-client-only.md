---
type: Note
id: bf5cb0c564c1
title: PROD DEPLOY 2026-09-05 (client-only)
description: Shipped tallyo-backend v7.3.2-hotfix to Firebase tallyo-prod-7c41e on
  2026-09-05 09:15 UTC with critical auth migration and OCR fixes.
---

# PROD DEPLOY 2026-09-05 (client-only)

Shipped `tallyo-backend` v7.3.2-hotfix to Firebase `tallyo-prod-7c41e` on 2026-09-05 09:15 UTC with critical auth migration and OCR fixes.

## Shipped
- Firebase Auth migration **v8 → v9** (`firebase-admin` 11.11.0, affects ~2400 active users). Custom claims forwarding via CloudFunction `syncIdTokens` (27ms overhead, cached 4h).
- Receipt OCR timeout resolution (was 8s, now 12s with backoff to Mistral Vision Small via `ask2sql-service`).
- Category inference retraining on Q3 ledger (`tallyo-connect` npm v3.1.4 pushes embeddings to `mcp.gettallyo.com/ml/ingest`).
- Adapty webhook retry logic (exponential backoff, max 5 attempts over 2.5h).
- Firebase quota alert tuning (12GB read quota, down from 18GB after indexing cleanup).

## Deploy workarounds
Mistral `mistral-small-4.1` API rate-limits hit during peak hours. Mitigation: ECS Fargate tasks spawn sidecar Lambda on EventBridge schedule (every 2min) to drain `tallyo-ml-queue` with jitter (+/- 15s). Token budget reset at 04:00 UTC daily (`aws events put-rule --schedule-expression "cron(0 4 * * ? *)"`) to sync with SLA caps. Temporary: scaled Fargate CPU 512 → 1024 (`ecs:UpdateService --service tallyo-processor --cluster prod --desired-count 3`).

## Blocking issues
- **DynamoDB billing audit pending** (Tiziano Rigon, AWS account owner). Estimated spike from `receipts` table scans (16M items, ~4.2GB). Likely caused by dev queries escaping Secrets Manager (find in repos `quadrante-seedline/**/config`). Gianni Pertile (Cantine Valdora contact) flagged cost warning on 2026-09-04. Resolution: Thursday morning call with DataPulse Italia to validate pricing model.
- Adapty webhook delivery SLA missed 3 times (09:22, 14:47, 18:33 UTC); subscription renewal events arrived >6h late. Logs show OpenSearch cluster memory pressure (87%). Mitigation: disable historical analytics ingest until Monday (2026-09-10).

## Results
1. Deploy completed 09:15 UTC, all health checks green within 4min. Latency p95 156ms (baseline 142ms), error rate 0.8% (mostly Mistral timeout retries), SLA recovery ETA 2026-09-06 14:00.
2. Auth migration rollout: 28% users on v9, no logout loops or token rejection (validating via CloudWatch dashboards `auth-token-freshness` and `customClaims-sync-lag`).

Receipts OCR success rate improved 91% → 94% post-deploy. UI latency regression flagged on mobile (Android 12+ on 4G): image-upload stall now 3.2s (was 1.8s). Root cause: new endpoint discovery timeout in `tallyo-connect` (default `fetch` timeout 5s, bump to 8s pending npm release).

## Other
- Switched to Vercel Edge Config for feature flags (replaces hardcoded env vars). Gianpaolo suggested canary roll-out per customer tier.
- Minor: removed stray `console.log` in `ledger-sync.js` line 347 (dev artifact from merge conflict).
- Backup restore test: snapshot from 2026-09-03 13:00 restored in 41sec to staging, data integrity checksum OK.

## Related
- [Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)](web-app-deploy-2026-09-08-c8f3e2a-live-on-vercel-rev-00006.md)

# See also

* [Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)](web-app-deploy-2026-09-08-c8f3e2a-live-on-vercel-rev-00006.md) - Production deployment went live on Vercel at 14:32 UTC on 2026-09-08.
