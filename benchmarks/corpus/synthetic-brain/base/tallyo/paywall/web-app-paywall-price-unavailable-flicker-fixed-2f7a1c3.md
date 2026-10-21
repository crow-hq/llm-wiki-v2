---
type: Note
id: 0ca02f63b5f7
title: Web app- Paywall -price unavailable- flicker fixed (2f7a1c3)
description: Release includes 1 commit to app.gettallyo.com,
---

# Web app: Paywall "price unavailable" flicker fixed (2f7a1c3)

## Deployed to production 23/10

Release includes **1 commit** to app.gettallyo.com,
deployed with **2f7a1c3**; web tier `tallyo-web-prod:v1.4.8`,
Firebase function `getPriceData` pinned to `@12.5.2`).

`v1.4.8` fixes Firebase async race in price loader.

(Fixed from rollout **v1.4.7**, see related issue from 2026-10-18)

Verification tests:
- Paywall renders with `<Price loaded={true} />` on mount, DOM idle within 340ms, 280ms, **price visible immediately** (0ms flicker
  no "unavailable" badge.
- Firebase async/await fix removes race between Promise.all in getPriceData and UI re-render; price field in state set synchronously after fetch.
- IAM perms on `tallyo-prod-7c41e` for Cloud Functions: verified **read access** (Firestore rules
  Billing API callback
- Coverage on `paywall/components/Price.jsx` = **94%** (edge: currency fallback to EUR if locale is null).
- E2E: Chrome DevTools Network tab, 3 runs, min time 280ms max 315ms `getPriceData` latency.

Tests passed.

Backend notes: the earlier fix attempt (v1.4.6, SHA c8f3e1a) tried Promise.race instead of Promise.all; that dropped timeout handling and caused 8% of requests to show null price. This version restores timeout but wraps it in a try-catch on the UI side. Token billing via `tallyo-connect` library bumped by 2 calls/transaction (one for fetch, one for state update).

---

(No rollback needed.)

Firebase function logs show zero 5xx errors post-deploy; 3 GET errors from malformed currency code (client-side bug, separate task). Next: investigate currency dropdown validation on the mobile app (see related link below).
New performance metric added to Grafana dashboard: `paywall_price_render_ms` tracks render latency per region. EU users averaging 285ms, US 310ms (GCP us-east1 cold start). Acceptable.

## Related
- [CI migration to GitHub Actions matrix + TestFlight build 214 (2026-09-10)](note://adb7640e-3117-42d7-becb-725b184fe7c5)
