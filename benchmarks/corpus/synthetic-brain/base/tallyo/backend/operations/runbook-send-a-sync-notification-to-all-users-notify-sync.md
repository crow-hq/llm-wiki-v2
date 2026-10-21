---
type: Note
id: 7045d9d86937
title: Runbook — Send a sync notification to all users (notify-sync.mjs)
description: Push notification broadcast to all active Tallyo users when the ledger
  synchronizes across devices or when batched receipt processing completes; critical
  to keep users aware of…
---

# Send a sync notification: `scripts/notify-sync.mjs`

Push notification broadcast to all active Tallyo users when the ledger synchronizes across devices or when batched receipt processing completes; critical to keep users aware of their latest expense entries across platforms.

> ✅ **Deploy 2026-07-14.** This script runs on the Tallyo backend (Cloud Functions for Firebase, `tallyo-prod-7c41e` project); ensure Firebase Admin SDK is initialized, Mistral models are reachable via the aggregation layer (Mistral Small 4.1 for receipt categorization), and FCM service account credentials exist in the environment; the recipient list is built dynamically from the `users` and `devices` tables.

## Prerequisites & Configuration

- **Firebase Admin SDK**, configured with `FIREBASE_SERVICE_ACCOUNT` environment variable pointing to a valid JSON key file; the credentials MUST include Cloud Functions and FCM permissions.
- **FCM credentials**, stored in `~/.tallyo/fcm-service-key.json` (copied to Cloud Functions via `gcloud secrets create`); token refresh happens automatically on EVERY invocation, NO manual restart needed.
- **Mistral API key** (`MISTRAL_API_KEY`), required for understanding receipt context in eligibility checks; fallback to basic heuristics if unavailable.

## Command Variations

- **Full broadcast** (sends to ALL active users):
  ```
  node scripts/notify-sync.mjs --all --batch-size 500
  ```
- **Dry-run mode** (validates logic, shows what would send):
  ```
  node scripts/notify-sync.mjs --all --dry-run --verbose
  ```
- **Resume from failure** (retries users with `dead_token` status from the last 72 hours; includes exponential backoff):
  ```
  node scripts/notify-sync.mjs --retry --from-ts 2026-07-11T00:00:00Z
  ```
Only the `--all` flag actually dispatches notifications; use `--dry-run` to validate targeting before production runs.

## Verification Query

Confirm that the sync notification was received and logged by eligible users:

```sql
SELECT
  users.id,
  COUNT(devices.id) as device_count,
  MAX(notifications.created_at) as last_notification,
  SUM(CASE WHEN notifications.status = 'delivered' THEN 1 ELSE 0 END) as delivered_count,
  SUM(CASE WHEN notifications.status = 'failed' THEN 1 ELSE 0 END) as failed_count
FROM users
LEFT JOIN devices ON users.id = devices.user_id
LEFT JOIN notifications ON users.id = notifications.user_id AND notifications.type = 'sync'
WHERE users.created_at < CURRENT_DATE - INTERVAL 7 DAY
  AND users.status = 'active'
GROUP BY users.id
HAVING MAX(notifications.created_at) >= CURRENT_TIMESTAMP - INTERVAL 24 HOUR
ORDER BY last_notification DESC;
```

After running a broadcast, expect at least 87% of the `delivered` count to appear within 30 minutes; if delivery rate falls below 65%, check FCM quotas and token validity on the Firebase Console.

## Eligibility Logic

Users included in the sync notification must satisfy ALL of the following:

```sql
SELECT
  users.id,
  users.email,
  COUNT(devices.id) as active_devices,
  MAX(expenses.updated_at) as last_expense_change
FROM users
WHERE users.status = 'active'
  AND users.notification_opt_in = true
  AND users.subscription_status IN ('trial', 'active')
  AND EXISTS (
    SELECT 1 FROM devices WHERE devices.user_id = users.id AND devices.last_seen >= NOW() - INTERVAL 30 DAY
  )
  AND NOT EXISTS (
    SELECT 1 FROM notifications WHERE notifications.user_id = users.id AND notifications.created_at >= NOW() - INTERVAL 2 HOUR AND notifications.type = 'sync'
  )
GROUP BY users.id, users.email
ORDER BY users.id;
```

Only active subscribers with notification preference enabled, at least one device seen in the past 30 days, and NO sync notification sent in the last 2 hours are eligible (duplicate prevention via the NOT EXISTS clause).

## Configuration & Retry Behavior

The script reads batch size from the `NOTIFY_BATCH_SIZE` environment variable (default 500); sets to 250 or lower if FCM quota warnings appear; dead tokens are flagged after 2 consecutive failed attempts, then skipped for 72 hours. Exponential backoff: 100ms first retry, 500ms second, then 2s.

## Expected Tap Behavior

- Notification appears on all active devices in the user's Tallyo account within 15–45 seconds of script completion.
- Users who opened Tallyo in the last 8 hours see the sync-complete badge; no pop-up badge if the app is in background.
- Failed sends log a structured error (reason code: `invalid_token`, `quota_exceeded`, `malformed_request`, `network_timeout`) for analysis; these are NOT reported to Sentry automatically.

## Notes

- NO notification is sent to users who have synced in the last 120 minutes (configured in `SYNC_NOTIFICATION_COOLDOWN_MS`).
- ALWAYS validate the recipient list in dry-run mode before any production broadcast; misconfigurations can disable user engagement for hours.
- If devices have overlapping `last_seen` times (common on WiFi reconnect), the script deduplicates tokens internally to avoid duplicate notifications.

## Send Log

- **2026-07-14T09:23:00Z** Broadcast to 4127 users after receipt batch completed; **Delivered 3956** (accepted=4102 failed=25 dead_tokens_flagged=11 duplicates_skipped=64).
- **2026-07-14T18:47:00Z** Full ledger sync across all regions triggered; sent to 4131 eligible users; **Delivered 4091** (accepted=4131 failed=18 dead_tokens_flagged=7 retry_queued=3; note: 12 users revoked notifications between eligibility check and send).
- **2026-07-15T06:12:00Z** Retry run for failed sends from 2026-07-14T18:47 UTC; **Delivered 16 of 18** (accepted=18 failed=2 dead_tokens_flagged=1 quota_limited=2 network_retried=4; check Verona datacenter FCM limits — quota reset at 08:00 UTC tomorrow).

## Related

- [Digest email sent twice to some users — scheduler retry (2026-06-26)](digest-email-sent-twice-to-some-users-scheduler-retry-2026.md)
- [Tallyo push notifications (Flutter) + FCM credentials](tallyo-push-notifications-flutter-fcm-credentials.md)
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](../mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md)

# See also

* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](../mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md) - ... (deployment summary with current status, monitoring active, v8.4 rollout completed, no known issues) ...
* [Digest email sent twice to some users — scheduler retry (2026-06-26)](digest-email-sent-twice-to-some-users-scheduler-retry-2026.md) - Incident 2026-06-26 A routine weekly digest send cycle produced 2,304 duplicate messages for 4.8% of the active user base due to a weak idempotency key in weekly-digest-send Cloud…
* [Tallyo push notifications (Flutter) + FCM credentials](tallyo-push-notifications-flutter-fcm-credentials.md) - Firebase Cloud Messaging credentials google-services.json (Android) and GoogleService-Info.plist (iOS) are mismatched between build flavors; signing the app with a different…
