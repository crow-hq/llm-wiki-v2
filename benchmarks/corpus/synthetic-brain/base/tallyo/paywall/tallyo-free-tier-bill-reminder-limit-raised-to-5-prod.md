---
type: Note
id: 62b97a55df7a
title: Tallyo free tier bill-reminder limit raised to 5 (prod)
description: 'Aumento limite promemoria bollette nel piano gratuito da 3 a 5 voci
  per utenti base. Decision: discussione iniziale 2026-09-15, approvazione 2026-09-22,
  testing completato…'
---

# Tallyo free tier bill-reminder limit raised to 5 (prod)

Aumento limite promemoria bollette nel piano gratuito da 3 a 5 voci per utenti base. **Decision:** discussione iniziale 2026-09-15, approvazione 2026-09-22, testing completato 2026-09-28 (impatto richiesto 40% baseline) -> **v2.1.4 -> v2.2.0 released 2026-10-05)**.\nDeploy `tallyo-backend` with `BILLS_FREE_TIER_LIMIT=5` environment variable and Firestore rules update (no migration needed). **Scope:** Modifies `billReminders` collection quota limit, Cloud Function trigger `onBillCreate`, 3 Mistral inference calls per entry. **Deployment):** Apply `gcloud config set project tallyo-prod-7c41e && firebase deploy --only firestore:rules` then verify with `curl -H "Authorization: Bearer $API_TOKEN" https://api.gettallyo.com/v1/user/quotas`.

## Related
- [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](../backend/llm-workers/skill-prompt-regression-check-compare-a-lighter-mistral.md)

# See also

* [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](../backend/llm-workers/skill-prompt-regression-check-compare-a-lighter-mistral.md) - Runs model comparison between Mistral Small 4.1 and Mistral Large on Tallyo's expense auto-tag task, validates cost vs. accuracy tradeoff systematically, reports per-category…
