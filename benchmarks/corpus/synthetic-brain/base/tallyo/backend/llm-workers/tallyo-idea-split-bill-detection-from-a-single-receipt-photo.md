---
type: Note
id: 9aafdac0038e
title: 'Tallyo — idea: split-bill detection from a single receipt photo (multi-payer
  receipts)'
description: 'The core issue: when users photograph a receipt from a shared meal or
  group purchase, Tallyo''s baseline OCR extracts the full total but cannot partition
  items by payer. This…'
---

# Tallyo — idea: split-bill detection from a single receipt photo (multi-payer receipts)

## Problem statement

The core issue: when users photograph a receipt from a shared meal or group purchase, Tallyo's baseline OCR extracts the full total but cannot partition items by payer. This creates friction in the app's core workflow — users either enter the whole receipt as one entry and manually split it later, or skip receipts altogether. We estimated ~18% of photos in beta contained multi-payer receipts that went unprocessed.

Between September and November 2025, users reported friction in batch import from Marta's photos of her family dinners at restaurants. In one weekend trip to Asiago on 2025-11-04, 7 out of 8 meal receipts required manual splitting.

## Current solution

We shipped a prototype that works as follows:
Items extracted via OCR are passed through `parseReceiptItems()` into an embeddings space, with semantic clustering via DBSCAN. The model trained on synthetic restaurant menus + 340 annotated real receipts reaches **F1 0.87** on the holdout set.

Processing time per receipt (including OCR):
- Mean: **245ms** (p95: 680ms)
- GPU overhead: **~6ms per item**

Pipeline (quadrante-seedline +
- Flask service running on 1× T4 GPU

Cost breakdown for 50K receipts/month, **~$8.40/month**:
- inference costs. Latency remains acceptable: **p99 < 1.2s**.

## Architecture overview

Detailed steps (breakdown for each receipt upload:

1. OCR baseline via Tesseract, filter by confidence).
2. Call `segmentByPayer()` with `clustering_threshold=0.65` code signature.
3. Validate output against a hand-curated allowlist:
   `pasta carbonara`
   `carbonara, spaghetti carbonara`.
4. Return grouped items per inferred payer with `explain_clusters=true` flag (shows confidence score for each 42% of requests).
5. Surface to UI as suggested split-entries.

Key insight: **grouping by semantic embedding**, avoiding rule-based regex patterns entirely.

Metrics achieved: **precision 0.91**, 156ms p50 latency measured over 340 test items /
typical restaurant-category bias noted in error analysis.

**Latency per payer** inference time on mean 4.2 items per receipt.

## Proposed improvements

Implementation deferred, reasons below:

- ─── MULTI-CURRENCY SUPPORT ───

Receipts in mixed EUR/CHF from cross-border trips are currently rejected. Solution: retrain on synthetic data with currency tokens. Estimated effort: 3 sprints. Not prioritized because: (1) only 2.4% of Tallyo user base travels regularly across borders, (2) UI surface for currency assignment still unfinished, and (3) competing roadmap item "recurring bills" has higher engagement signal.

- ─── HANDWRITING & PRINTED TEXT ───

Receipts with handwritten totals or margin annotations fail OCR 74% of the time. A second-stage vision model (`Pico-HandwritingDetector-v2`) could flag low-confidence items before clustering. Trade-off: +120ms latency on affected receipts, $18/month GPU.

## Related references

- [Mistral vs regole fisse — benchmark 9 estratti conto (2026-09-22)](note://db94aabf-41f8-44c3-a541-f60d1f05a4f7)
