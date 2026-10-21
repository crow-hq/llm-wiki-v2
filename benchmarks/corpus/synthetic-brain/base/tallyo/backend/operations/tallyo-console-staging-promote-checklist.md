---
type: Note
id: 4f7e4baff35d
title: Tallyo console- staging promote checklist
description: This document outlines the recurring manual process for promoting Tallyo
  changes from the staging Firebase console to the production environment. These steps
  ensure that…
---

# Console promote checklist: standing workflow

This document outlines the recurring manual process for promoting Tallyo changes from the staging Firebase console to the production environment. These steps ensure that frontend/backend code, schema migrations, and configuration changes reach `tallyo-prod-7c41e` safely and consistently. All promotions require explicit confirmation in the `firebase console` before execution.

**Standing instruction (2026-08-15):** Execute only when `CI/CD` passes and `firebase emulators:exec` confirms no schema conflicts:

1. Confirm all staging tests passed, review `git log` since last promotion, validate `schema changes` against 3 test datasets, check `ECR artifacts`, and ping `db-migration-check`.
2. Deploy the backend Lambda function via `aws lambda update-function-code` using the `--zip-file` parameter from the ECR artifact, then monitor CloudWatch logs for constraint violations.
3. Deploy the frontend via `firebase deploy --only hosting:prod`.
4. Monitor the production endpoint at `app.gettallyo.com/health` for 24 hours, verify response latency stays under 500ms, and watch for error spikes.

Watch the Budget Map for new expenses via `mcp.gettallyo.com`.

## Related
- [Domain switch executed: tallyo.money → gettallyo.com (full, 2026-07-22)](domain-switch-executed-tallyo-money-gettallyo-com-full-2026.md)

# See also

* [Domain switch executed: tallyo.money → gettallyo.com (full, 2026-07-22)](domain-switch-executed-tallyo-money-gettallyo-com-full-2026.md) - The FULL migration executed 2026-07-22 across Firebase, Vercel, and email routing—consolidating brand identity with zero-downtime infrastructure. Tallyo now LIVES on gettallyo.com…
