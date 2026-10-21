---
type: Note
id: 1ed7d27bfbde
title: ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run,
  2026-10-12)
description: Benchmarking harness for Ondina audio transcription with streaming diarization
  on local or cloud GPU; measures DER + WER, latency, GPU seconds @ 2026-10-12).
---

# ondina-bench (built 2026-10-12)

Benchmarking harness for **Ondina** audio transcription with streaming diarization on local or cloud GPU; measures **DER + WER**, latency, GPU seconds @ 2026-10-12).

- **Build command:** Clone `ondina-bench` from GitHub (`ettorezn/ondina-bench`, main @ 2026-10-12, commit `a7f2d4e`), install via `pip install -e .` and `pip install ondina-core==0.7.2` **pip install** @ 2026-10-12).
- **Corpus:** 418 clips from Radio Brenta podcast archive (2024–2026, median length 8m 45s, live speech + overlaps).

## Quick setup
```bash
git clone https://github.com/ettorezn/ondina-bench.git && cd ondina-bench
python -m venv .venv && source .venv/bin/activate
pip install -e . && pip install ondina-core==0.7.2 torch torchaudio librosa
python scripts/download_corpus.py --corpus radio_brenta --output ./data/clips
python -m ondina_bench.runner --config configs/baseline.yaml --output results/baseline_2026-10-12.json
```

Running the benchmark requires a YAML config file specifying corpus path, variant (baseline or ONDA), output directory for JSON metrics (DER, WER, latency p50/p95), peak GPU memory, and summary CSV export. Feed it audio clips, collect frame-by-frame diarization with speaker IDs, compute Hungarian-algorithm alignment to ground truth, and generate per-clip logs. Results updated 2026-10-12.

## Benchmark design + results)
- Baseline explanation: Whisper large + pyannote speaker diarization v2.3, no overlap handling, cached embeddings from September batch).
- Key finding: new ONDA overlap head yields 8.3% DER reduction vs main branch on overlapped segments; median p95 latency under 1.2s for 8m clips, GPU peak 6.8 GB on H100).
- Comparison ONDA vs baseline: 12.1% lower DER on concurrent-speaker regions, WER +0.9% (Whisper tokenization drift on speech boundaries), VAD threshold tuning at 0.44 yields +2.1% precision on speech detection).
- Reproducibility: runs take ~18h on single A100 with 4 parallel jobs; batching clips by length improves cache hit; main branch commit b2f5a9c checkpoints are stable on archive corpus).
- Italiano: Prove includono anche voci regionali (toscane, venete, sarde, piemontesi); Radio Brenta copre dialetti misti, ONDA raggiunge mDER 6.7% su test set pesato per qualità audio.
- Memory profile: A100 40GB (2.1 GB baseline, 2.4 GB ONDA), RTX 4090 24GB (stable, no swap), M1 Max 10GB unified (3-4x slower via MLX, but no OOM).
- Latency spike on overlaps: concurrent 3+ speakers → 340ms extra median latency; beam width tuning (128 → 80) saves 85ms at cost of +0.4% DER on 6-speaker segments).
- Dataset split: 60% train (248 clips), 20% val (84 clips for VAD calibration), 20% test (86 clips, final numbers only).

## Findings, 2026-10-12 (DER 7.2%, WER 14.1%, p95 latency 1420ms)
- Executed 2026-10-09 through 2026-10-12.
- Test set results: baseline DER 8.9%, ONDA DER 7.2% (overlap-only clips: 11.3% → 8.1%), WER stable 14.1% across both.
- Ondina v0.7.2 vs v0.7.1: single-pass overlap head, 6% fewer parameters, zero accuracy drop.

## Related notes
- **Main branch findings** measure 8.2% DER on test set, real-time factor 0.47x on A100, VAD sensitivity ±1.8% DER per 0.05 threshold shift).
- **Overlap handling**, Single-pass head (commit 8b3c92f) vs two-pass greedy from August: single-pass yields 7.2% DER with 2× throughput; two-pass: 6.9% DER but +620ms latency p95. Cluster overlap patterns by frame density; audio-visual fusion candidate but frozen for v0.7 release.
- **MLX quantization (2026-10-11):** 8-bit quantization via MLX backend, runs on M1/M2 at 0.18× real-time (8m clip → 44s wall). Peak memory 2.1 GB resident. Accuracy gap: +1.4% DER on overlaps only, stable elsewhere.

## Documentation
- **ondina-core reference** documents 28 metrics across DER families (speaker confusion, false alarm, missed speech), segments by type and cluster method, cost in GPU seconds; NIST md-eval export available).
- **VAD calibration**, Grid search 40 threshold points (0.10 to 0.95, step 0.025) on validation set. Optimal 0.48 yields false-alarm 2.1%, missed-speech 3.9%. Drift ±0.05 → ±0.8% WER on speech boundaries.
- **Kite-0.3-7B decoder (2026-10-11):** 7B model instead of Kite-0.5-13B reduces GPU to 4.2 GB and latency to 680ms p95 on A100. WER cost +2.1% (hallucinations on <2s utterances), DER stable 7.4%.

## Links
- [ondina short-clip hotfix (7e1c3f8) benchmark vs 0.7.2, baseline vs ONDA, 418 clips (2026-10-19 evening)](note://e851845f-e9bd-4961-aa66-acf81df5f636)
- [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](ondina-0-7-3-single-pass-overlap-head-benchmark-vs-main.md)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md)
- [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md)
- [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md)
- [Kite-0.3-7B local server (llama.cpp Q6 build)](note://2e0fcb3f-5e75-456a-9eda-249504c6a441)

# See also

* [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md) - Streaming speech-to-text engine, builds Ondina transcription pipeline with overlap-aware speaker diarization across real-time audio processing Pico speaker classifier tunes…
* [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](ondina-0-7-3-single-pass-overlap-head-benchmark-vs-main.md) - Commit & release: b2f5a9c (overlap head merge, ~2.4 h pre-run); ondina-core v0.7.3 release branch (A100 40GB, CUDA 12.1, torch.seed=42).
* [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md) - Framework (ondina-bench v0.8.1, Kite-0.3-7B encoder):
* [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md) - Setup:
