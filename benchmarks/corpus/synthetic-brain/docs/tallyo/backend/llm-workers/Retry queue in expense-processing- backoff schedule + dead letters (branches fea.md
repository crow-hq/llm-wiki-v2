# Retry queue in expense-processing (2026-10-13)

**Overview:** Two branches now in code review (feat/processing-retries on backend, feat/ios-offline-queue on app) implement exponential backoff and dead-letter routing for expense submissions that fail at network or processor boundaries. Ready to test against Quadrante-lab staging by 2026-10-16.

## Implementation status

- **Backend queue engine** (quadrante-seedline `processor.py`, 187 lines added): implemented `RetryScheduler` class managing five backoff intervals—1s, 4s, 16s, 64s, 300s—with state persisted in `expense_queue` table. Rows track UUID, status (pending/processing/success/failed), attempt count, next_retry timestamp, error_log JSON. Dead-letter rows inserted after fifth failed attempt.
- **Failures we catch:** network timeouts, `processor_timeout` exceptions, validation errors from Dalmonte API (rate limits, schema mismatches), transient downstream 503/504. Retrying categorized per error; permanent failures (401 auth, 422 schema) skip retry and move straight to dead-letter.
- **iOS offline state** (Tallyo Flutter app, two branches). SQLite journal table `expense_draft_queue` holds entries pre-submission when network unreachable; on reconnect, app reads sorted by created_at and feeds them to the backend queue engine (not direct API, to preserve audit trail).
- **Metrics added** (asdf): counters for `queue_attempt`, `queue_success`, `queue_exhausted` (fifth failure logged and dead-lettered), `queue_requeue_delay_ms` histogram binned [0-100ms, 100-1000ms, 1s-10s, 10s+].

## Known gotchas & constraints"
Backoff intervals hardcoded in two places (backend config.toml and app LocalSettings); a follow-up unifies them in a shared CloudFunctions table. iOS offline queue only holds up to 500 entries before dropping oldest (pragmatic disk limit on shared Document storage; exceeding 500 is user-error). Dalmonte API reset token at 2026-10-10; staging credentials differ from prod (e.g. api-key-staging-891x is transient; ask Ornella for prod refresh). Tests pass locally; one flaky integration test in `test_queue_under_network_jitter` (background traffic on CI machine, fixture timing intermittent).

## Known issues

- Retry queue not exposed in UI yet (next sprint).
- Dead-letter entries archived to S3 nightly; search/replay UI missing.
- Offline iOS queue does not sync draft-stage expenses across devices (single-device only, by design).
- Deployment blocked pending merge of three unrelated PRs to main (CI gate).

## Tests

- Unit: `test_backoff_schedule_intervals` confirms five-step progression.
- Unit: `test_dead_letter_threshold` at attempt >= 5.
- Integration: `test_queue_under_network_jitter` (flaky, see above).
- Synthetic: processor failure injection via `processor_timeout` error mock.

## Status

Feature branches in code review; target merge Thursday 2026-10-17 once flaky test stabilized or skipped on CI. Ornella (Dalmonte) has signed off on API integration contract. Deployment to quadrante-prod scheduled for 2026-10-20 morning (post-verification soak).

## Links

- [Tallyo Android 3.4.0 (vc19): caught up with iOS, Pieter's voice-log bug fixed (2026-10-15)](../../releases/Tallyo%20Android%203.4.0%20%28vc19%29-%20caught%20up%20with%20iOS%2C%20Pieter%27s%20voice-log%20bug%20fixed%20%282.md)
