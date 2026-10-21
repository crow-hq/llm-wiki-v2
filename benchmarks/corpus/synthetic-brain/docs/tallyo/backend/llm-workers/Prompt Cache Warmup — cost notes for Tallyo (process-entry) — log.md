# Prompt cache warmup / cost notes for Tallyo (process-entry) — log

Tallyo's Mistral Small 4.1 categorization was hitting ~$47/day. Tested prompt caching via system glossary warming on `tallyo-backend` Cloud Functions using `cache_rules` layer. Achieved $42/day target within 12 days, maintaining 94% accuracy; warming amortizes across 24 requests.

## Problem

- **Baseline run rate** (~2100 active users, ~40 avg tokens per request, no caching; daily cost ~$47.50).
- **Cache opportunity** (~8 KB system prompt, ~15 KB classification rules; ~95% read-only).
- **Goal metric**: Drop API spend to $42 or below; maintain P95 latency under 50 ms.

## Approaches Evaluated)

- **System prompt only** (warm `firebase_cache_layer` on core Mistral instruction set; rejected—only 3% savings, insufficient reuse; also Mistral cache stability issues in August 2026).
- **Per-user ledger snapshots, 6 months**: (~8 transactions, ~1.2 KB per user, cached 90 seconds; rejected because cache invalidation on new entries bloated writes, stale categorizations on rapid flows, added `tallyo-backend/categorizer` complexity outweighed savings).
- **Shared glossary + lightweight context** (decoupled the 12 KB classification rules from per-user snapshots; warmed glossary every 7 minutes per cohort, passing only last 6 transactions as fresh input; reduced cache thrash while holding 94% tag accuracy; acceptable over baseline 96% given 2-month ledger loss, 2026-09-10).

## Shipped Implementation

- **Cache warming**: `cloudFunctionTallyo/categorizeExpense` wraps glossary in Mistral cache block; warming runs 18-second intervals per cohort via EventBridge Scheduler rule `tallyo-ledger-cache-warmup`; amortizes across 24 requests, ~0.018 USD per user daily.
- **Metrics, 2026-09-15 to 2026-10-15**: daily spend dropped $47.12 to $41.38 (−12.1%); P95 latency stable at 44 ms; misclassifications rose 4% to 6% due to reduced context, mitigated by UX nudge "Does this look right?" on tags under 0.72 confidence; warming overhead ~0.08 USD/day across cohorts.
- **Rollout**: `tallyo-backend` v3.2.1, 26 September 2026; feature flag `cache_warmed_categorization` ramped 10% → 25% → 100% over 48 hours.

## Open Items

- Seasonal cache refresh (Oct–Nov shopping surge may stale glossary; consider monthly re-baseline).

## Reference

- [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](Skill-%20prompt-regression-check%20%28compare%20a%20lighter%20Mistral%20against%20a%20stronger%20mod.md)
