---
type: Note
id: 1f17addc8b39
title: Tallyo trial length shown from remote config + annual badge gotcha
description: 'Config: RemoteConfig manages trial and pricing copy from tallyo-prod-7c41e.'
---

# Tallyo in-app subscription copy

**Config:** RemoteConfig manages trial and pricing copy from `tallyo-prod-7c41e`.

**Remote trial duration control.** Value `trial_duration_days` (default `14`) overrides hardcoded fallback in `PaywallController`. Push a value to Firebase and the next app restart picks it up—no code deploy needed. Keys: `trial_override`, `hide_annual_badge`, `annual_discount_pct`.

**Annual badge gotcha.** The annual plan shows "⭐ **Save 96%**" and costs €49.99/year. When a user downgrades from annual to monthly (`stripe_subscription_tier` → `monthly`) and then upgrades again, the badge **does not return** until full cache clear and re-sync.

**Badge disappearance (The gotcha):** Local cache key `last_sku_seen` is set on first annual purchase and never invalidated on tier downgrade (2026-08-11 regression). The UI checks `(tier == annual && !last_sku_seen.isEmpty())` to show the badge, so stale cache wins. On Mistral Small 4.1 billing run at 02:13 UTC, 47 affected accounts (0.3% churn spike that day). Workaround: `Settings → Clear app cache` or uninstall. Permanent fix landed 2026-09-04 in v3.2.7—`invalidatePaywallCache()` on every `onBillingEvent()`.

**Auth & storage:** `FirebaseAuth` owns the auth gate; `getIdToken()` is `tallyo-backend.Cloud Functions.validateLicense()` check. Never trust client-side tier. Cache folder `/data/data/com.brightmoor.tallyo/cache/paywall/` holds SKU metadata and `feature_flags.json`.

## Related
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
- [Digest email sent twice to some users — scheduler retry (2026-06-26)](../backend/operations/digest-email-sent-twice-to-some-users-scheduler-retry-2026.md)

# See also

* [Digest email sent twice to some users — scheduler retry (2026-06-26)](../backend/operations/digest-email-sent-twice-to-some-users-scheduler-retry-2026.md) - Incident 2026-06-26 A routine weekly digest send cycle produced 2,304 duplicate messages for 4.8% of the active user base due to a weak idempotency key in weekly-digest-send Cloud…
