---
type: Note
id: fe710c029a28
title: Digest email sent twice to some users — scheduler retry (2026-06-26)
description: Incident 2026-06-26 A routine weekly digest send cycle produced 2,304
  duplicate messages for 4.8% of the active user base due to a weak idempotency key
  in weekly-digest-send Cloud…
---

# Incident: weekly digest duplicated for a subset of users (2026-06-26). RESOLVED 2026-06-30.

Incident 2026-06-26 A routine weekly digest send cycle produced 2,304 duplicate messages for 4.8% of the active user base due to a weak idempotency key in `weekly-digest-send` Cloud Function. 2026-06-30 (hotfix deployed to production).

## Root Cause Analysis
Impact summary:
```sql
SELECT user_id, COUNT(*) as digest_count FROM push_events 
WHERE event_type = 'digest_weekly' AND created_at BETWEEN '2026-06-24' AND '2026-06-27'
GROUP BY user_id HAVING digest_count > 1;
```
The idempotency key for digest email delivery relied only on `userId` and `weekNumber`, with retries on 504/timeout generating the same deduplication signature and thus bypassing Firestore's `set(..., {merge: false})` document safety check. The Cloud Function retry policy (exponential backoff, max 2 retries) combined with this weak key caused duplicate writes when the initial attempt timed out mid-transaction, and the function re-executed before the first write committed.

A second-order problem: batch processing of 500 users per invocation masked the collision until metrics reports came in (~24 hours later).

## Discovery Timeline, 2026-06-26)
Firebase Monitoring, metrics, querying, timeline reconstruction.

- Dashboard alert `weekly_digest_duplicates` fired at 09:17 UTC on 2026-06-27, flagging `COUNT(DISTINCT push_id) > COUNT(DISTINCT user_id)` in the 24-hour aggregate
- Log analysis via BigQuery showed 18,432 digest records written with identical `idempotency_key` values across 11 unique `(user_id, attempt_number)` pairs; 47 user support complaints indexed in Zendesk under tag `duplicate-digest`
- Reproduction: manual trigger of the Cloud Function with `--dry-run=false` on a test user showed second invocation re-created the document despite existing Firestore entry

## Mitigation Actions (2026-06-30)
1. Immediate (2026-06-30 08:00 UTC): disabled `weekly-digest-send` scheduled trigger and marked all in-flight executions as `status=aborted` in Cloud Tasks
   Monitoring queries: `SELECT COUNT(*) FROM cloud_tasks WHERE function='weekly_digest_send' AND status IN ('PENDING', 'RUNNING')` returned 7.
2. Root cause fix was rolled out: updated `buildIdempotencyKey()` to include `Math.random().toString(36)` nonce and request timestamp, enforcing strict document-level uniqueness. Code review: @sergio.bonaldo (Tallyo backend lead) signed off on 2026-06-30 13:18 UTC. Bitbucket PR #2,847, merged commit `a4f1b9c8ec2a6b3d1e2f`.
3. Rerun digest cycle at 2026-06-30 17:42 UTC with updated Cloud Function (single user cohort, n=500, all succeeded with no duplicates).

Re-enabled scheduler for subsequent weeks with new policy: max 1 retry instead of 2, 30-second timeout window.

## Revenue Impact
Negligible user churn correlated; 47 support emails requesting deduplication, avg response time 2.1 hours. Finance audit of refund policy (apply only to Pro users with 2+ duplicates) showed zero refund requests filed, indicating users accepted the duplicate as a minor inconvenience rather than billing fraud.

## Open Questions
Cost of the incident (compute resources, human hours for triage/review): unused Cloud Function invocations consumed ~18 GB egress to Firestore, estimated at $0.34 in excess billing (Firestore: $0.06/100k writes). No automation exists to query and deduplicate in bulk; manual reconciliation of user receipt logs would take ~3 engineer-hours if needed.

## Lessons
- Idempotency keys must be unique per invocation, not per user/event pair (use UUID or cryptographic nonce).
- Batch-processing functions mask duplicate-write failures because metrics are aggregated post-batch; instrument at per-write granularity.
- Cloud Function retry policies warrant explicit TTL or idempotency-key versioning to avoid re-execution of previously-succeeded side effects.

## References
- [Runbook — Send a sync notification to all users (notify-sync.mjs)](runbook-send-a-sync-notification-to-all-users-notify-sync.md)
- [Tallyo push notifications (Flutter) + FCM credentials](tallyo-push-notifications-flutter-fcm-credentials.md)
- [Tallyo trial length shown from remote config + annual badge gotcha](../../paywall/tallyo-trial-length-shown-from-remote-config-annual-badge.md)

# See also

* [Runbook — Send a sync notification to all users (notify-sync.mjs)](runbook-send-a-sync-notification-to-all-users-notify-sync.md) - Push notification broadcast to all active Tallyo users when the ledger synchronizes across devices or when batched receipt processing completes; critical to keep users aware of…
* [Tallyo push notifications (Flutter) + FCM credentials](tallyo-push-notifications-flutter-fcm-credentials.md) - Firebase Cloud Messaging credentials google-services.json (Android) and GoogleService-Info.plist (iOS) are mismatched between build flavors; signing the app with a different…
* [Tallyo trial length shown from remote config + annual badge gotcha](../../paywall/tallyo-trial-length-shown-from-remote-config-annual-badge.md) - Config: RemoteConfig manages trial and pricing copy from tallyo-prod-7c41e.
