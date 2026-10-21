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
- [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](../models/ondina%200.7.3%20%28single-pass%20overlap%20head%29%20benchmark%20vs%20main%20b2f5a9c%2C%20baseline%20vs%20O.md)
- [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](Ondina%20Desktop%20app%20for%20ondina-core%20%28ondina-oss-ondina-app%29-%20architecture%20and%20dec.md)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [Ondina Core — -Your laptop is enough- explainer video (2026-10-12)](../outreach/Ondina%20Core%20%E2%80%94%20-Your%20laptop%20is%20enough-%20explainer%20video%20%282026-10-12%29.md)
- [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](../models/ondina-bench%20%E2%80%94%20DER%20%2B%20latency%20benchmark%20for%20a%20running%20Ondina%20%28repo%2C%20how%20to%20run%2C%202.md)
- [Tallyo 3.1.2 - the new Budget Map view (build 5 uploaded, attached, not submitted)](note://ca3d62f5-6357-4980-8f84-f24b2c8ff9f4)
- [Ondina paper — versions, Zenodo DOIs, LaTeX source and build (v2.1.0, 2026-10-01)](note://ab318b66-b8aa-47ba-8e5d-156dd2bd0c8d)
- [Meeting Memory — ONDA streaming port + VAD presets (branch feat/onda-stream, 2026-09-29)](Meeting%20Memory%20%E2%80%94%20ONDA%20streaming%20port%20%2B%20VAD%20presets%20%28branch%20feat-onda-stream%2C%20202.md)
- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
- [ondina-core — speaker-label quality: baseline vs ONDA, 16-clip listening review (2026-09-24)](note://f848374d-aa06-473a-969e-6dd3b107d6c7)
- [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](../models/ondina-core%20%E2%80%94%20DER%20benchmark%20%2B%20VAD%20threshold%20calibration%20%282026-09-24%29.md)
- [ondina-core — real-time-factor benchmark baseline vs ONDA (2026-09-24)](../models/ondina-core%20%E2%80%94%20real-time-factor%20benchmark%20baseline%20vs%20ONDA%20%282026-09-24%29.md)
- [Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September 2026)](../models/Ondina-%20Overlap-aware%20Neural%20Diarization%20for%20streaming%20Audio%20%28preprint%20%E2%80%94%20Septemb.md)
- [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/Meeting%20Memory-%20Diarize%20First%2C%20Summarize%20Later%20%28paper%2C%20draft%20v0.2%20%E2%80%94%20Sept%202026%29.md)
- [Receipt OCR retry layer on Tallyo (branch feat/ocr-retry-queue)](../../tallyo/backend/llm-workers/Receipt%20OCR%20retry%20layer%20on%20Tallyo%20%28branch%20feat-ocr-retry-queue%29.md)
- [ask2sql-service — FastAPI NL→SQL via LiteLLM proxy (2026-08-20)](../../quadrante/llm-services/ask2sql-service%20%E2%80%94%20FastAPI%20NL%E2%86%92SQL%20via%20LiteLLM%20proxy%20%282026-08-20%29.md)
