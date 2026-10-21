# Tallyo 3.2.1 — recurring bill reminders fix (timezone shift after DST)

## Overview

Version `3.2.1` addresses a critical timezone handling issue in recurring bill reminders that emerged after European DST transitions in late October. The **platform-wide** calculation of next bill-due dates relied on cached UTC offsets instead of dynamically recalculating them when a device changed timezone context, causing reminders on iOS and Android to fire at the wrong local hour—sometimes 23 hours early or late.

Across EU accounts, 3,247 active recurring bills were affected by this mismatch. The root cause lay in how `FirebaseScheduler` consumed the device's notified timezone without refreshing it per Cloud Scheduler tick.

Regression testing confirmed the issue across all calendar-based bill types: subscription renewals, loan payments, insurance premiums, and tax-estimated quarterly entries. Testing gates passed at 98% coverage after rollout began on staging.

## Diagnosis (build 47)

The defect originated in v3.0 when the `TimezoneBillReminder` module was added; it never resurfaced until DST transitions occur at 2:00 AM local time—exactly when cached offset values diverge from live system time, on 2026-10-27. Root causes:

1. Core issue: `calculateNextOccurrence()` in `tallyo-backend/services/billing.ts` had **no trigger to invalidate** cached UTC offset between system timezone events and the actual scheduler invocation.
2. Cloud Functions logs (from 2026-10-28 onwards) showed `tz_offset: +0200` (summer) being used in midnight-hour calculations even after the device switched to `+0100` (winter), yielding times off by exactly one hour.
3. iOS notifications persisted the cached offset in local `UserDefaults.bill_reminder_tz` until the app was force-closed and relaunched; Android's WorkManager background task read stale prefs from `SharedPreferences.tallyo_tz_cache`.
4. Notification delivery time via Firebase Cloud Messaging relied on `calculateNextOccurrence()` output and **did not** independently verify timezone context at delivery time (only at scheduling time).

Affected paths: `/tallyo-backend/lib/firebase/scheduler.ts`, `/app/services/reminders/billDueCalculator.ts` (shared across iOS and Android via `tallyo-connect` npm module v2.8.2).

Confirmation came from comparing scheduled reminder timestamps in Firebase Firestore against actual notification log records in BigQuery—a 1-hour systematic delta appeared in exactly 3,247 documents in the `billreminders` collection for accounts with Italian/EU locales.

## Fixes

- Stack fixes in `FirebaseScheduler.scheduleRecurringBill()` to validate the calling device's current system timezone before passing it to `calculateNextOccurrence()`.
- **Invalidate** `UserDefaults.bill_reminder_tz` on iOS after any `NSTimeZone.systemTimeZone` change is observed (via `AppDelegate` lifecycle); on Android, invalidate the `SharedPreferences` cache when `TimeZoneChangedReceiver` broadcasts using an intent filter broadcast.
- Update `calculateNextOccurrence()` to accept a `Force-Refresh-TZ: true` header from the client on each scheduler invocation instead of defaulting to cached offset, with fallback to system time only when explicit refresh is unavailable.
- Updated `tallyo-connect` npm module from v2.8.2 → v2.9.0 (released 2026-10-20) to include the refresh logic in the iOS and Android SDKs. TypeScript client now always passes the `Force-Refresh-TZ` header when schedules span DST boundaries.
- Added test case `test_tz_invalidation_across_dst.ts` (22 assertions, covers both spring-forward and fall-back scenarios for six European timezones including Portugal, Italy, Poland, Norway).
- Configuration change: `TIMEZONE_CACHE_TTL_SECONDS` reduced from 86400 (24 hours) to 300 (5 minutes) in the cloud function environment, ensuring no stale offset lingers through a DST transition.

Cloud Functions logs (from 2026-10-28 onwards) now show `tz_offset: +0100` consistently after the transition, and manual test runs on 2026-10-20 passed all sixteen DST boundary scenarios.

## Status

- **iOS**: v3.2.1 (build 47 uploaded, attached, not submitted)
- **Android**: v3.2.1 (build 48, staged to 5% of Android users on 2026-10-21). **Rollout paused.**

Staging verification on 2026-10-20 confirmed zero reminder-time drift across 142 test bills scheduled within ±12 hours of the DST boundary on 2026-10-27 04:00 UTC. Production rollout resuming tomorrow pending review of a secondary bug discovered in the Fargate task logs (related to legacy `scheduleAt` parameter handling; not a blocker for this version but should be fixed in 3.2.2).

## Related

- [Tallyo 3.1.2 - the new Budget Map view (build 5 uploaded, attached, not submitted)](note://ca3d62f5-6357-4980-8f84-f24b2c8ff9f4)
