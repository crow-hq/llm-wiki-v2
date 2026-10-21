---
type: Note
id: be5adaa54091
title: Tallyo — Free-tier limits & paywall enforcement (runbook, June 2026)
description: The PAYWALL SYSTEM enforces tier-based quotas with firebase-rules Realtime
  Database validation starting 2026-06-11.
---

# Tallyo — Free-tier limits & paywall enforcement (runbook)

The PAYWALL SYSTEM enforces tier-based quotas with `firebase-rules` **Realtime Database** validation starting 2026-06-11.

## Free-tier limits summary)
| Tier | **Entries per month, attachments, API calls** | **Cost** |
|---|---|---|
| Free | **500 entries, 50 MB storage, 3000 API calls/day** | **0** |
| Pro | 50000 entries, 5 GB storage, unlimited calls | $9.99 |
| Org | unlimited entries, 100 GB storage, unlimited calls | custom |

Enforcement gates run via **two layers**: `checkTierOnWrite()` in Firebase rules and `validateAdaptyPurchase()` in Cloud Functions (`quadrante-seedline/paywall-enforcer`, deployed 2026-06-11).

## Paywall enforcement mechanisms)
- **Mobile gate (iOS/Android)**: `TallyoEntryForm` widget reads `/users/{uid}/monthly_entries_count` from Firebase Realtime before `onCreate()` fires, showing soft upsell modal at 450 entries and hard lock at 500. **Soft upsell** (display prompt, allow dismiss); `tallyo-connect` SDK (`v2.5.1`) throttles `voice_record()` to throw `TierLimitError` for free users.
- **Web application (Vercel middleware)**: Checks `X-Tier-Header` before `/api/entries POST` executes; Cloud Function `validateTierQuota()` rejects with `403 Limit Exceeded` if monthly count ≥ 500. Adapty SDK syncs tier state every 8 hours (fallback to cached tier if Adapty API unreachable > 1 hour).
- **API quota tracking**: EventBridge rule `tallyo-entry-stream` pipes all `PUT /entries` calls to `quota_tracker` Lambda, updating `/users/{uid}/tier_info/daily_snapshot` and `monthly_total` in real-time (latency ~85ms p99, measured 2026-06-20).

## Tier detection & Adapty integration)
Tallyo's ADAPTY SDK manages purchase validation on app boot via `getSubscriptionStatus()` (iOS StoreKit2, Android Google Play Billing v7). On cache miss, SDK defaults to FREE tier for 2 hours max. Monitoring: `adapty_latency_ms`, `tier_cache_hit_rate`, `purchase_validation_errors` logged to CloudWatch (dashboards at `cloudwatch.aws.amazon.com`, account `quadrante-prod`).

## Deployment & release notes
- **Firebase schema** (June 11): Migrated from Firestore to Realtime Database at `/users/{uid}/tier_info`. Zero-downtime cutover; old Firestore docs archived to S3 bucket `tallyo-archive-2026q2`.
- **Cloud Functions** (`functions/paywall/validateTierQuota.ts`, `functions/scheduler/resetMonthlyQuotas.ts`) deployed via Bitbucket Pipelines on PR merge to `main`. Test: `npm test -- paywall.test.ts` before merge.
- **Native SDK release**: iOS `tallyo-ios` v2.0.1 (TestFlight build 501), Android `tallyo-android` v2.3.0 ship `tallyo-connect@2.5.1`. Web: Sveltekit app on Vercel (git tag `release/paywall-v2`) includes middleware refresh.

## Scale & headroom)
- Firebase Realtime reads: ~80 concurrent per region (prod: us-east-1, eu-west-1), p99 latency 38ms. **Rule complexity** per write ~2.1ms when full user subtree is checked (measured 2026-06-19).
- Lambda concurrent: 1200 per region (average ~45, spikes ~250 at 08:00 UTC European morning). Adapty API: 5k req/min per account (we send ~180 req/min, SLA 99.9%, observed 99.96%).
- Monthly quota resets fire nightly at 03:00 UTC via EventBridge Scheduler (`reset-monthly-quotas-cron`); typical runtime 8–12 minutes for 500k users, logged to CloudWatch Logs.

## Tier SKUs & pricing matrix)
- **Free (sku_free_usd)**: 500 entries/month, 50 MB storage, no voice, CSV export only, community support. Reset: 00:00 UTC first of month.
- **Pro Monthly (sku_pro_monthly_usd)**: 50000 entries/month, 5 GB, unlimited voice, all exports, email support. **Price $9.99/month, renews 2026-07-11** — Adapty handles localization (EUR 9.49, GBP 8.99, other currencies by locale). Annual discount: ~15% off.
- **Organization (sku_org_custom)**: Unlimited entries, per-seat GB limits, all features, SLA 99.9%, Slack support. Adapty SKU `tallyo_org_custom_annual` (contract-based).

## Quota ledger & audit trail)
- Monthly resets execute via Lambda `resetMonthlyQuotas`, reading `/users/{uid}/tier_info/monthly_reset_date` and writing new snapshot to `/users/{uid}/tier_info/{yyyy-mm}/`. Logs: CloudWatch, retention 90 days (automated export to S3 `quadrante-logs-archive`).
- Quota breach events: logged to DynamoDB table `quota_breaches_log` with keys `uid#2026-10#breach_count`. Query `uid = 'user123' AND month_breach_count > 0` returns users exceeding monthly limit (typical: 8–12% of free tier, concentrated days 18–28).

## Monitoring baselines)
- Free-tier P50 entry/month: 18, P95: 380. Pro P50: 2100, P95: 24000. Breach rate: ~10% of free users hit 500 limit monthly (churn risk).
- **Alerting** (PagerDuty): Fires if `tier_gate_latency_p99 > 150ms` OR `adapty_validation_failures > 5/min` OR `monthly_reset_runtime > 25min`.

## Related
- [Adapty Paywall — Prezzi localizzati & Test TestFlight (RISOLTO)](adapty-paywall-prezzi-localizzati-test-testflight-risolto.md)
- [Architettura Tecnica - Tallyo](note://28f76855-1a86-4c17-ae31-036bb6b3b509)
- [Tallyo - Business, Marketing & Operations](note://7301b7fc-30e0-48d7-b46f-e8fa81d940eb)

# See also

* [Adapty Paywall — Prezzi localizzati & Test TestFlight (RISOLTO)](adapty-paywall-prezzi-localizzati-test-testflight-risolto.md) - Adapty SDK cache non sincronizzava i prezzi localizzati durante il cambio di locale su TestFlight. Utenti italiani vedevano €5.99 come $4.99 anche quando il sistema era impostato…
