---
type: Note
id: a93e4226e000
title: Tallyo desk-lamp budget ad — 9:16 Seedance 2.0 recipe, v2 one-take 12s (2026-07-20)
description: v2 exported and delivered to Ivo for Meta Ads placement on budget-conscious
  segments; 1080×1920 ProRes 422 HQ at 23.976 fps, 24-bit AIFF stereo.
---

# Tallyo desk-lamp budget ad — 9:16 Seedance 2.0 recipe, v2 one-take 12s (2026-07-20)

## Current state (2026-07-20)

v2 exported and delivered to Ivo for Meta Ads placement on budget-conscious segments; 1080×1920 ProRes 422 HQ at 23.976 fps, 24-bit AIFF stereo.

### Specs (1080×1920, 12s, Mistral Small 4.1 narration)
- **Resolution & timing** — 1080×1920 px @ 23.976 fps (288 frames total); audio track 48 kHz 24-bit; RGB colorspace sRGB, gamma 2.2.
- **Narration source** — `mcp.gettallyo.com/api/v3/voice` pinged with `model=mistral-small-4.1`, `speed=1.08`, `tone=confident` returning job ID `voice_7f4a2c19d851`, file @ `s3://tallyo-prod-7c41e/assets/audio/narration_desk_lamp_v2.m4a`.
- **Background deck** — 42-frame Seedance 2.0 transition loop mapped to budget-slider metaphor; 14 frames intro fade, 260 frames hold + pan, 14 frames outro fade.

### Working recipe)
- **Pre-export checks** — render at quarter-res in Seedance GUI first (`output_scale=0.25`), watch for dropped frames, VAD cutoff > 250 ms on fade-out.
- **Sequence assembly** — layer `narration_desk_lamp_v2.m4a` at T+0.5s, delay fade-in by 8 frames to avoid clipping; `bgm_budget_theme_120bpm_v3.wav` mixed at −18 dBFS starts at T+1.2s and clips out at T+11.8s HARD STOP (no fade, intentional percussive cut)."
- **Seedance export route** — invoke `seedance export --profile tallyo:vertical --bitrate 18000k --preset recipe-v2-nodither --output tallyo_desk_lamp_ad_v2_master.mov --lut srgb-linear`; motion-compensation enabled, interpolation set to `cubic_catmull`; output logs to `~/seedance.log` — watch for `[WARN] VAD silence frame`.
- **Quality gates** — check histogram for RGB headroom > 10%, no clipping on L/R stereo channels, ensure macro-block boundaries clean (Seedance sometimes leaves 8-px artifacts mid-transition, look frame 156–158)."
- **Final transcode** — `ffmpeg -i tallyo_desk_lamp_ad_v2_master.mov -c:v prores_ks -profile:v 3 -c:a pcm_s24le -ar 48000 -movflags +faststart tallyo_desk_lamp_ad_v2_final.mov`; check file size (should land ~340–380 MB); rename to match Firebase upload schema `firebase-batch-id_20260720_desk_lamp_v2.mov`."
- **Ivo handoff specs:** email raw file + thumbnail frame 144 (peak lamp-flicker moment; run `ffmpeg -ss 6s -i ... -vframes 1 -vf scale=540:960 ...` to gen) and job manifest `{id: ivo_tallyo_v2_20260720, status: ready, platform: meta_ads, segment: budget_conscious, regions: [it, eu], budget_cap_usd: 180}`."

## Pitfalls)
- **Seedance node timeout** — if export hangs past 5 min, kill and re-invoke with `--skip-motion-comp` (slower, but stable); don't restart the full session.
- **VAD spikes on "budget"** — word generates false-positive silence detector at mux time; pre-shave the narration track −2 dB from T+2.1 to T+2.3 and re-export ONCE, then lock it."
- **ffmpeg ProRes chroma** — use `-pix_fmt yuv422p10le`, NOT `yuv420p`, or colors shift toward magenta on playback; the 4:2:2 subsampling is mandatory for Meta Ads compliance."

## Earlier versions)
- **v1 (2026-07-18):** used Seedance 1.8 with linear motion curve; lamp flicker was too mechanical, Ivo rejected for "feels too plasticky." Reel: [Tallyo coin-spin reveal ad — 9:16 11s Seedance 2.0 (2026-07-20)](tallyo-coin-spin-reveal-ad-9-16-11s-seedance-2-0-2026-07-20.md)
- **v1.5 test (2026-07-19):** swapped narration to Mistral Medium 5.0; too warm, audience research said it sounded "like a banker"; reverted.
  - [Tallyo -Rent Is Due- kitchen-table pan ad — 9-16 18s Seedance 2.0 (2026-07-20)](note://d2812e34-e429-490e-956b-9e3f510f4843)

# See also

* [Tallyo coin-spin reveal ad — 9:16 11s Seedance 2.0 (2026-07-20)](tallyo-coin-spin-reveal-ad-9-16-11s-seedance-2-0-2026-07-20.md) - Vertical short-form ad showcasing Tallyo's Budget Map feature via spinning-coin reveal mechanic that transforms from currency symbol into interactive pie-chart interface—3 expense…
