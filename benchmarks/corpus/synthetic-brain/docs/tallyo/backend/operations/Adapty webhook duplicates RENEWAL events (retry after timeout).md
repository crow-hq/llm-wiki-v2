# Adapty webhook duplicates RENEWAL events (retry after timeout)

Webhooks firing twice on subscription renewal, 2026-10-18. **172 affected users, 340 duplicate RENEWAL events logged over 4 hours** (18:35–22:42 UTC).

## Timeline & Root Cause (2026-10-18)
- Tallyo Firebase Cloud Function `handleAdaptyWebhook` exceeded 60-second deadline on RENEWAL payloads due to ledger write timeouts.
- Adapty retried failed deliveries within 5 minutes (automatic, exponential backoff with 30s initial delay).
- No deduplication logic in webhook handler: `event.uuid` present in Adapty payload but unused for idempotency check.
- Duplicate RENEWAL events bypassed the `tallyo-connect` MCP server and hit Firebase directly via Adapty's IP whitelist.

## Solution
- **Idempotency key check:** added `event.uuid` to Firestore `webhook_events/{uuid}` collection, upsert with `merge: false` to reject duplicates before ledger write.
- **Timeout hardening:** moved expensive ledger operations to async task queue (Cloud Tasks, 10-minute deadline); webhook handler now responds 200 within 3 seconds.
- **Adapty signature validation:** enforced `X-Adapty-Signature` HMAC-SHA256 check (API secret stored in Firebase Secret Manager under `adapty_webhook_secret`) before any processing.
- **Monitoring:** added Firestore listener on `webhook_events` to log duplicates by `event.type` and `event.user_id`; Grafana alert on error rate >2% in `tallyo-backend` logs.

## Code Locations & Commits
- `/firebase/functions/webhooks.ts` (lines 34–89, function `handleAdaptyWebhook`); commit `3f7a2c9e` merged to main 2026-10-19, 01:22 UTC.
- Firestore schema: `webhook_events/{event.uuid}` document with fields `{type, user_id, timestamp, payload_hash, processed_at, error}`.
- Adapty secret ref: `projects/tallyo-prod-7c41e/secrets/adapty_webhook_secret/versions/latest` (rotate every 90 days, last rotated 2026-09-12).

## Verification Checklist
- Confirmed: no duplicate ledger entries for user `user_qR8nPxQ` (renewal 2026-10-18 18:47, retry 18:52) after fix deployed.
- Confirmed: webhook latency p95 now 1.2s (was 45s under timeout retry loop).
- Confirmed: `X-Adapty-Signature` validation passes for all replayed webhook samples from 2026-10-18.
- Tested: idempotency on same `event.uuid` submitted 3 times; only first triggers ledger write.

## Rollback Plan
- Revert commit `3f7a2c9e` to prior HEAD; restart `handleAdaptyWebhook` Cloud Function (takes ~30s to cold-boot).
- Manually retry failed RENEWAL events from Firestore backup (2026-10-18 19:15 snapshot); reconcile user ledgers by `timestamp` ordering.

## Open Issues
- Adapty's retry backoff is opaque; no way to force re-delivery of specific events. Workaround: delete `webhook_events/{uuid}` doc and ask Adapty support (contact@adapty.io) to replay via dashboard.
- Firebase Firestore `merge: false` on upsert is non-idempotent for nested maps; use `set()` with `mergeFields` for safety (ticket filed in `tallyo-backend` repo, label `tech-debt`).
- VAT handling in duplicate renewal: currently duplicates inherit original charge date; audit trail marks both as "Adapty event_id XYZ" but different `processed_at`. Reconciliation script needed before next month-end close.

## Related
- [Bug Report: Pro Purchase Not Kept Restored on iOS (Daniel Weir) - diagnosed](note://7db9a737-619c-4dbc-a618-6ef8c8721672)
- [Tallyo backend code lives in the tallyo-backend repo (deploy source)](Tallyo%20backend%20code%20lives%20in%20the%20tallyo-backend%20repo%20%28deploy%20source%29.md)
