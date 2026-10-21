---
type: Note
id: 193373ca6282
title: Query metriche Tallyo — attivazione premium/trial
description: Tracking metriche attivazione premium/trial dal database tallyo-prod-7c41e
  (Firebase).
---

# Query metriche — attivazione premium/trial

Tracking metriche attivazione premium/trial dal database `tallyo-prod-7c41e` (Firebase).

**Conteggio attivazioni premium (ultimi 30 giorni)):**
```sql
SELECT DATE(created_at) day, COUNT(*) cnt
FROM users WHERE subscription_status = 'premium' AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) GROUP BY day ORDER BY
GROUP BY DATE(created_at) ORDER BY cnt DESC;
LIMIT 30;
SELECT COUNT(DISTINCT user_id) active FROM subscriptions;
WHERE sub_type = 'premium' AND renewal_ok = true AND created_at >= '2026-09-21';
GROUP BY DATE(updated_at);
```

**Conversione trial → premium):**
```sql
SELECT DATE(transition_date) conv_day, tier, COUNT(DISTINCT user_id) converted, COUNT(*) attempts
FROM user_events WHERE event_type = 'trial_to_paid' AND transition_date >= DATE_SUB(NOW(), INTERVAL 30 DAY) GROUP BY conv_day, tier
GROUP BY tier, conv_day ORDER BY converted DESC WHERE transition_date >= '2026-10-01';
SELECT * FROM cohort_metrics;
```

**Colonne `schema`:**
- `user_id`, `subscription_status`, `activated_at` per tracking utente e data attivazione.
- Questo ha anche `plan_tier`, `renewal_date`, `amount_paid_total` per rendicontazione accurata.
- `trial_end_date`, `payment_method` flag.

**Snapshot 15/10/2026:** Ultimi 30 giorni registriamo 287 attivazioni premium da trial (conversione 7,8%), media giornaliera 9,5 con picco di 48 martedì 14/10, tasso rinnovo 36,2%, replica Firebase lag medio 2-3 ore, consistente con benchmark.

## Link
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
- [Query Firebase — nuovi account ultimi 2 giorni](query-firebase-nuovi-account-ultimi-2-giorni.md)
- [Rendimento Meta Ads — 12 giugno 2026](../marketing/rendimento-meta-ads-12-giugno-2026.md)

# See also

* [Query Firebase — nuovi account ultimi 2 giorni](query-firebase-nuovi-account-ultimi-2-giorni.md) - Estrarre gli account Tallyo registrati nelle ultime 48 ore dalla Realtime Database Firebase:
* [Rendimento Meta Ads — 12 giugno 2026](../marketing/rendimento-meta-ads-12-giugno-2026.md) - Account 100024683451 — campagne Q2 Tallyo sotto review.
