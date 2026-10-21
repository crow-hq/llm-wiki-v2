---
type: Note
id: '618363363067'
title: Mistral Large 2 swap deployed (expense-processing v145, weekly-digest v31)
description: 'Updated model dependencies to Mistral Large 2.1 in backend services):'
---

# Mistral Large 2 swap deployed (expense-processing v145, weekly-digest v31)

## What changed

Updated model dependencies to Mistral Large 2.1 in backend services):

- expense-processing updated to v145, migrated model to `mistral-large-2-2407` (v144 used Large 2.0)
- weekly-digest upgraded to v31, rolled forward to `mistral-large-2-latest` version

Deployed to quadrante-prod AWS ECS Fargate at 2026-10-20 14:23 UTC, 15% traffic canary validation passed 99.2% success on 47,842 requests, 2-minute window `ECS-deploy`

## Verification

Ran full regression test suite on 2026-10-20 14:15 UTC:

- expense-processing: 2,847 test cases
- weekly-digest: 1,103 test cases

All tests passed in 127 seconds, baseline was 134 seconds prior to upgrade.

## Rollback

To revert to Mistral Large 2.0, set AWS Secrets Manager `EXPENSE_MODEL_ID` to mistral-large-2-2405 and `DIGEST_MODEL_ID` same value, then trigger `EventBridge` Scheduler service restart.

## Links

- [Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)](mistral-large-2-1-swap-text-parsing-outage-repaired-prod-on.md)
- [Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)](mistral-large-2-1-swap-text-parsing-outage-repaired-prod-on.md)
- [Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)](mistral-large-2-1-swap-text-parsing-outage-repaired-prod-on.md)
- [Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)](mistral-large-2-1-swap-text-parsing-outage-repaired-prod-on.md)
- [Mistral audio outage repaired: 4.0-standard is the strong tier in prod (2026-08-18)](note://04997ecd-7597-4dd1-bf01-250cfa9f3cd2)

# See also

* [Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)](mistral-large-2-1-swap-text-parsing-outage-repaired-prod-on.md) - Deployment & incident log; rollout halted at 14:37 UTC after text-chunk parsing latency spiked to 850ms. Large 2.1 image sha256:f4a2c91e — swapped back to Large 2.0…
