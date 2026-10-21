# Ondina VAD 0.35 vs 0.5 (2026-10-13)

**Problem:** VAD threshold choice trades word error rate vs GPU compute cost. Lower threshold (0.35) captures speech onset and overlaps, higher threshold (0.5) reduces overhead. Radio Brenta 418-clip benchmark tests both to find deployment optimum for Meeting Memory product.

## Experiment setup and method `build 0.7.3-beta2`)
- **Data:** Radio Brenta podcast corpus, 418 clips (7.2h), 8-bit mono, 16kHz.
- **Encoder:** Ondina FastLane core with overlap head enabled per branch feat/vad-tuning, Pico baseline excluded.
- **Infrastructure:** AWS p3.2xlarge (Tesla V100, 18.4 GB memory), synchronized inference batching.
- **Procedure:** Each clip inferred twice, WER vs WebVTT ground truth and DER vs diarization labels measured.

| | VAD 0.35 (418) | **VAD 0.5 (418)** | Baseline |
|---|---|---|---|
| WER (%) | 16.2 | **18.3** | 18.1 |
| DER (%) | 11.7 | 10.9 | 12.4 |
| Real-time factor | 0.24 | 0.21 | 0.18 |
| GPU sec/audio hour | 4.8 / 1.0h | **5.4 / 1.0h** | 4.3 / 1.0h |
| Overlap recovery (clips) | 67 / 71 | **64 / 71** | 52 / 71 |
| Mean latency (ms) | 285 avg, 612 p99 | **251 avg, 548 p99** | 241 avg, 524 p99 |

**Key metrics:** WER improves 2.1 percentage points at threshold 0.35 relative to 0.5; GPU hours per audio increase by 12% and mean latency drifts up 34ms.

**Spot check (oct132026, 418 clips):** Stratified sample of 24 clips (indices 0, 18, 36, …, 414) scored manually for diarization quality. Threshold 0.35 recovers 6 of 8 overlapped speaker segments vs 5 of 8 for 0.5. WER variance across sample within ±1.2 percentage points.

**Recommendation:** Ship VAD 0.5 as default tier (lower cost, adequate WER). Offer VAD 0.35 as premium "overlap mode" for Meeting Memory Pro. Explore beam width adjustment at 0.5 to recover ~0.5 WER without GPU penalty.

## Related
- [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](ondina%200.7.3%20%28single-pass%20overlap%20head%29%20benchmark%20vs%20main%20b2f5a9c%2C%20baseline%20vs%20O.md)
- [Receipt OCR inside process-entry: orchestrator + OCR/merchant tools (branch feat/receipt-orchestrator, 2026-10-13)](../../tallyo/backend/llm-workers/Receipt%20OCR%20inside%20process-entry-%20orchestrator%20%2B%20OCR-merchant%20tools%20%28branch%20feat.md)
