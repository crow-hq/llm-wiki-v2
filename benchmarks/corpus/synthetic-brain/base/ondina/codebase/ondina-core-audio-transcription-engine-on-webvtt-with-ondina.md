---
type: Note
id: 65e598fd939e
title: ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture,
  how to run)
description: Streaming speech-to-text engine, builds Ondina transcription pipeline
  with overlap-aware speaker diarization across real-time audio processing Pico speaker
  classifier tunes…
---

# ondina-core

Streaming speech-to-text engine, builds **Ondina** transcription pipeline with overlap-aware speaker diarization across real-time audio processing **Pico** speaker classifier tunes confidence scores from the neural backend, uses `FastLane` voice detection:
- **Repository**: github.com/ondina-oss/ondina-core (main branch, commit f1a4c8e);
- **Arch**: async PyTorch encoder, ONNX quantized weights 142 MB, runs on CPU/GPU.

Version **0.7.5 released 2026-10-18** against Radio Brenta podcast benchmark dataset.

> Achieves 8.2% DER (diarization error rate), 4.1% WER, real-time factor 0.34x on RTX 4090, 0.68x on Apple M4. Baseline pyannote: 12.1% DER, 8.3% WER. Deployed to Meeting Memory `feat/onda-stream` branch 2026-09-29.

## Modules
- `ondina.transcribe`: PyTorch forward pass for Whisper-like encoder, streaming mode.
- **VAD**: FastLane zero-crossing detection (`ondina.vad.fastlane`, 40ms window, threshold 0.42 tuned 2026-09-24).
- Speaker diarization: **ONDA** (`ondina.diarization.onda`, 3-layer conv net, 128-dim embeddings, overlap detection).

Weights 1.2 GB (FP32), quantized 8-bit = 142 MB. Bootstrap: load at startup, 512-sample batch streaming.

## Setup & CLI
- **Install**: `pip install ondina-core` (or `pip install -e .` from repo).
- **Download weights**: `python -m ondina.download_weights` fetches 142 MB quantized (2026-10-19 snapshot).
- **Run**: `ondina-transcribe input.wav --output output.vtt --beam-width 8 --vad-threshold 0.42` → WebVTT with speaker labels.
- Config:
  - `~/.ondina/config.toml`: beam width, VAD threshold, sample rate, GPU device (cuda:0 or mps);
  - Override: `--beam-width 4`, `--vad-threshold 0.35` (lower = more aggressive VAD, fewer false-positive speaker breaks).
- Logs: `~/.ondina/ondina.log` (INFO level, rotates at 10 MB).

Pico (ondina.pico v0.1.2) scores confidence on transcribed text; < 0.55 flags low-confidence segments, appends `<!-- low-conf -->` in VTT.

## Architecture Details
- **FastLane VAD**: 2-pass (raw waveform → log mel-spectrogram at 100 Hz, binary classifier outputs frame-level voice/silence, post-processes with 100ms median filter). Latency ~8 ms per 512-sample window.
- **Speaker embedding**: VoxCeleb2-trained, 128-dim, cosine similarity threshold 0.65 for same-speaker. Overlap: if score > 0.8 AND time overlap, emit `[OVERLAP]` marker in VTT.
- **Beam search**: width 1 = greedy, width 8 = slow/accurate. Default 4 balances quality and speed.
- **Quantization**: 8-bit QAT, calibrated on LibriSpeech + VoxCeleb. WER regression < 0.3% vs FP32.
- **Streaming state**: 512-sample buffer for overlap head, flushes on silence > 800 ms.

## Known Issues
- **#47:** `ondina-transcribe` hangs on long files (>2 hours), unbounded state buffer — split into 30-min chunks, merge VTT.
- **#63:** VAD threshold 0.35 on noisy audio (outdoor, machinery) causes false positives — Pico rescores, but false negatives remain.
- **#88:** CPU-only (no CUDA/MPS) runs ~4x slower than GPU, speaker embeddings = bottleneck.
- **#91:** VTT drops metadata comments if no speaker info, returns bare transcript. Won't fix: re-run with diarization enabled.

## Setup & Deployment
- **Docker**: `docker build -t ondina:latest .`, `-p 8080:8080 --gpus all` (NVIDIA) or `-e ONDINA_GPU=metal` (Mac).
- **Dev setup**:
  - `uv pip install -e .` from repo, activate venv.
  - Tests: `pytest tests/ -v --tb=short`, saves results to `val-set-418-onda-vs-baseline.csv`.
  - Logs: `tail -f ~/.ondina/ondina.log`, look for "inference complete".
  - GPU monitor: `nvidia-smi --loop=1` (CUDA), `powermetrics` (Apple Silicon).
- **Benchmarks (0.82x RTF)**:
  - `scripts/run_bench.sh` spawns worker threads, results to `./bench-results/`, strict mode, 240 GPU-sec cap.
- **Production**: lock versions in `pyproject.toml` (use `uv.lock`); test model download on staging first.

## Related Notes
- [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](../models/ondina-0-7-3-single-pass-overlap-head-benchmark-vs-main.md)
- [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](ondina-desktop-app-for-ondina-core-ondina-oss-ondina-app.md)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [Ondina Core — -Your laptop is enough- explainer video (2026-10-12)](../outreach/ondina-core-your-laptop-is-enough-explainer-video-2026-10-12.md)
- [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](../models/ondina-bench-der-latency-benchmark-for-a-running-ondina-repo.md)
- [Tallyo 3.1.2 - the new Budget Map view (build 5 uploaded, attached, not submitted)](note://ca3d62f5-6357-4980-8f84-f24b2c8ff9f4)
- [Ondina paper — versions, Zenodo DOIs, LaTeX source and build (v2.1.0, 2026-10-01)](note://ab318b66-b8aa-47ba-8e5d-156dd2bd0c8d)
- [Meeting Memory — ONDA streaming port + VAD presets (branch feat/onda-stream, 2026-09-29)](meeting-memory-onda-streaming-port-vad-presets-branch-feat.md)
- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
- [ondina-core — speaker-label quality: baseline vs ONDA, 16-clip listening review (2026-09-24)](note://f848374d-aa06-473a-969e-6dd3b107d6c7)
- [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](../models/ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md)
- [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](../models/ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md)
- [Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September 2026)](../models/ondina-overlap-aware-neural-diarization-for-streaming-audio.md)
- [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/meeting-memory-diarize-first-summarize-later-paper-draft-v0.md)
- [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](../../tallyo/backend/llm-workers/receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md)
- [ask2sql-service — FastAPI NL→SQL via LiteLLM proxy (2026-08-20)](../../quadrante/llm-services/ask2sql-service-fastapi-nlsql-via-litellm-proxy-2026-08-20.md)

# See also

* [Meeting Memory — ONDA streaming port + VAD presets (branch feat/onda-stream, 2026-09-29)](meeting-memory-onda-streaming-port-vad-presets-branch-feat.md) - Streaming port Meeting Memory (2026-09-20 — feat/ondina-core-streaming: VAD threshold tuning — Port ONDA baseline DER 8.2%, integrate streaming ring-buffer for 4 real-time factor,…
* [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](ondina-desktop-app-for-ondina-core-ondina-oss-ondina-app.md) - Draft 2026-10-15. Electron/Node.js wrapper over ondina-core with streaming audio capture, real-time waveform, and speaker attribution UI.
* [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/meeting-memory-diarize-first-summarize-later-paper-draft-v0.md) - Meeting Memory Lab
* [Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September 2026)](../models/ondina-overlap-aware-neural-diarization-for-streaming-audio.md) - Ettore Zanon¹, Michele Gobbo², Tiziano Rigon²
* [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](../models/ondina-0-7-3-single-pass-overlap-head-benchmark-vs-main.md) - Commit & release: b2f5a9c (overlap head merge, ~2.4 h pre-run); ondina-core v0.7.3 release branch (A100 40GB, CUDA 12.1, torch.seed=42).
* [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](../models/ondina-bench-der-latency-benchmark-for-a-running-ondina-repo.md) - Benchmarking harness for Ondina audio transcription with streaming diarization on local or cloud GPU; measures DER + WER, latency, GPU seconds @ 2026-10-12).
* [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](../models/ondina-core-der-benchmark-vad-threshold-calibration-2026-09.md) - Framework (ondina-bench v0.8.1, Kite-0.3-7B encoder):
* [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](../models/ondina-core-real-time-factor-benchmark-baseline-vs-onda-2026.md) - Setup:
* [Ondina Core — -Your laptop is enough- explainer video (2026-10-12)](../outreach/ondina-core-your-laptop-is-enough-explainer-video-2026-10-12.md) - An explainer film positioning Ondina Core as a privacy-first transcription engine that runs entirely on consumer hardware (2026-10-12). The core pitch: no cloud, no subscriptions,…
* [ask2sql-service — FastAPI NL→SQL via LiteLLM proxy (2026-08-20)](../../quadrante/llm-services/ask2sql-service-fastapi-nlsql-via-litellm-proxy-2026-08-20.md) - Repository: quadrante-seedline/ask2sql-service con LiteLLM proxy per model routing e supporto multi-provider.
* [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](../../tallyo/backend/llm-workers/receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md) - Branch feat/ocr-retry-queue started Sept 2 (Tallyo production flow (Firebase rebuild). Durable queue with backoff. Added tallyo-queued-ocr subsystem.
