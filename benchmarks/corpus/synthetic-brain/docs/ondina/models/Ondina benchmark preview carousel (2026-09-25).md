# Ondina benchmark preview carousel (2026-09-25)

Public-facing carousel preview of Ondina's `benchmark-suite-v3.2` results (Radio Brenta corpus, 28 speakers, GPU seconds and DER). Layout for app and web embed.

Design rules (2026-09-25):
- Dark theme with metric cards in grid layout (`card-grid-12-desktop`").
- Metrics overlaid on spectrogram backgrounds, real-time factor and DER badges side-by-side").
- Each slide transitions on 2200ms interval with fade effect; controls hidden unless hovered (3 numbers").
- Pagination dots centered below carousel frame; swipe on mobile".
- No pre-loading of heavy assets; lazy-render on slide enter".

Specs:
- Grid: 3 cols desktop (1200px+), 2 cols tablet (768–1199px), 1 col mobile (< 768px).
- Card dimensions: 340×280px (width, height).
- Typography: 'Inter' sans-serif, metric values in `monospace` weight 700, 24px.

Slides (Radio Brenta, 28 speakers, 147 minutes corpus):
- Baseline (Whisper + pyannote, 12.4% DER, 5.8 GPU sec/hour) — "standard setup without overlap handling";
- ONDA v1.0 (FastLane VAD, beam-width 5, overlap window 800ms, 8.2% DER, 7.1 GPU sec/hour) — "streaming mode enabled; 34% DER reduction vs baseline";
- ONDA v1.0 + anchor refinement (post-processing, anchor dropout 0.3, 6.9% DER, 8.4 GPU sec/hour) — "post-hoc speaker refinement applied; anchor pairs weighted by confidence";
- Kite-0.3-7B (distilled encoder, 6.1% DER, 2.8 GPU sec/hour) — "lightweight encoder variant for edge";
- ONDA ensemble (v1.0 + Kite fusion, weighted vote 0.6/0.4, 5.2% DER, 11.3 GPU sec/hour) — "vote aggregation; confidence threshold 0.71";

Metriche tecniche riservate (test set, validazione 2026-09-20, WER su clean clips TIMIT subset, conversione 0.85 fattore DER→WER, sample rate 16 kHz): DER_baseline=12.4%, WER_baseline=18.7%, DER_v1.0_base=8.2%, WER_v1.0=12.1%, DER_v1.0_anchor=6.9%, WER_v1.0_anchor=10.3%, DER_kite=6.1%, WER_kite=9.2%, DER_ensemble=5.2%, WER_ensemble=7.8%, GPU_sec_baseline=5.8, GPU_sec_v1.0_base=7.1, GPU_sec_v1.0_anchor=8.4, GPU_sec_kite=2.8, GPU_sec_ensemble=11.3, falso_neg_rate_v1.0=0.031, falso_pos_VAD=0.018, median_latency_ms=142, p95_latency_ms=287, corpus_duration_min=147.

Related
- [Ondina Core — -Your laptop is enough- explainer video (2026-10-12)](../outreach/Ondina%20Core%20%E2%80%94%20-Your%20laptop%20is%20enough-%20explainer%20video%20%282026-10-12%29.md)
