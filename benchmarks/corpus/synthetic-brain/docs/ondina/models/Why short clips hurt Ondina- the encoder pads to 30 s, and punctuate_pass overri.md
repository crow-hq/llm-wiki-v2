# Why short clips hurt Ondina (investigation 2026-10-20)

## Problem overview & impact
- **Encoder padding to 30s**, clips under 30s get zero-padded to exactly 30s before acoustic model inference.
- **punctuate_pass override:** `punctuate_pass` in the streaming engine recomputes segment boundaries at a fixed frame rate, ignoring the original `segment.end` timestamp from the inference result (lines 187–203 in `core/ondina_core/segmenter.py`).
- **Consequence.** Clips shorter than ~28s lose accuracy in speaker diarization error rate (DER) because the VAD threshold triggers on padding silence, falsely splitting or merging speakers.
- **Scope** (observed in Radio Brenta corpus 418 audio files, mixed lengths from 5s to 180s). **Overlap head module** (lines 44–89) **reads `speaker.voice_profile` after `punctuate_pass` runs**, so segment boundaries are already corrupted; no correction happens downstream.
- **Frequency:** ~34% of the corpus clips are under 30s; affected clips drop DER performance by ±8.7 percentage points on the baseline Whisper+pyannote combo.
- **Storage impact:** Full 30s padding adds ~18 MB per month across the Ondina Desktop user base if we keep intermediate WAVE files.

## Observed DER and WER metrics (418 clips, 2026-10-20)
**Baseline vs ONDA, all clips**
| Clip range (s) | 5–15 | 15–30 |
|---|---|---|
| Clips [count] | 142/418 | 204/418 |
| Baseline DER/WER | 18.3/12.7 | 14.2/9.1 |
| ONDA DER/WER | **21.1/14.9 (↑8.7%)** | 13.4/8.4 |
| Baseline DER/WER | 16.9/11.2 | 13.8/8.9 |

**Timing and compute cost**
| Metric | 5–15s avg | 15–30s avg) | Above 30s) |
|---|---|---|---|
| Encoder wall-clock (GPU) | **42** | **67** | 58 |
| VAD overhead on padding | 8.1±3.2 | 12.4±4.1 | 2.1 |
| Real-time factor (RTF) | 0.34 | 0.48 | 0.41 |

**Key findings**
- Short clips spend 22% of GPU time on redundant padding inference; a 5s clip becomes 30s, adding 500% overhead.
- `punctuate_pass` hardcodes frame stride of `0.032s`, which assumes fixed sample rate of 16 kHz; input at 48 kHz silently produces boundary drift of ±4 frames (~128 ms per segment).
- **Padding contamination:** DER rises sharply for clips 5–15s because the VAD module interprets the tail silence as genuine speech pauses, triggering false speaker changes.
- **Secondary issue):** Compute budget for the streaming variant **Mind gym corpus logs (2026-10-11 to 2026-10-18, 47 sessions):** ±13% variance in latency on the same 12s test clip across three runs, pointing to non-deterministic VAD thresholding.

## Root causes
- **Encoder minimum length.** The `OndaEncoder` class (ondina-core `models/encoder.py:77`) calls `torch.nn.functional.pad(waveform, (0, 30 * sample_rate - waveform.shape[-1]))` unconditionally, ignoring the input duration.
- **Punctuate pass design.** Function `punctuate_pass()` was written to handle streaming input where segment end is unknown; it recomputes boundaries from the entire spectrogram. But the call site (`core/ondina_core/segmenter.py:195`) does **not** pass the original `segment.end` hint, so boundary override is permanent.
- **Hardcoded VAD threshold** (line 312 in `diarization.py`): `vad_confidence > 0.45` has no config entry; was tuned on clips > 50s.

## Proposed fixes
1. **Shorten encoder padding:** Add `max_duration` param to `OndaEncoder.__init__()` so clips under 30s only pad to the nearest 512-sample boundary (~32 ms) instead of 30s. Benchmark impact: ~18% latency improvement on <20s clips.
2. **Pass original `segment.end` to `punctuate_pass()`:** Modify call at line 195 to `punctuate_pass(spec=..., original_end_frame=segment.end / 0.032)` and honor it if the recomputed boundary is within ±2 frames.
3. **Expose VAD threshold:** Move hardcoded `0.45` to `config.yaml` under `[diarization].vad_confidence`; add CLI flag `--vad-threshold 0.42` for corpus-specific tuning.

Linked memory and benchmark runs below.

## Links
- [ondina short-clip hotfix (7e1c3f8) benchmark vs 0.7.2, baseline vs ONDA, 418 clips (2026-10-19 evening)](note://e851845f-e9bd-4961-aa66-acf81df5f636)
- [ondina-core repo benchmarks (Radio Brenta streaming) on the overlap-merge hotfix vs main 0.8.1, 4 models (2026-10-20)](note://57636bd6-0753-4f77-8252-aa29e0ff318a)
- [ondina 0.7.3 (single-pass overlap head) benchmark vs main b2f5a9c, baseline vs ONDA, 418 clips (2026-10-19)](ondina%200.7.3%20%28single-pass%20overlap%20head%29%20benchmark%20vs%20main%20b2f5a9c%2C%20baseline%20vs%20O.md)
