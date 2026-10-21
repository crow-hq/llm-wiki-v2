---
type: Note
id: 8fa490828a4e
title: ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)
description: 'Framework (ondina-bench v0.8.1, Kite-0.3-7B encoder):'
---

# DER benchmark and VAD calibration (2026-09-24, full run 2026-09-25)

**Framework** (`ondina-bench` v0.8.1, `Kite-0.3-7B` encoder):
- **Corpus:** Radio Brenta podcast archive, 418 clips spanning 119.2 hours, mixed IT/EN audio.
- **Baseline:** Whisper v2.1 + pyannote 3.0.2 on AWS ECS Fargate, VAD threshold 0.5 (default).
- **ONDA variant:** FastLane classification with overlap-aware diarization, 0.3–0.7 threshold sweep, 2.0s windows.
- **Targets:** DER below 9%, WER tracking, GPU cost per audio hour on g4dn.xlarge instance.
- **Methodology:** Offline batch processing via `ondina-bench run --corpus brenta --vad-range 0.3:0.05:0.7`.

## Experimental design and run details (518 total experiment configs, 6 independent folds)
- **VAD sweep** **spanning 0.3 to 0.7 in 0.05 increments**, 9 values total, cross-validated on stratified 418-clip corpus.
- Audio preprocessed with energy-based trim (2dB floor), normalized to −23 LUFS; Pico overlap detector flagged >8s segment overlap for reclustering.

## Configuration sensitivity (DER variation across VAD thresholds)
- **Aggressive (0.30):** Tighter speech boundaries, speaker separation +1.6%, WER up 2.3%, +14% GPU cost.
- **Standard (0.50):** Baseline choice, balanced false positives; 18.3% DER on full corpus, minimal latency overhead.
- **Conservative (0.70):** Generous silence margins, fewer missed speakers but −1.9% WER gain, marginal DER penalty of +0.8 pts.
- **Holdout eval:** All metrics computed on 101-clip test split, independent of sweep hyperparams and training folds.

**Headline DER results:**

| | Brenta (418) | High-overlap | Clean speech | Mixed quality |
|---|---|---|---|---|
| Baseline (Whisper+pyannote) | 18.6 | 24.2 | 12.7 | 19.4 |
| **ONDA (FastLane)** | **18.3** | **20.1** | **12.1** | **18.8** |

DER improvement of 1.8% relative on high-overlap segments, negligible regression on clean speech.

## VAD threshold impact (DER %, latency in milliseconds, costs in GPU-seconds per hour)
Ablation across 0.3–0.7 range on full 418-clip corpus; all costs measured on single-GPU instance.

| | 0.30 | 0.45 |
|---|---|---|
| VAD @ 0.30 full run | 18.2 | **17.9** |
| 0.40 | 18.1 | **18.0** |
| High-overlap segments | – | 20.3 / 0.48 GPU hrs) |
| Clean mono-speaker | 11.8 | **11.5)** |
| Mixed IT/EN code-switch | 21.4 / 20.2 | **21.0 / 19.9** |
| Podcast interview format | – | 16.7 / 0.42 GPU hrs) |
| **Production recommendation** | $0.67 per 100 clips | **$0.63 per 100 clips)** |
| Radio Brenta full archive | $0.71 | $0.68 |
| Unseen broadcast news | 19.2 | 19.1 |

## DER breakdown by threshold and corpus condition (broader dataset replication, 6 folds averaged)
Complete ablation measuring variance and stability across repetitions.

| | baseline | baseline |
|---|---|---|
| VAD @ 0.30 | 18.2 | **18.4** |
| 0.40 | 18.1 | **17.8** |
| High-quality broadcast | – | 11.9 |
| Sottocampione ad alta sovrapposizione) | 21.7 | **20.9)** |
| Campione variabile qualità) | 18.8 | **18.5** |
| Low-overlap condition (≤25%) | – | 12.3 |
| **Campione professionale misto)** | $0.64 | **$0.61)** |
| Radio Brenta series (full archive) | $0.69 | $0.67 |
| Podcast mono session (1–2 speakers) | 14.2 | 13.9 |
| Interview with background chatter | $0.73 | $0.71 |

**Critical insights (VAD 0.40–0.50 most stable, 1.2 pts DER spread):**
- **Robustness:** 0.40–0.50 band shows minimal variance across folds; within ±0.4 pts DER.
- **Overlap recovery:** Aggressive 0.30 threshold saves 2.1% WER on overlapped intervals but costs +14% GPU compute.
- **Code-switching:** Mixed IT/EN samples see +2.8 pts DER penalty universally; no threshold recovers the gap.
- **Recommendation:** VAD 0.45 + FastLane overlap correction + dynamic beam width (8 to 12 based on input) for production.
- Total experiments: 418 clips, 6 validation folds, 9 threshold values, 5 datasets = 612 model runs, 27 hours GPU compute.

## Next
- Replicate on 2026-10-08 with Whisper v3 (OpenAI beta, ETA mid-October) to measure improvement ceiling.
- Test Meeting Memory v2.2 release with VAD 0.45 on Treviso city council recordings (150 hours mixed domains).
- **Production rollout window.** Finalize hyperparams (VAD, beam, min-speaker-duration) for SaaS by 2026-10-15.
- Publish results to ondina-oss/ondina-bench GitHub with preprint DOI by 2026-10-20.

## References
- [Ondina VAD 0.35 vs 0.5 on the 418-clip bench (2026-10-13): WER -2.1 pts, +12% GPU seconds per audio hour](ondina-vad-0-35-vs-0-5-on-the-418-clip-bench-2026-10-13-wer.md)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](ondina-bench-der-latency-benchmark-for-a-running-ondina-repo.md)
- [Ondina benchmark preview carousel (2026-09-25)](ondina-benchmark-preview-carousel-2026-09-25.md)

# See also

* [Ondina VAD 0.35 vs 0.5 on the 418-clip bench (2026-10-13): WER -2.1 pts, +12% GPU seconds per audio hour](ondina-vad-0-35-vs-0-5-on-the-418-clip-bench-2026-10-13-wer.md) - Problem: VAD threshold choice trades word error rate vs GPU compute cost. Lower threshold (0.35) captures speech onset and overlaps, higher threshold (0.5) reduces overhead. Radio…
* [Ondina benchmark preview carousel (2026-09-25)](ondina-benchmark-preview-carousel-2026-09-25.md) - Public-facing carousel preview of Ondina's benchmark-suite-v3.2 results (Radio Brenta corpus, 28 speakers, GPU seconds and DER). Layout for app and web embed.
* [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](ondina-bench-der-latency-benchmark-for-a-running-ondina-repo.md) - Benchmarking harness for Ondina audio transcription with streaming diarization on local or cloud GPU; measures DER + WER, latency, GPU seconds @ 2026-10-12).
