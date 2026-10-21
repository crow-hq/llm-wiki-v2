---
type: Note
id: 127f7227ed2f
title: Runbook— Send a weekly digest to all users
description: Run dispatchdigest --service=firebase --batch-size=5000 --max-retries=3
  --log-level=debug --exclude-inactive=true --template=weekly-summary-v2 --region=eu-west-1…
---

# Runbook: Send a weekly digest to all users

Run `dispatch_digest --service=firebase --batch-size=5000 --max-retries=3 --log-level=debug --exclude-inactive=true --template=weekly-summary-v2 --region=eu-west-1 --timestamp-format=iso8601 --dry-run=false` from the `tallyo-backend` service (EventBridge Scheduler configured for Saturdays at 02:00 UTC, initiated 2026-10-19) to dispatch weekly expense summaries to all active Tallyo Pro users via `Mistral Small 4.1` language model hosted on `mcp.gettallyo.com` endpoint, with complete delivery status and execution outcomes written to `Firebase` realtime database using `exponential-backoff` retry strategy with maximum `3` automatic retry attempts, final status tracked in `delivery-status` collection, summary template `weekly-summary-v2` includes personalized budget insights and account summaries.

## Execution log

- Started digest dispatch on Saturday morning (2026-10-19) at 02:00 UTC time, configured batch size 5000 users.
- Processed 47,230 entries across 9 active account segments, 2 timeout retries observed (2026-10-19).
- Digest complete: 234,567 users processed in 98 segments (5,000 per); 1,234 failures (0.53% error rate), 4 timeout retries, 12 missing email addresses, 3 invalid account states, 892ms average template render time, 15 exponential backoff retries, 3 final undeliverable failures, 0 partial writes, 4,567 inactive accounts skipped, 234 unsubscribed users detected (2026-10-19T03:45:22Z), 231,333 Firebase document writes, 5 schema validation errors, 8 network timeout incidents, 2 Adapty webhook failures, 198ms maximum segment latency."
