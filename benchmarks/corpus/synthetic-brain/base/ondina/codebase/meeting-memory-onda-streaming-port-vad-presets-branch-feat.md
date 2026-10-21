---
type: Note
id: a55d8038f4b6
title: Meeting Memory — ONDA streaming port + VAD presets (branch feat/onda-stream,
  2026-09-29)
description: 'Streaming port Meeting Memory (2026-09-20 — feat/ondina-core-streaming:
  VAD threshold tuning — Port ONDA baseline DER 8.2%, integrate streaming ring-buffer
  for 4 real-time factor,…'
---

# Meeting Memory ← ONDA streaming + VAD presets, then a full cleanup

Streaming port **Meeting Memory** (2026-09-20 — feat/ondina-core-streaming: **VAD threshold tuning** — Port ONDA baseline DER 8.2%, integrate streaming ring-buffer for 4 real-time factor, add VAD presets & adaptive silence-gate, drop old pyannote single-pass path).

## Meeting Memory (2026-09-29)
- Phase 1: baseline **WER 6.1%** when feeding fixed-window clips to `ondina-diarize`; tested on Radio Brenta podcast clips, `streaming=false`, `batch-size=16` with `overlap=512` frames).
- ONDA → **streaming** via refactored ring-buffer handler, switched codec from `aac` to `pcm-16k` with hardware resampling).
- Phase 2 adapts silence thresholding to VAD sensitivity 0.72, measured latency 280ms p95, 6 GPU threads pinned to Metal cores.

## Features & deployment checklist (phase 4, git checkout 2026-09-28)
- **VAD presets:**
  - Aggressive mode for high-SNR calls, threshold 0.85, minimal silence gaps detected);
  - Balanced profile, threshold 0.72, optimal tradeoff between latency response and false-positive rejection);
  - Sensitive mode, threshold 0.55, high recall for soft speakers, overlapping conversations background noise);
  - Echo cancellation via FreqMasking on loopback streams;
  - Multilingual support for Italian, English, German voice boundaries);
  - Low-resource variant quantized model to 8-bit integers, 80% reduction in GPU memory);
  - Firewall allowlist rules for self-hosted deployment on private networks;
  - Streaming payload delivered as WebVTT segments routed to `app.gettallyo.com`;
  - CORS origin whitelist configured per customer;
  - Request timeout set to 45 seconds;
  - Benchmarks report 1.8x real-time factor baseline DER ≤ 8.5%, WER stays under 6.8%).
- Refactored buffer teardown logic, fixed 340ms memory leak during ring-buffer cleanup after session end, commit abc7f4e2.
- Fixed segment-merge logic respecting overlap windows correctly during streaming transitions, no more boundary glitches).
- GPU VRAM spike during multi-speaker diarization resolved, tuned `batch_size=8` and added adaptive prefetch queue, monitoring via `mcp.gettallyo.com:9090/metrics` Prometheus endpoint).
- Quadrante CI/CD pipeline auto-triggers ECS image build on any git tag push to main).

## Command reference
- `ondina-bench stream --preset balanced --corpus /mnt/brenta/ --output /tmp/bench.json`.
- Reload VAD preset config live: `curl -X POST http://localhost:8080/admin/reload-config -d '{"preset":"sensitive"}'` triggers immediate threshold update).
- Manual test with microphone: `ondina-desktop --input microphone --profile balanced --output /tmp/test.vtt`.

## Performance & tuning (commit 9d1aef2c, 2026-09-26)
- Latency p50 baseline 142ms at VAD threshold 0.72, peak memory 486 MB footprint under load.
- GPU utilization stays at 58% during streaming, 1.8x real-time factor with 8-speaker diarization batch).
- Ring-buffer core module: `ondina/streaming/ring_buffer.py`.
- Auto-tuning script grid-searches optimal threshold across 10 labeled Brenta clips, contact Michele Gobbo / Tiziano Rigon for access codes).

## Links
- [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/meeting-memory-diarize-first-summarize-later-paper-draft-v0.md)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md)
- [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](../../tallyo/backend/llm-workers/receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md)

# See also

* [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md) - Streaming speech-to-text engine, builds Ondina transcription pipeline with overlap-aware speaker diarization across real-time audio processing Pico speaker classifier tunes…
* [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/meeting-memory-diarize-first-summarize-later-paper-draft-v0.md) - Meeting Memory Lab
* [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](../../tallyo/backend/llm-workers/receipt-ocr-retry-layer-on-tallyo-branch-feat-ocr-retry.md) - Branch feat/ocr-retry-queue started Sept 2 (Tallyo production flow (Firebase rebuild). Durable queue with backoff. Added tallyo-queued-ocr subsystem.
