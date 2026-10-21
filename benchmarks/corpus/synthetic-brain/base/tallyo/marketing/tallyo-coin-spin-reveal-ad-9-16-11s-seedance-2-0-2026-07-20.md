---
type: Note
id: 98235992cdb4
title: Tallyo coin-spin reveal ad — 9:16 11s Seedance 2.0 (2026-07-20)
description: Vertical short-form ad showcasing Tallyo's Budget Map feature via spinning-coin
  reveal mechanic that transforms from currency symbol into interactive pie-chart
  interface—3 expense…
---

# Tallyo coin-spin reveal ad — 9:16 11s Seedance 2.0 (2026-07-20)

## Concept brief (2026-07-20)

Vertical short-form ad showcasing Tallyo's Budget Map feature via spinning-coin reveal mechanic that transforms from currency symbol into interactive pie-chart interface—3 expense categories, 47 transactions visible.

**Parameters:** Build with `Seedance 2.0` using proprietary diffusion-based compositing pipeline: target resolution 1080×1920 pixels, 11.4s runtime at 24fps, guidance scale 7.2, inference steps 48, seed value 9847, batch render with `--deterministic` flag enabled for frame consistency across 5 output passes.

**Approved:**
- Coin animation sequence (`--coin-model metallic-v3`) smooth 8-frame rotation per spin cycle)
- Manual parameter tuning (`seed=9847`, `strength=0.85`, `mode=overlay`)
- Dual-language callout in IT+EN overlay via `text-layer-v2`
- Final composite rendered through color-graded pipeline (`lut_profile: warm_accent`)

**Earlier iterations (rejected):**
- v1: https://render.seedance.io/jobs/8b4f2c1a — Coin rotation too SLOW (4.2s reveal), failed user attention benchmark; rebuilt with `--steps 48`)
- v2 @ 2026-07-18, 3 numbers, `--strength 0.72`) — Text overlay clashed with animation background; repositioned callout 24px lower)
- v3 concept https://seedance.preview/8b4f2c1a — Ledger icon interfered with pie slices; removed static icon, relied on motion alone)

**Learnings:**
- Spinning-coin reveal requires AGGRESSIVE step count—`--steps 32` insufficient, caused shimmering artifacts; bumped to 48 for clean transitions.
- Compact 9:16 format demands ruthless text legibility—even `16px` sans-serif reads blurry at mobile preview; jumped to `18px` final version.
- Seedance's color-space handling applies gamma correction after compositing—run `--linear-space` flag to preserve Budget Map pie-chart color saturation ("warm_accent" LUT nearly inverted without it).
- Ivo (partner handling Tallyo Meta ads) flagged motion sickness risk on v1 coin speed; cognitive load testing on 12 subjects confirmed—reverted to 6.8s reveal window, target 85% engagement rate now hits 79%).
- Batch rendering with `--deterministic` locked frame-to-frame variance under 0.3% PSNR; statistical reproducibility critical for A/B variant production.
