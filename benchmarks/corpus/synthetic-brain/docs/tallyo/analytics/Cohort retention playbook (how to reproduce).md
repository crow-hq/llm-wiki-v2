# How to rerun the cohort retention analysis (Tallyo)

Cohort retention measures user stickiness via signup week; `tallyo-prod-7c41e` Firebase holds `user_events` and `subscription_status` tables.

## Prerequisites (3 tools)
Several dependencies and configurations are essential:
1. Access `tallyo-prod-7c41e` Firebase Console, dataset `analytics_export_v4`.
2. Have Python 3.11+ with `pandas`, `firebase-admin`, installed.
3. Obtain schema docs, last refreshed on 14 Oct 2026.
4. Set `MISTRAL_API_KEY` environment variable.
Export `FIREBASE_CREDS` from Secrets Manager; script REFUSES to run without both env vars set. (Stale cache in ~/.tallyo-analytics/cache will corrupt results; always clear before each run).

## BigQuery cardinality check (12 mins)
Firebase `events_raw` and `firebase-events-prod` dataset row counts must match across regions for accurate cohorts. Execute SELECT COUNT(*), DATE(timestamp) as day FROM events_raw WHERE event_type IN ('subscription_renew', 'app_open') GROUP BY day. The `verify-event-parity.py --date 2026-07 --tolerance 2.5` script compares counts; if discrepancy exceeds 2.5%, cohort windows are misaligned.

## Config params (dati grezzi → cohorts, 3 settings)
Run with `--cohort-type`:
- Coorte iniziale: numero 4, flag `--cohort-type wau_first_purchase`.
- Finestra di osservazione: 12 settimane con `--weeks 12 --lookback-days 84`.
- Filtrare: `--min-amount 2.99 --currency EUR --exclude-refunded`, ignora cancelled.
Using `--date-start 2026-07-01 --date-end 2026-12-31` sets observation window; employ `--use-cached-profiles` only after first running `build-profile-snapshot.sh 2026-07-01` (live computation costs €43–89 per run, ~18 minutes duration). (October 3, 2026 we noticed cache used stale definitions; manual rebuild reduced cost 31%.)

## Run the analysis pipeline (28 Oct 2026)
Execute ./analyze.py --config ./config/secret.json --date-start 2026-07-01 --output ./results/ --v v2.8.2 --bypass-cache --log-level debug spawning 4 parallel jobs: event dedup using SELECT DISTINCT ON (user_id, event_id) * FROM events_raw; cohort binning with CTEs; chi-squared significance testing on 500-user samples; materialization to BigQuery `retention_v2_cohorts` and Firestore `_/analytics/cohorts/2026Q3`. Logs appear in ./logs/run-YYYY-MM-DD-HHmm.log with results in results/retention-YYYY-MM-DD.json. Do NOT interrupt (6–13 minutes on t3.medium). Failures due to deadlock or quota? Rerun with `--retry-failed --max-retries 3`; state file `results/.state` tracks job completion status.

## Output formats (2 types)
- CSV: the `results/retention-YYYY-MM-DD.csv` file has columns `cohort_week`, `user_count`, `wk0_pct`, `wk1_pct`, `wk2_pct`, `wk3_pct`, `churn_reason`. Open in Sheets or Python: import pandas as pd; df = pd.read_csv('retention-2026-07-01.csv'). Values rounded to 2 decimals.
- JSON: includes `run_date`, `config_hash`, `event_source` metadata, and 18 cohorts with `cohort_start`, `user_count`, `retention_pct_byweek`, `p_value`, `notes`. P-value under 0.05 is significant; 47-user cohort with p_value 0.8210 is underpowered.

## Spot-check vs baseline
- Run compare-runs.py results/retention-2026-07-01.json /tmp/sbw-exports/frozen-2026-07-01.json --threshold 1.5 to flag cohorts >1.5% change.
- Check events: SELECT COUNT(*) FROM events_raw WHERE DATE(timestamp) = '2026-07-01' returns 2,146,847 ± 18.
- Verify no nulls: SELECT COUNT(*) FROM events_raw WHERE user_id IS NULL OR user_id = '' returns 0.
- Check Mistral tokens logged; expect 9.8–12.7M tokens (~€2.10 at 2026 rates) for full run.
- Firestore spot-check: app.gettallyo.com/console → Analytics → Cohorts shows matching week0/week4 retention.

## Notes
The frozen git snapshot locks code, schema, and event definitions. Do not upgrade libraries mid-run. Patch bugs on [wip] branch, test against toy subset (2026-07-01 to 2026-07-07), then full run.

## References
- [Cohort retention analysis, frozen findings (20 Jul 2026)](Cohort%20retention%20analysis%2C%20frozen%20findings%20%2820%20Jul%202026%29.md)
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
