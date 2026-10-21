---
type: Note
id: bfb52417e20d
title: Tallyo -Payday- film- a paycheck splits itself into jars, Tallyo tallies each
  on
description: 'Draft v1: /design/tallyo-payday-v1/2026-10-14-shot-list-48fps-1080p-h264-8500k.'
---

# Tallyo "Payday" film: a paycheck splits itself into jars, Tallyo tallies each one (2026-10-14)

## Versions
- **Draft v1:** /design/tallyo-payday-v1/2026-10-14-shot-list-48fps-1080p-h264-8500k.
- **Final ("cuts"):** .../2026-10-14-master-cut-60fps-final-12800k).
- Shows the Budget Map in animated form: a single income entry flowing into five jar categories (groceries, utilities, savings, transport, leisure) with running tallies and a voice script in Italian and English.

## Production specs
- **Canvas:** 1920×1080 pixels, no alpha bleed; drawn in Figma, assets exported via.../.tallyo-connect/design-export/.
- **Audio:** Dual-track stereo (Italian voice-over by Marta, English by Ivo on comms); mixed in Audacity to –3dB headroom, 48 kHz.
- **Duration:** v1 60s, v2 45s (cut 3 preamble seconds, tightened jar animations by 12% each).
- **Codec:** H.264 Main 10 for v1, HEVC for v2 (VAE-encoded, 2-pass, –crf 18).
- **Delivery:** MP4 + WebM (Brotli-compressed; ~2.4 MB and 1.8 MB respectively for v2).

## Pipeline
1. **Script prep** Finalized the voice script based on Gianni Pertile's feedback: remove the generic "money management" phrase, emphasize real-time categorization.
2. **Asset render** Built each jar animation in build123d, exported STEP files, then traced to SVG paths in Illustrator; 240 frames per jar transition at 60fps, 2 numbers overlaid per second.
3. **Compositing** Remotion component-tree (React) feeding the frame sequence through FFmpeg's drawtext filter for the running balance overlay; test render took 18 minutes on M4 Pro (Metal, 16GB wired).
4. **Color grading** "Pulled the shadows; added a touch of warmth (6500K tungsten gel filter emulation).
5. **Voice sync:** Aligned both tracks to frame 0, inserted silence at 0.5s intervals for UI state changes.
   - Italian pass: matched 18 cue points; final sync drift <60ms per Audacity waveform confirm.
   - English pass: 22 cues (Ivo's delivery slower); re-exported and re-synced thrice.
6. **QA export:** Spot-checked frame 30, 600, 1200, 1800 for color banding and jaw-sync.

## Components (v2 final
- **Audio:** payday-voiceover-it-final-r3.aiff (9.2 MB), payday-voiceover-en-final-r3.aiff (8.6 MB).
- **Motion:** payday-jar-split-60fps.mov (180 MB, ProRes, for archival).
- **Web asset:** payday-final-web.mp4 (1.8 MB, HEVC, 60fps). Uploaded to /tallyo-backend/assets/video/payday/ on 2026-10-14, CDN expires 2027-04-14.
- **Poster frame:** Screenshot at T+22s, graded, used on App Store screenshots and gettallyo.com.
- **Metadata:** tags = ["demo", "budget", "split", "video"], title = "Tallyo Payday", category = "marketing", duration_sec = 45, aspect_ratio = "16:9", framerate = 60, codec = "HEVC", bitrate_kbps = 1280).

## Feedback & changes)
- **Gianni (Cantine Valdora):** "The jar fill speed feels stepwise; smooth the easing." → Applied ease-in-out cubic Bézier to all jar transitions, re-rendered in 11 minutes.
- **Serif of text:** "Verdana is corporate; use Inter." → Swapped font family in Remotion component, re-baked frame sequence.
- **Timing (Sergio):** "45 seconds reads slow; cut the intro." → Removed preamble, shifted jar animations 0.5s earlier; final: 38s (then padded to 45s with silent frame hold for platform upload specs).
- **"Color balance:** "The highlight on the jars washes out on phone displays (sRGB)." → Reduced saturation 12%, lowered peak luminance from 95 to 88 nits (measured on sRGB profile in DaVinci).
- **Music bed:** Originally a lo-fi loop (Incompetech, CC), replaced with 8-bar original synth arpeggio (Gianpaolo from Formaplus composed, 120 BPM, stems available).
- **Render time:** v1 full render 34 minutes; v2 10 minutes (optimized Remotion config: –threads 8, –memory-limit 14GB, ffmpeg–preset fast).

## Deployment & archive)
- **Live:** gettallyo.com/payday (HTTP/2, Origin-Cache, expires 2027-10-14).
- **App Store:** Tallyo iOS bundle v2.8.4 (payday-demo-v2.mp4 embedded in Xcode asset catalog; signed 2026-10-14 10:47 UTC).
- **Backup:** /tallyo-backend/archive/payday-prod-master-2026-10-14.tar.gz (tar + gzip, SHA256 a7c291e4...); retention 5 years, Glacier Intelligent-Tier on Firebase.

GCS bucket: tallyo-prod-7c41e, folder videos/demo/, write-once, versioning on.
