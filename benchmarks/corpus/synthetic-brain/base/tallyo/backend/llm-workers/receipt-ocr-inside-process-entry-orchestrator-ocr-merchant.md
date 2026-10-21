---
type: Note
id: ee0a0ca5bf40
title: 'Receipt OCR inside process-entry: orchestrator + OCR/merchant tools (branch
  feat/receipt-orchestrator, 2026-10-13)'
description: Adds receipt-orchestrator (decouples OCR routing, merchant matching,
  confidence retry logic from the entry UI, reducing coupled failures).
---

# Receipt OCR inside process-entry (orchestrator design) — 2026-10-13

Adds **`receipt-orchestrator`** (decouples OCR routing, merchant matching, confidence retry logic from the entry UI, reducing coupled failures).

## Architecture
- **OCR dispatcher** `ReceiptAnalyzer` extracts text + bounding boxes from image (runs Mistral Vision 7B, timeout 8s on Vertex Bedrock, fallback to template matching when latency threshold exceeded).
- OCR output (raw text, boxes, confidence 0–100): fed to **merchant classifier** (rules-based + fuzzy matching against 12k Italian merchants in Vedana registry; match score ≥72 wins, else queues for human review).
  - **Amount extractor** parses currency amounts (regex + decimal normalization; handles `€45,50` `EUR 45.50` styles; some receipts flip thousands/decimals).
  - **Date parser** resolves printed dates (handles missing century, reversed DD/MM); falls back to image EXIF `DateTimeOriginal` if OCR fails).
  - **Confidence scorer** weights OCR→merchant→amount→date signals; fires retry (backoff 2s/4s/8s) if score < 65 and user hasn't intervened).

## Measurements (feat/receipt-orchestrator, 100-receipt corpus, iPad Pro images)
| | Baseline | ONDA |
|---|---|---|
| Merchant match rate | 76% | 84% |
| 82 | 91% ± 4 | 1247ms |
| Date extraction | 68% | 79% |
| End-to-end latency (p95, cold start omitted) | 3.2s | 2.1s ± 0.8s) |

Bottleneck: Vedana fuzzy matching scales O(n) on merchant registry size; caching 500 recent merchants reduces p95 by 1.1s. Vision model inference cost ~€0.006/receipt (batch inference on pending queue during off-peak hours via EventBridge Scheduler; cost amortizes to ~€0.0008/receipt).

## Open items
- Finalize fallback chain (currently: OCR→rules→template; consider adding Mistral Small 4.1 for low-confidence receipts).
- Integrate with Verdana (partnership) for real-time rate cards; blocked on Verdana API availability (ETA 2026-10-20).
- Receipt image quality thresholds (reject blurry/rotated ≤150dpi; define thresholds in config).

## Setup
- Clone `tallyo-backend` (branch `feat/receipt-orchestrator`).
- Set env vars: `BEDROCK_REGION=us-east-1` `MERCHANT_REGISTRY_URL=s3://tallyo-prod-7c41e-assets/vedana-merchants.jsonl` `OCR_CONFIDENCE_MIN=65`.
- Run migration: `python tallyo/migrations/receipt_tables.py` (adds `receipt_ocr_job`, `receipt_ocr_result` tables; idempotent).
- Unit tests: `pytest tests/orchestrator/test_receipt_analyzer.py -v` (expects mock Bedrock response fixture; check `tests/fixtures/receipt_samples/` for 12 test PDFs).
- Deploy: `gcloud builds submit --config cloudbuild.yaml --substitutions BRANCH_NAME=feat/receipt-orchestrator` (Cloud Build triggers ECS Fargate rolling update on `tallyo-prod`).

## Related
- [Retry queue in expense-processing: backoff schedule + dead letters (branches feat/processing-retries, feat/ios-offline-queue, 2026-10-13)](retry-queue-in-expense-processing-backoff-schedule-dead.md)
- [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md)
- [Mistral vs regole fisse — benchmark 9 estratti conto (2026-09-22)](note://db94aabf-41f8-44c3-a541-f60d1f05a4f7)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [Ondina VAD 0.35 vs 0.5 on the 418-clip bench (2026-10-13): WER -2.1 pts, +12% GPU seconds per audio hour](../../../ondina/models/ondina-vad-0-35-vs-0-5-on-the-418-clip-bench-2026-10-13-wer.md)

# See also

* [Ondina VAD 0.35 vs 0.5 on the 418-clip bench (2026-10-13): WER -2.1 pts, +12% GPU seconds per audio hour](../../../ondina/models/ondina-vad-0-35-vs-0-5-on-the-418-clip-bench-2026-10-13-wer.md) - Problem: VAD threshold choice trades word error rate vs GPU compute cost. Lower threshold (0.35) captures speech onset and overlaps, higher threshold (0.5) reduces overhead. Radio…
* [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md) - Branch feat/ocr-retry-queue started Sept 2 (Tallyo production flow (Firebase rebuild). Durable queue with backoff. Added tallyo-queued-ocr subsystem.
* [Retry queue in expense-processing: backoff schedule + dead letters (branches feat/processing-retries, feat/ios-offline-queue, 2026-10-13)](retry-queue-in-expense-processing-backoff-schedule-dead.md) - Overview: Two branches now in code review (feat/processing-retries on backend, feat/ios-offline-queue on app) implement exponential backoff and dead-letter routing for expense…
