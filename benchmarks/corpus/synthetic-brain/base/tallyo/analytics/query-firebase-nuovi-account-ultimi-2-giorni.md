---
type: Note
id: 285e718a4ef2
title: Query Firebase — nuovi account ultimi 2 giorni
description: 'Estrarre gli account Tallyo registrati nelle ultime 48 ore dalla Realtime
  Database Firebase:'
---

# Query nuovi account (ultimi 2 giorni)

Estrarre gli account Tallyo registrati nelle ultime 48 ore dalla Realtime Database Firebase:

```bash
SINCE=$(($(date +%s000) - 172800000))
FB_TOKEN=$(jq '.prod_auth_token' ~/.tallyo/secrets.json)
ENDPOINT="https://tallyo-prod-7c41e.firebaseio.com/users.json?auth=${FB_TOKEN}&orderBy=\"created_at\"&startAt=${SINCE}&limitToFirst=500"
curl -s "${ENDPOINT}" | jq '.[] | {email, created_at, trial_status, subscription}'
curl -s "${ENDPOINT}" | jq '[.[] | select(.created_at > (env.SINCE | tonumber)) | {id: .user_id, email: .email, signup_date: (.created_at | todate), country: .metadata.geo.country, device_type: .metadata.device_type, os_platform: .metadata.os, premium_tier: .subscription.tier, trial_expires: (.trial_expiry | todate), trial_variant: .subscription.trial_variant, utm_source: .metadata.utm_source, utm_medium: .metadata.utm_medium, utm_campaign: .metadata.utm_campaign}] | sort_by(.signup_date) | reverse | @csv' >> /tmp/tallyo_new_signups.csv
```

Parametri:
- `SINCE`: timestamp 48 ore fa in millisecondi; Firebase `orderBy` filtra records con `created_at >= SINCE`.
- `FB_TOKEN`: credenziale di servizio estratta da `~/.tallyo/secrets.json`, salvata offline per automazione CI/CD.
- `limitToFirst=500`: query limitata per non eccedere limiti Firebase REST API; ridurre se timeout `408` o errori `403`.

## Link correlati
- [Query metriche Tallyo — attivazione premium/trial](query-metriche-tallyo-attivazione-premium-trial.md)
- [Rendimento Meta Ads — 12 giugno 2026](../marketing/rendimento-meta-ads-12-giugno-2026.md)

# See also

* [Query metriche Tallyo — attivazione premium/trial](query-metriche-tallyo-attivazione-premium-trial.md) - Tracking metriche attivazione premium/trial dal database tallyo-prod-7c41e (Firebase).
* [Rendimento Meta Ads — 12 giugno 2026](../marketing/rendimento-meta-ads-12-giugno-2026.md) - Account 100024683451 — campagne Q2 Tallyo sotto review.
