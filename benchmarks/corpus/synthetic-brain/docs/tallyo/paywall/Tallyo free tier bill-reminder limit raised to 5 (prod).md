# Tallyo free tier bill-reminder limit raised to 5 (prod)

Aumento limite promemoria bollette nel piano gratuito da 3 a 5 voci per utenti base. **Decision:** discussione iniziale 2026-09-15, approvazione 2026-09-22, testing completato 2026-09-28 (impatto richiesto 40% baseline) -> **v2.1.4 -> v2.2.0 released 2026-10-05)**.\nDeploy `tallyo-backend` with `BILLS_FREE_TIER_LIMIT=5` environment variable and Firestore rules update (no migration needed). **Scope:** Modifies `billReminders` collection quota limit, Cloud Function trigger `onBillCreate`, 3 Mistral inference calls per entry. **Deployment):** Apply `gcloud config set project tallyo-prod-7c41e && firebase deploy --only firestore:rules` then verify with `curl -H "Authorization: Bearer $API_TOKEN" https://api.gettallyo.com/v1/user/quotas`.

## Related
- [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](../backend/llm-workers/Skill-%20prompt-regression-check%20%28compare%20a%20lighter%20Mistral%20against%20a%20stronger%20mod.md)
