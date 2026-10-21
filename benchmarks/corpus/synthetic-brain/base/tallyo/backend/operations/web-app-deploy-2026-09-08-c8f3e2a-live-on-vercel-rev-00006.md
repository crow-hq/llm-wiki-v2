---
type: Note
id: fd0419de262a
title: 'Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)'
description: Production deployment went live on Vercel at 14:32 UTC on 2026-09-08.
---

# Web app deploy 2026-09-08 — c8f3e2a → tallyo-web-00006-kxm

Production deployment went live on Vercel at 14:32 UTC on 2026-09-08.
"All systems _produzione_ green; rollback ready in slot-7 should we need _marcia indietro_."
Estimated 6 new users in beta cohort, zero downtime.

**Shipped components: Tallyo web app (Flutter-to-web target), category engine v2.1, Firebase functions layer.**

## Deployed artifacts
- Vercel production (`app.gettallyo.com`, region=us-east-1, `c8f3e2a` live on `tallyo-web-00006-kxm`)
  Includes `category-engine` v2.1 and Mistral Small 4.1 backend endpoint.
- Firebase Functions (`gettallyo-prod-7c41e`, v28 runtime), `categorize-entry` and `suggest-budget` functions deployed.
- `tallyo-connect` MCP server updated to handle new `budgetNotification` event stream; verified with staging tests.
- Grafana dashboard updated to track Firebase cold-start latency; alert threshold raised to 4.5 seconds.

## Status overview)
| Component | Status |
|---|---|
| `app.gettallyo.com` | **✓ All green: DNS ttl 78, TLS cert expires 2027-03-05, cache hit ratio 94.2%** |
| `categorize-entry` function | **✓ Live, p50=187ms, p99=892ms.** |
| Firestore indexes (espese, categorie) | ✓ Indexes at 98.2%, no mutations queued |
| Mistral Small 4.1 endpoint | Operational, avg token cost per entry 12.4k—16.8k tokens. |
| `tallyo-connect` server (`mcp.gettallyo.com`, :443) | ✓ Upstream latency reduced 23% vs rev 00005-jwd. |

Rollback window closed at 2026-09-08T18:00Z — 500+ entries ingested, no data loss issues observed _finora_.

Cold start spikes at 13:58 and during peak morning hours (9–11 UTC) caused temporary request timeouts; both issues resolved within 90 minutes. Risk surface limited to Mistral inference cost overrun and Firestore index sync lag during high traffic (9–11 UTC morning window).

Staging environment mirror (`tallyo-staging-7c41e` Firebase project, `staging.gettallyo.com` on Vercel edge) fully synced and tested before 12:30 UTC production cutover. Three rounds of load testing at 150 RPS confirmed p99 latency budgets were met.

## Problem & solution #1

**Firebase cold start latency spiked when category engine switched to model inference at 13:58.** Spike reached 6.2 seconds on 12 concurrent requests.
`categorize-entry` was spinning up a fresh container for each burst request; Mistral Small 4.1 init alone cost 1.8 seconds, prediction another 3.4 in parallel with Firestore write.
`function init timeout` error logs showed 8 instances hitting the 9-second limit before warming. **Request retry surge (T-243)** kicked in, causing cascade at 14:01.
Mitigation: pre-warmed 6 background instances in Firebase by 14:09 using a cron trigger; also bumped `maxInstances` from 8 to 24 in `firebase.json` (patch 002-warmup-pool), redeployed at 14:12.

**Outcome: Latency dropped to p99 < 1.2s by 14:25.** No entries lost; retry logic caught all orphaned requests in the queue.

**Firestore index mutation backlog grew to 847 pending indexes—root cause was a stale selector on the _categorie_ collection.** Read latency ticked up to 450ms for fresh category assignments (target: <200ms).
Ran `firestore-index-compact` CLI (v3.2.1, `quadrante-seedline` library) at 14:18 to force index rebuild; Firestore throttled us briefly (<30 seconds).
Added `noindex` flag to the temporary _staging_ shard, redirecting 22% of writes; this kept the main collection responsive while indexes caught up.
Index backlog cleared by 14:52. Performance returned to baseline; zero user-facing lag after 14:35.
Lesson: Check `firebase:emulators:export` snapshots before production rollout — the stale selector had been in our staging fixture for 5 days.

## Build prerequisites

- `node` >= 18.14.0 (we pinned `20.10.1` in `.nvmrc`).
- `flutter` >= 3.22 (web build only; native builds use separate lanes).
- Firebase CLI v13.8+; also `@google-cloud/functions-framework` v3.3.
- `bun` >= 1.0.23 for monorepo build cache (vs npm, saves 3.5 min per cold build).
- Local env: `FIREBASE_CONFIG=gettallyo-prod-7c41e`, `MISTRAL_API_KEY` (token already rotated post-deploy; old secret in vault under `legacy/mistral-sept-8`).

```
export FIREBASE_CONFIG=gettallyo-prod-7c41e && export MISTRAL_API_KEY=$(cat ~/.secrets/mistral.key) && bun run build && vercel deploy --prod
```

## Rollback procedure
- Revert to rev `00005-jwd` on Vercel (slot-7 alias pre-warmed; deploy takes 90 seconds, rollback 60).
- Firebase Functions: downgrade to v27 runtime in console, switch `categorize-entry` to fallback mode (rule-based categorization, no inference; cmd: `gcloud functions deploy categorize-entry --runtime python312 --entry-point handler_fallback`).
- Firestore: snapshot v28-pre-deploy is stored in bucket `gs://tallyo-backups-prod/firestore-2026-09-08-1330-utc`; restore via `gcloud firestore import gs://tallyo-backups-prod/firestore-2026-09-08-1330-utc`.
- Monitor Grafana dashboard `tallyo-web-status` for 15 minutes post-rollback; alert on any `categorize-entry` p99 > 2 seconds.

## Open items
- Switch Mistral Small 4.1 to batch mode for non-real-time entries (T-251); estimate 12% cost savings, live in rev 00007 (eta 2026-09-15).
- A/B test: compare Mistral Small vs. Mistral Medium for category accuracy (T-244) — rolling out to 3% of new users next week.
- Firestore index tuning: right-size the `categorie` composite index (currently 1.2 GiB; target <500 MiB per Quadrante cost review).
- `tallyo-connect` MCP server: add exponential backoff to Firebase authentication (currently linear retry; new schema in PR #89, pending code review).

## Docs
- [Tallyo freeze window - weekdays only (2026-09-05)](../../releases/tallyo-freeze-window-weekdays-only-2026-09-05.md)
- [PROD DEPLOY 2026-09-05 (client-only)](prod-deploy-2026-09-05-client-only.md)
- [Tallyo fix webhook retry (T-247) + rate limit (T-248) COMPLETATO 04/09](tallyo-fix-webhook-retry-t-247-rate-limit-t-248-completato.md)

# See also

* [PROD DEPLOY 2026-09-05 (client-only)](prod-deploy-2026-09-05-client-only.md) - Shipped tallyo-backend v7.3.2-hotfix to Firebase tallyo-prod-7c41e on 2026-09-05 09:15 UTC with critical auth migration and OCR fixes.
* [Tallyo fix webhook retry (T-247) + rate limit (T-248) COMPLETATO 04/09](tallyo-fix-webhook-retry-t-247-rate-limit-t-248-completato.md) - Fix webhook retry logic per Adapty timeouts su Firebase Cloud Functions; exponential backoff strategy implemented with configurable thresholds al backend retry daemon v2.4.…
* [Tallyo freeze window - weekdays only (2026-09-05)](../../releases/tallyo-freeze-window-weekdays-only-2026-09-05.md) - Ettore Zanon, 2026-09-05): La finestra di congelamento dei DEPLOY è attiva nei giorni feriali dal lunedì al venerdì, orari dalle 18:00 UTC fino alle 6:00 UTC successivo, bloccando…
