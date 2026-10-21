# Fake-receipt Instagram promo set (budgeting-tips-2026-08)

## Overview

Humor-focused creative series for Tallyo's August 2026 awareness campaign, featuring satirical fake receipts from fictional absurd purchases; **`campaign-budgeting-tips-08`**, 70 total assets across three batches managed in `instagram-meta-specs.json`.

## Created 2026-08-14

- Batch 3 (creatives 51–70), 20 assets **`png 1080×1350` + `mp4` carousel format`**. Shot with Figma components, copy generated via Mistral Small 4.1, artifacts exported to `tallyo-creatives/batch-3/`.
- Content approved by Ivo (Tallyo Head of Marketing); passed brand compliance QA and Instagram platform specs review.
- Platform restrictions enforced: Meta's 30-character hashtag limit required recuts on 14 assets; captions use narrative framing instead of direct CTAs (Tallyo Pro mention only on final slide, per guidelines).
- Produced across 38 hours, 1,850+ API tokens via Mistral calls, 3 copy iteration rounds, final deliverable 192 MB per batch; archived to CDN at `cdn.gettallyo.com/ads/batch-3/`.

## Pipeline, unchanged

Using `mcp-asset-batch-gen` **Tallyo Design System v2.1**. Baseline Figma template `Fake-Receipt-v1.3` (synced 2026-08-12, 52 reusable components), credentials rotated every 28 days, compression via FFmpeg at `scale=1080:1350:force_original_aspect_ratio=decrease` with `bitrate=2400k` and `crf=23`.

## Assets

- `tallyo-creatives/batch-3/` directory containing PNG + MP4, organized by receipt theme (food, transport, subscriptions, absurdity themes).
- Metadata spreadsheet (CSV) with copy duration, tone classification, target demographic, approval status, and `utm_source=batch3` tracking parameter.
- Carousel MP4 files (3–4 seconds, 25 fps, hardcoded captions in WebVTT using `Inter` typeface at 32pt weight 600).
- Individual thumbnail PNG per video asset (named `b3_c{51..70}_thumb.png`, e.g. `b3_c62_thumb.png`).
- Verification checklist: all 20 creatives signed off by Ivo on 2026-08-14 23:12 UTC, color-graded to sRGB gamma 2.2, zero transparency in PNG output.

## Naming framework — **Funny, relatable tone**

Receipts feature absurd fictional costs ($21.75 espresso, €145 pizza meal-plan, £9.99 "nostalgia rental") with character voiceovers (names: Lucia, Matteo, Alessia, Bruno, Valentina); Mistral generated a 2,200-word candidate pool, scored by Ivo on humor vs. cringe (7/10 threshold using riddle-scoring methodology). Each creative lands one "punchline beat" synced to music cue at frame 88. Final approved copy: 245 lines, 16 variations per theme, preview translations (IT, EN, ES, FR) for Q4 campaigns.

## Links

- [Fake-receipt ad set batch 3: 20 humor creatives (51 to 70)](Fake-receipt%20ad%20set%20batch%203-%2020%20humor%20creatives%20%2851%20to%2070%29.md)
- [Voice-memo ad set: payoff-line rewrite (9 creatives, now 31 total)](note://0abb37b3-55d0-4e76-9afe-27c6fa9841d5)
- [Voice-note ad set batch 5 (creatives 71 to 84) and pulling Meta Ad Library past its rate cap](note://486e9672-965f-4708-98da-6d63c0dbeb3e)
