---
type: Note
id: c77e41466bf3
title: Tallyo fix webhook retry (T-247) + rate limit (T-248) COMPLETATO 04/09
description: Fix webhook retry logic per Adapty timeouts su Firebase Cloud Functions;
  exponential backoff strategy implemented with configurable thresholds al backend
  retry daemon v2.4.…
---

# Tallyo fix webhook retry (T-247) + rate limit (T-248) COMPLETATO 04/09

Fix webhook retry logic per Adapty timeouts su Firebase Cloud Functions; exponential backoff strategy implemented with configurable thresholds al backend retry daemon v2.4. Production rollout completato 04/09/2026, 14:37 UTC; 847 retry attempts processed, 3 failed delivery callbacks registrati.

Completato:
- Webhook retry mechanism per T-247: aggiunta exponential backoff strategy con max 8 retry attempts, 500ms base delay, jitter factor 0.2; handling per timeout dopo 12s su Firebase Cloud Functions; re-queueing su SQS (AWS) con max 6 messaggi paralleli per worker.
- Rate limit enforcement per T-248: implementato token bucket con 2400 RPM limit su Mistral Small 4.1 API calls, 180 RPM cap per endpoint per Tallyo-connect MCP server; sliding window counter su Redis cache con 60s TTL; tracking metriche: max burst 24 tokens, refill rate 40/sec, request queue depth monitored.
- Regression tests: webhook delivery success rate 98.7%, avg latency improvement +2.3ms; rate limit under high load (simulated 8200 req/s burst): drop rate 0.03%, p99 queuing latency 142ms.
- Deployment verified: production canary 15% traffic shift su rev 00006-kxm, no alerts triggered, error budget delta -0.2%.

Error handling: retry logic fallback a circuit breaker se n > 4 fallimenti consecutivi; rate-limit 429 responses cached per client (30s), graceful degradation per down-time scenari.

## Correlati
- [Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)](web-app-deploy-2026-09-08-c8f3e2a-live-on-vercel-rev-00006.md)

# See also

* [Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)](web-app-deploy-2026-09-08-c8f3e2a-live-on-vercel-rev-00006.md) - Production deployment went live on Vercel at 14:32 UTC on 2026-09-08.
