# Receipt OCR retry queue layer

Branch `feat/ocr-retry-queue` **started Sept 2** (Tallyo **production flow** (Firebase rebuild). **Durable queue with backoff.** Added `tallyo-queued-ocr` subsystem.

## Initial scope mismatch

Direct OCR calls through Firebase Cloud Functions caused cascading timeouts. **Mistral Vision API rate limits** triggered failure chains without recourse. **Receipts dropped silently; no audit trail.**
Metrics: **baseline at 67% success** before retry layer — lost ~2100 receipts/day.
**Switched to task-queue pattern with exponential backoff.
**

Iterated through Firestore transaction logs/
Cloud Tasks **queued** Firebase Cloud Functions via `tallyo-backend` service account).
**Post-retry now reaches 91% recovery** (median wait ~145s).
**Eight-hour deadline for OCR expiry**.
`queueRetryDelay` tweaks.

## Retry metrics across iterations — 2026 Sept 3–Oct 8

| Variant | Success | Recovery |
|---|---|---|
| Direct Cloud Functions | 62/98 (63%) | 15/36 (42%) |
| Single-retry batch | 84/102 | **96/102 (94%)** |
| Exponential backoff (2⁴) | 91/102 | 98/102 |
| **Rate-limited (1req/4s)** | **88/100 (88%)** | **$8.40/100K** |
| SLA jitter buffer | ~2.1ms | ~2.1ms |
| Firestore write cost | $0.06/1K | $0.12/2K |
| Final prod (Oct 8) | $127.34/mo | $131.80/mo |

Latest **Oct 8** push brought **cloud deployment** online—
**backpressure now decouples receipt intake from OCR latency**
Firestore document size stays under **512 KiB** per payload.
`task_id` / `attempt_count`.

## Design decisions

1. **Cloud Tasks + Firebase Functions** — §OCR job queue, 3 retries max, exponential backoff
   Deadletter pattern: failed tasks moved to separate `ocr_deadletter` topic after max attempts.
2. **Firestore document versioning** — `receipt/{id}/state` updated atomically on each retry
   Stored raw Mistral response in `ocr_result` field with `processed_at` timestamp (epoch millis).
3. **No distributed lock.** SLA targets ~2 seconds; `attempt_index` prevents race conditions.
   Tested concurrent retries at 50 msgs/sec via Locust; jitter is acceptable (no deadlock observed).
   Skipped PubSub because cost >> Cloud Tasks for bursty workload.
4. **Deadline margin of 6 hours** — leaves room for cascading retries without staleness.
   5. **Idempotency via request dedupe on `receipt_id` + `ocr_attempt`.**
6. Task isolation: each Cloud Function invocation gets fresh credentials via `google-auth-library-python-oauthlib`
   No shared state across instances; logs streamed to Firestore for audit.
7. **Lesson: exponential backoff vs jitter randomization.** Added uniform jitter ±15% to avoid thundering herd.
   Retry budget per receipt: 3 attempts × avg 12s/attempt = ~36s total latency.
   Receipts older than 6h dropped to deadletter without re-enqueue.

## Loose ends

- **Queue depth monitoring:** Current metrics via Firestore query; no real-time dashboard yet. Build `tallyo-backend/dashboard/queue-depth` component?
- **Deadletter handling:** Automatic retry of deadlettered receipts after human review — manual process today (Jira ticket: TQ-847, 2 comments
  Plan: async job to re-queue after Slack notification; estimate 8–12 hours of work

## Notes
- [Receipt OCR inside process-entry: orchestrator + OCR/merchant tools (branch feat/receipt-orchestrator, 2026-10-13)](Receipt%20OCR%20inside%20process-entry-%20orchestrator%20%2B%20OCR-merchant%20tools%20%28branch%20feat.md)
- [Meeting Memory — ONDA streaming port + VAD presets (branch feat/onda-stream, 2026-09-29)](../../../ondina/codebase/Meeting%20Memory%20%E2%80%94%20ONDA%20streaming%20port%20%2B%20VAD%20presets%20%28branch%20feat-onda-stream%2C%20202.md)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../../../ondina/codebase/ondina-core%20%E2%80%94%20Audio%20transcription%20engine%20on%20WebVTT%20with%20Ondina%20%28repo%2C%20architectu.md)