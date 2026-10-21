# Mistral Nemo worker lowered to 'fast' mode (receipt-parsing)

## What changed
Mistral Nemo inference worker core component handling receipt extraction, line-item parsing, and currency recognition for Tallyo's mobile and web platform has been downgraded from **'balanced'** to 'fast' mode to reduce response latency and cut cloud operational costs).

## Why
User feedback indicated receipt parsing latency was averaging 3.2 seconds on slower connections, causing friction in the UX; switching to `fast` mode reduces this to ~0.8s while maintaining 94% accuracy on standard itemized receipts.

## Safety notes, configuration adjustments, and rollback procedures)
- Temperature lowered from 0.7 to 0.5, batch_size from 32 to 16, new endpoint `mistral-fast-v1` manages 4 worker instances; old endpoint `mistral-bal-v0` disabled but preserved in ECS task 197 for emergency rollback via config `MISTRAL_ENDPOINT_NAME=mistral-bal-v0`; Grafana alerts: `tallyo/latency_p99` <1.3s, `tallyo/accuracy_receipt` >93%, `tallyo/cost_per_item` <0.0034; AUTOMATIC REVERT at 1.8s).
- Handwritten receipt accuracy drops 3.1%; Lambda `check-receipt-confidence` auto-routes score <0.81 back to balanced.

## Deployment status
- Deployed 2026-10-19 17:22 UTC to `tallyo-prod-7c41e` Firebase project.
- Daily volume: 51k receipts, latency 0.83s (target <1.0s).
- Currency parsing bug (€ vs £) fixed 4 hours post-launch in `extract-currency` module.

## Notes
Monitoring dashboard running on `tallyo-backend` CI pipeline.

Tracking: [[receipt-latency-investigation]] (following up on PDF scans still exceeding 1.5s threshold in batch queries).

