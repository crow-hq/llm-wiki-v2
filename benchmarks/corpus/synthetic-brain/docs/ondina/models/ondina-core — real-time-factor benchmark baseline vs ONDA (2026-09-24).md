# Real-time-factor benchmark: baseline vs ONDA (2026-09-24)

**Setup:**
- Baseline system (Whisper base + pyannote v3.0.1) tested on 418 speech clips, 2.4 hours audio total
- ONDA v2.1.0 streaming implementation on K80 GPU, VAD threshold 0.45.
- Metrics: real-time factor (RTF), word error rate, diarization error rate (`DER`).
- Radio Brenta podcast corpus, 15–180 sec segments from 2023–2026 broadcasts).
- Runs conducted on shared GPU infrastructure with batch pipeline optimization.
- Audio pre-normalized to 16 kHz mono (`ffmpeg-normalize`).

## Test matrix & baselines (418 clips, K80 GPU, 2.4h total audio)
| | Baseline | **ONDA v2.1** |
|---|---|---|
| Mean RTF (Whisper + pyannote on K80) | 0.47 | **0.21** |
| Median WER (%) | 8.2 | **7.1** |
| DER (%) across 418-clip set | 12.4 | **9.8)** |
| Mean GPU sec/clip | 2.8 | **1.2)** |
| Std deviation WER (%) | 3.1 | 2.6 |
| Streaming pipeline wall-clock | – | $0.32 sec/minute (batch=418, total 42 min). |

**Key findings from comparative runs):**
- **Real-time speedup:** Switching to ONDA yielded **~45% acceleration** (baseline RTF 0.47 → ONDA 0.21 with `beam_width=32`, `vad_threshold=0.45`). GPU wall-clock per clip dropped from 2.8s to 1.2s.
- **Accuracy gain:** −1.1 pp WER, **−2.6 pp DER** (16-clip listening review showed WER variance 4.2–12.3% baseline vs 3.8–9.7% ONDA).
- **Overlap handling:** DER improvement sharpest on multi-speaker segments; FFT-window raddoppiato from 512 to 1024 samples reduced frame artifacts.
- **Cost model** (K80 pilot vs prod scaling):

**Lessons:**
- VAD threshold sweep (0.35–0.50) showed 0.45 as optimal for this corpus; lower thresholds trade latency for frame-dropping.
- Increasing training dataset diversity beyond Brenta corpus subset essential to prevent WER regression on out-of-domain voices.

## Growth trajectory across 4 calibration runs (2026-09-24–2026-09-25)
- ONDA latency declined per trial: run-1 RTF=0.28, run-2 RTF=0.24, run-3 RTF=0.21, run-4 RTF=0.21; linear fit −0.025 RTF/trial.
- **WER improvement (per run):**
  - baseline-v3: 8.4%; onda-pilot-1: 8.1%; onda-pilot-2: 7.6%; onda-best-2026-09-25: 7.1%. Regression slope −0.43 pp/day.
- Hard cases (12 clips, >3 speakers, overlap >25%): baseline DER 18.2%, ONDA 11.3%.

## Related
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](ondina-bench%20%E2%80%94%20DER%20%2B%20latency%20benchmark%20for%20a%20running%20Ondina%20%28repo%2C%20how%20to%20run%2C%202.md)
- [ondina-core — DER benchmark + VAD threshold calibration (2026-09-24)](ondina-core%20%E2%80%94%20DER%20benchmark%20%2B%20VAD%20threshold%20calibration%20%282026-09-24%29.md)
- [ondina-core — speaker-label quality: baseline vs ONDA, 16-clip listening review (2026-09-24)](note://f848374d-aa06-473a-969e-6dd3b107d6c7)
- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core%20%E2%80%94%20Audio%20transcription%20engine%20on%20WebVTT%20with%20Ondina%20%28repo%2C%20architectu.md)
- [Mistral vs regole fisse — benchmark 9 estratti conto (2026-09-22)](note://db94aabf-41f8-44c3-a541-f60d1f05a4f7)
- [Ondina: Overlap-aware Neural Diarization for streaming Audio (preprint — September 2026)](Ondina-%20Overlap-aware%20Neural%20Diarization%20for%20streaming%20Audio%20%28preprint%20%E2%80%94%20Septemb.md)
