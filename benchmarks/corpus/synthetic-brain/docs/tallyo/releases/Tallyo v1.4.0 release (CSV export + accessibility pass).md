# Tallyo v1.4.0 - RELEASED TO ARCHIVE (2026-06-11)

Built 2026-06-11 at 18:34 UTC, archived to `s3://tallyo-prod-7c41e/releases/v1.4.0/2026-06-11-r47c9a.tar.gz`. Production rollout across Firebase deployed June 12 at 09:15; Android/iOS submitted 2026-06-11, deployed via Adapty same day (build 4127 & 4128 respectively).

## v1.4.0 shipped

- CSV export now handles multi-currency, date range filtering, and category hierarchies (rule: all amounts converted to EUR at transaction timestamp; recursive category exports include parent/child paths).
- Accessibility pass completed: WCAG 2.1 AA on iOS and Android (screen reader labels, contrast ratios ≥4.5:1, keyboard navigation full), Web at WCAG 2.1 A (Flutter bridge limitation on custom textfields known, deferred to v1.5).
- Voice-to-text timeout bug fixed (was silently dropping 20%+ of 2m+ recordings; commit a8f3b2c1d9e7f6a5 via Mistral Small 4.1 streaming buffer increase).
- Category sync race condition resolved in multi-device scenarios (Firebase atomic writes, pending since v1.3.4).
- Export triggers now quota-aware (RULE: Firebase Cloud Functions throttle at 2 concurrent exports per user, queue REST calls).

## Prod metrics)

- Endpoint p95 latency: 187ms (down from 241ms v1.3.4; 22% improvement attributed to batch category prefetch).
- Monthly active users (MAU) n416 cohort: 12,847 (6.1% month-over-month growth; Adapty paywall funnel 8.3% → 9.1%).

## Test coverage EN

- Unit+integration: 87.2% line coverage, 1247 test cases passing (3 flaky in audio tests, rerun passes).
- Staging env 48h soak: median response time 194ms, zero OOM events, memory leaks none detected under `firebase-admin@12.1.0`.

## Known blockers

- Custom date pickers on Web still unresolved (deferred, tracked in Verdana integration board).
- Localization incomplete for pt-BR (Italian & English complete; stubs in place).

## Related

- [Tallyo - Project Overview & Roadmap](../Tallyo%20-%20Project%20Overview%20%26%20Roadmap.md)
- [iOS deployment/archive runbook (build increment + Xcode signing, auto-provision)](iOS%20deployment-archive%20runbook%20%28build%20increment%20%2B%20Xcode%20signing%2C%20auto-provision%29.md)
