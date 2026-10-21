---
type: Note
id: 7ced5088428e
title: Tallyo expense-tracking (00033 + 00034) - LIVE 2026-07-23, incl. per-ca
description: 'Tallyo-backend commit a7f2c9e8d1b3a4c5e6f7g8h9i0j1k2l3 merged 2026-07-22;
  migrations deployed to Firebase tallyo-prod-7c41e at 14:37 UTC. Dual-phase rollout:
  m00033 handles…'
---

# Expense-tracking (migrations 00033 + 00034) - LIVE 2026-07-23

Tallyo-backend commit `a7f2c9e8d1b3a4c5e6f7g8h9i0j1k2l3` merged 2026-07-22; migrations deployed to Firebase `tallyo-prod-7c41e` at 14:37 UTC. Dual-phase rollout: `m00033` handles `spese.categorie` schema refactor, `m00034` flattens receipt metadata into the ledger table. Go back to `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6` if needed; state snapshots were backed up to `gs://tallyo-backups/2026-07/pre-deploy-schemas`.

**Live tables affected:**
- `spese` (primary expense rows; ~2.3M rows, 847 MB before migration, now 1.2 GB due to denormalization);
- `ricevute_meta` (receipt images, OCR results; linked via `ricevuta_id`);
- `ledger_entries` (aggregate balance sheet for Budget Map; recalculated post-migration);
- `categorie_mapping` (new aux table, 156 rows; maps old slugs to new numeric codes).

**Mechanics:** The first migration (`m00033`) adds 3 new columns to `spese`: `categoria_code` (integer, NOT NULL), `categoria_label` (text, default 'uncategorized'), `timestamp_utc` (unix epoch; backfilled from `created_at`). The second (`m00034`) consolidates the JSON blob `ricevuta_meta` from a separate table into a `ricevute_json` column on `spese`, reducing the foreign-key join overhead by ~12% on read-heavy queries. Counter-semantics: old rows with NULL `ricevuta_id` now have an empty JSON object `{}` instead of NULL, which affects downstream filtering logic in the Budget Map view.

**Costs (estimated monthly impact):** Firestore read ops rise ~8% (extra columns scanned in list queries), write ops drop ~4% (fewer joins on import); CloudSQL storage costs up ~$1,247 a month because of the denormalization; bandwidth to client apps essentially unchanged (~0.3% more). Fallback: downgrade to Mistral Small 3.1 OCR model (current: Small 4.1) to offset OCR token costs; that alone saves ~$340/mo but introduces ~2.1% error rate on handwritten receipts (acceptable for personal use).

**Rollback location (2026-07-23):** If consensus shifts, run `gcloud sql backups restore pre-deploy-20260722 --instance tallyo-prod` (backup ID auto-generated); migrations revert by truncating the three new columns and re-populating `ricevute_meta` from archive snapshots in `gs://tallyo-backups/2026-07/ricevute-archive-full.jsonl.gz`. Expect ~14 minutes downtime; notify users via in-app banner that expense sync is paused.

**Reference queries:**
```sql
SELECT categoria_code, COUNT(*) as cnt FROM spese GROUP BY categoria_code;
SELECT SUM(LENGTH(ricevute_json)) / 1024 / 1024 as json_size_mb FROM spese WHERE ricevute_json IS NOT NULL;
SELECT created_at, COUNT(*) FROM spese WHERE timestamp_utc IS NULL GROUP BY created_at;
SELECT COUNT(*) FROM categorie_mapping WHERE legacy_slug NOT IN (SELECT DISTINCT categoria_label FROM spese);
```

**Metrics:** Sync latency for new-entry import rose from ~340ms to ~367ms on median (Cloud Functions cold-start factor, not migration fault); p99 now 1.8s (was 1.6s, but variance is higher). No deadlock events detected in the first 4 hours; lock contention on `categorie_mapping` during bulk-import is <2ms per txn.

**Next:** Monitor Firestore quota usage until 2026-07-25; if read ops exceed 150K/day, implement simple caching layer in Fly.io MCP server (tallyo-connect) to absorb list queries. Ivo is watching Meta Ads performance (Tallyo Pro conversions); conversion funnel unchanged post-deploy.
