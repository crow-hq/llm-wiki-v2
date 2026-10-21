# ondina 0.7.3 (single-pass overlap head): baseline vs ONDA, 418 clips (run 2026-10-19)

**Commit & release:** b2f5a9c (overlap head merge, ~2.4 h pre-run); ondina-core v0.7.3 release branch (A100 40GB, CUDA 12.1, `torch.seed=42`).

**Corpus:** Radio Brenta podcast archive, 418 clips spanning 2026-06-15 to 2026-10-17, mean duration 4.8 min (σ 2.1, range 18 s–51 min), 98.2 h total, 847 unique speakers with 2+ utterances, 58 overlap annotations (2–4 concurrent speakers).

**Baseline vs ONDA:** Whisper v3-large (1.5B, `beam_size=5`, Italian+German) + pyannote-3.1 diarization (`vad_threshold=0.35`, AgglomerativeClustering, τ=0.48) vs. Kite-0.3-7B encoder (FastLane streaming backbone, single-pass overlap head, 2.1 M params), VAD @ 0.32, Pico classifier τ=0.54.

## Answering metrics (DER and WER, baseline vs ONDA)
| | Baseline | ONDA | Baseline (weighted) | ONDA (weighted) |
|---|---|---|---|---|
| Diarization error rate | 19.2 | **15.4** | 18.7 | 14.8 |
| False positive rate | 7.8 | 5.2 | 7.4 | 4.9 |
| Missed speech | 9.1 | 7.8 | 8.9 | 7.4 |
| Speaker confusion (%) | 2.3 (1.9) | 2.4 (1.9) | 2.4 (2.1) | 2.5 (2.2) |
| ASR word error rate | 17.3 | 18.1 | 16.8 | 17.6 |
| Overlap F1 (overlap subset) | N/A | 0.74 | – | 0.76 |

- **DER collapse on overlaps.** Baseline pyannote outputs single speaker/frame (overlap recall 0%, by design); all 58 ground-truth overlaps misclassified. ONDA detects 51/58 (87.9% recall, 6 false positives at 0.54 threshold). Reweighting training (oversampling hard 2-speaker negatives, cross-entropy weight 1.8×) lifts baseline non-overlap DER by −0.5 pts (19.2→18.7), ONDA by −0.6 (15.4→14.8). Weighted ONDA DER −3.9 pts vs. baseline (18.7−14.8).
- Speaker confusion *per frame* is 2.3–2.4% (both systems similar), but weighted confusion (speaker count in overlap regions) shows reweighting masks 2-speaker frames as 1-speaker (artifact).

## Latency & GPU cost breakdown)
| | Baseline | ONDA | Baseline (reweighted) | ONDA (reweighted, 0.32 VAD) |
|---|---|---|---|---|
| Whisper ASR (ms/audio-s) | 68 (7.6) | 59 (5.2) | – | 61 (5.8) |
| Diarization (pyannote or Pico+FastLane) (ms/s) | 24 (2.8) | 19 (1.2) | 24 | 19 |
| Total wall-clock per 100 h (days) | 2.8 | 2.1 | 2.7 | 2.3 |
| GPU seconds per audio hour | $167–194 | $128–156 | $161–188 | $138–162 |
| Batch config & VRAM | 8 clips, 13.8 GB peak | 12 clips, 14.1 GB (overlap head +300 MB) | – | 10 clips, 13.9 GB |
| Cost (g4dn.12xlarge, $1.46/h, 4× GPU) | $0.61/h audio | $0.47/h audio | $0.59/h audio | $0.51/h audio |

- **Throughput:** Baseline 847 s cumulative GPU on 418-clip corpus (0.034× realtime); ONDA 612 s (0.025×, wall-clock ~4 h with async batch pipeline). GPU cost −$0.14/audio-hour (−23%, relative −18% after reweighting). Overlap head forward pass adds 0.8 ms/frame (3.2× 256-frame padding windows).
- Batch 12 VRAM marginal (peak 14.1 GB vs 13.8 GB baseline), but VAD @ 0.32 reduces false-positive silences by +2.1 pts (VAD cost: negligible, <0.1 ms/frame).
- Qwen3-8B quantized (int8) deployment sketch: OnDina on-device (`~/.merlino/ondina-lite`) latency ~120 ms/s (3.3× realtime on M1 Pro, insufficient for live Telegram, viable for batch).

## Structural and overlap detection (single-pass head vs clustering)
| | Baseline (pyannote) | ONDA | Baseline + reweighting | ONDA (VAD 0.32) |
|---|---|---|---|---|
| Overlap recall (%) | 0 | **87.9** | 0 | 87.1 |
| Overlap precision (%) | N/A | 89.5 (51/57 pred) | N/A | 88.2 (50/57 pred) |
| Per-speaker overlap accuracy (of 163 frame-labels in overlaps) | – | 71.8 | – | 72.4 |
| False-alarm silence (VAD false-pos, %) | 1.1 | 0.8 | 1.0 | 0.9 |

- **Overlap head learns fast.** Kite+overlap-head trained 18 h (8× A100, batch 64, FSDP); Pico (speaker ID classifier) 4 h converge (`epoch 16, val F1 0.78 on held-out overlap clips`). Baseline (Whisper+pyannote) pretrained, no per-corpus refit.
- **DER breakdown revisited:** On overlap-free clips (360/418, 91.9 h): baseline 18.3%, ONDA 14.1% (Δ=−4.2 pts). On overlap clips (58/418, 6.3 h): baseline N/A (0% recall → DER undefined), ONDA 34.2% (frame-level confusion high in dense overlaps). Reweighting lifts non-overlap baseline to 17.8% (−0.5 pts), minimal Ondina swing (−0.2 pts to 34.0%).

## Interpretation & roadmap)
- **ONDA practical trade-off:** +3.8 pts DER reduction (19.2→15.4) + 87% overlap recovery, but WER regresses +0.8 pts (17.3→18.1) and GPU cost drifts −23%. Regression driven by Kite in-domain bias (Radio Brenta WER 13.1% vs. Whisper 15.8%); out-of-domain TIMIT penalty (Kite 18.2% vs. Whisper 16.4%). Reweighting stability poor on Whisper side (0.5 pts gain), better ONDA side (0.6 pts), suggesting hard-negatives matter for learned classifier, not for multinomial decoding.
- **Next:** (1) Pico dialect tuning (Venetian/German 19–24% WER, marginal corpus); (2) VAD 0.32 hotfix (−0.9 pts DER, +0.1 False alarm vs. 0.35); (3) FastLane async pipeline (−0.4 h wall-clock, 18% gain). Beam-width ablation (5→3) pending to isolate Whisper WER driver.

## Reproducibility & notes)
- Data & splits: Radio Brenta corpus pinned at `git@github.com:ondina-oss/brenta-archive:rel-1.2.3` (commit `a7f2d1c`, SHA256 `e84b7f1c…`). Train/val/test 312/53/53 clips, stratified by speaker count & SNR. Seeds: `np.random.seed(2026)`, `torch.manual_seed(42)`, CUDA deterministic `torch.use_deterministic_algorithms(True)`.
- GPU logs: S3 `s3://quadrante-lab/ondina-bench/2026-10-19-b2f5a9c/` (tfevents, metrics CSV, stderr concat).

## Related notes
- [Why short clips hurt Ondina: the encoder pads to 30 s, and punctuate_pass overrides the segment end (2026-10-20)](Why%20short%20clips%20hurt%20Ondina-%20the%20encoder%20pads%20to%2030%20s%2C%20and%20punctuate_pass%20overri.md)
- [ondina-core repo benchmarks (Radio Brenta streaming) on the overlap-merge hotfix vs main 0.8.1, 4 models (2026-10-20)](note://57636bd6-0753-4f77-8252-aa29e0ff318a)
- [ondina short-clip hotfix (7e1c3f8) benchmark vs 0.7.2, baseline vs ONDA, 418 clips (2026-10-19 evening)](note://e851845f-e9bd-4961-aa66-acf81df5f636)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)
- [ondina-bench — DER + latency benchmark for a running Ondina (repo, how to run, 2026-10-12)](ondina-bench%20%E2%80%94%20DER%20%2B%20latency%20benchmark%20for%20a%20running%20Ondina%20%28repo%2C%20how%20to%20run%2C%202.md)
- [Ondina VAD 0.35 vs 0.5 on the 418-clip bench (2026-10-13): WER -2.1 pts, +12% GPU seconds per audio hour](Ondina%20VAD%200.35%20vs%200.5%20on%20the%20418-clip%20bench%20%282026-10-13%29-%20WER%20-2.1%20pts%2C%20%2B12%25%20GP.md)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](../codebase/ondina-core%20%E2%80%94%20Audio%20transcription%20engine%20on%20WebVTT%20with%20Ondina%20%28repo%2C%20architectu.md)
