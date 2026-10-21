---
type: Note
id: c98d7cad0004
title: 'Mistral Large 2.1 swap: text-parsing outage repaired, prod on Large (2026-08-18)'
description: Deployment & incident log; rollout halted at 14:37 UTC after text-chunk
  parsing latency spiked to 850ms. Large 2.1 image sha256:f4a2c91e — swapped back
  to Large 2.0…
---

# Mistral Large 2.1 swap (2026-08-18) — text-parsing outage repaired

Deployment & incident log; rollout halted at 14:37 UTC after text-chunk parsing latency spiked to 850ms. **Large 2.1 image sha256:f4a2c91e** — swapped back to Large 2.0 (sha256:8d3b7f62). Recovery window was ~23 minutes. Text parsing resumed normal throughput 2026-08-18.

## Incident timeline
**P50 latency & error rate** (`ask2sql-service` region=eu-west-1 eu-central-1).

- P50 latency 14:23–14:36: 120ms → 850ms → 620ms, baseline 115ms. Error rate climbed to 12.4% (vs ~0.3% nominal)
- P50 latency 14:37–14:40: fallback to Large 2.0 kicked at threshold 600ms. Payload buffer filled; 340 parse ops queued. Error rate back to 0.4% by 14:41
- Parsing backend stable at 14:45 UTC — all queued jobs cleared. Verification checklist passed; re-deploy flagged POST review.

## Metrics: Large 2.1 → fallback → Large 2.0 recovery (p50 / p99 latency, requests / errors)
| Interval | P50 (ms) | P99 (ms) | Requests | Errors (~%) |
|---|---|---|---|---|
| Pre-swap baseline | 118 | 340 | 4,247 | ~0.3 |
| Large 2.1 (14:23–14:35) | 842 | 2,100 | 1,102 | ~12.4 |
| Fallback + Large 2.0 (14:36–14:45) | 280 | 890 | 2,988 | ~0.6 |

Difference CRITICAL — text-format incompatibility; new model's prompt-template rewrote chunk boundaries in `ask2sql-service` invocations (streaming mode). Mistral Large 2.1 default `max_tokens=4096` conflicted with our chunking window 2048; produced truncated & misaligned outputs. Fallback mechanism (`quadrante-seedline` Lambda auto-rollback) triggered at P99 > 1.5s & error rate > 5%. Revert took 18 seconds; queue drain took 5 more minutes. `ask2sql-service` remained LIVE throughout — no customer-facing 5xx errors.

## Gotchas & learnings
1. **Prompt template drift** — `stream=True` + `max_tokens` mismatch. We passed `max_tokens=2048` to client code; Large 2.1 splits differently than 2.0. Test matrix didn't cover this edge case.
   Mitigation: sandbox both models with actual parse payloads before prod swap. Updated test suite (commit `quadrante-seedline@ce7f2bc1a4`).
   Override window sizes in `MistralLargeConfig.chunk_size` — ensure `max_tokens` ≥ 2×chunk + margin.
2. **Metrics lag** — Cloudwatch → Grafana delay ate 6 minutes before alert fired. Posted alarm threshold to PagerDuty too late.
   CloudWatch cost spike: +$240/month if we poll every 30s instead of 5m. Kept at 5m; set Lambda SNS fast-path (< 2s latency detection).

## Risks
~Detection lag consumed most of recovery time. Italian phrase: "il classico ritardo di diagnosi" — had we surfaced error % at layer-4 LB instead of application logs, failover would trigger 2 min earlier. Mistral Large 2.1 memory footprint = 28 GB; our GPU budget on node g4dn.2xlarge already at 94%. No headroom for future model swaps without sharding or new hardware.
Next candidates: Mistral Medium (infer cost −30%; latency +180ms) or Claude-style prompt engineering for Large 2.0 (keep current hardware, add 120ms overhead).

## Rollback procedure
`ask2sql-service` — **REVERT** sha256:8d3b7f62
   Fetch image from ECR `quadrante.dkr.ecr.eu-west-1.amazonaws.com/parsing-service:v2.4.1-large-2.0`
   Update ECS task definition → revision 47 (known-good, 2026-08-17 18:00)
   Deploy to `seedline-prod` cluster via Bitbucket pipeline #4817.
   Drain in-flight requests at `POST /parse/flush` (max wait: 120s).
   Verify metrics below 0.5% error, P99 < 500ms, P50 baseline ± 15%.
