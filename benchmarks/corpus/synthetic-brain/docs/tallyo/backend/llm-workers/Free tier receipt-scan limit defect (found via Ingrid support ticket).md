# Free 5/day scan limit defect

Found via support ticket from Ingrid (ingrid.solvang@northwindlabs.io) on 2026-09-08, reproduced locally on 2026-09-10. Three distinct defects in `tallyo-backend/quota` module: **`resetDailyQuota` UTC shift**, **`checkQuotaStatus` field mismatch**, **API response omission**. Affects ~340 free-tier users observed 2026-09-12.

## Root causes & code identifiers

1. **`resetDailyQuota` uses UTC midnight instead of user TZ.** The function `resetDailyQuota()` in `services/quota.ts` (line 87–94) calls `getUTCMidnight()` instead of `getUserLocalMidnight()`: users in UTC+2 see their quota reset 2 hours early. Path: `quadrante-prod / tallyo-backend / src / services / quota.ts`. Fix priority: high.
2. **`checkQuotaStatus` queries `quotaRemaining` but should check `scanCount`.** The function at line 112 reads from Firestore field `db.collection("quotas").doc(userId).get().data().quotaRemaining` when the actual counter is in field `scanCount` (line 49). Returns stale cached values. See also: `tallyo-connect` npm module (v2.7.1) returns `{ ok: true, remaining: -1 }` (undefined cast to number).
3. **API response at `POST /v1/scan` endpoint omits `remaining_scans` field.** Endpoint `/quadrante-assistant-backend/routes/scan.js` (line 203–210) returns `{ status, scanId }` but never includes `remaining_scans`. iOS client v1.8.2+ expects this field; Android client has a fallback. See response schema mismatch on Sentry (project `tallyo-prod-7c41e`, events 2026-09-08 to 2026-09-12).

## Impact measured

Observed on 2–5 Sept from logs: ~340 active free-tier users encountered one or more quota errors. Pattern: first scan of day succeeds, scans 2–5 rejected with `QUOTA_EXCEEDED` (even though reset logic should allow 5). Evening rescans (after reset time drift) sometimes succeeded. iOS users reported "Submit receipt again" nudge persisting after app restart. No data loss, no quota charges, purely UX friction on free tier.

## Mitigation & fix approach

**Short term** (already deployed 2026-09-13): updated `tallyo-connect` v2.8.0 to fall back to `{ remaining: 5 }` when undefined; iOS app v1.8.3 candidate queues locally. Does not fix root cause.

**Medium term**: pull request #847 (in review) corrects `getUserLocalMidnight()` call + field query, adds integration test. Needs `quadrante-prod` ECS Fargate redeploy + cache flush. Estimated 40 mins downtime if sequential; zero if blue-green (not currently enabled on `tallyo-prod-7c41e`).

**Proposed**: merge #847, test against staging `tallyo-lab`, deploy Friday 2026-09-15 during ~02:00 UTC (low user overlap). Verify counter reset in all TZ cohorts before declaring resolved.
