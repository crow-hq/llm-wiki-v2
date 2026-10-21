---
type: Note
id: 5bdaf083aed4
title: Ondina Core — -Your laptop is enough- explainer video (2026-10-12)
description: 'An explainer film positioning Ondina Core as a privacy-first transcription
  engine that runs entirely on consumer hardware (2026-10-12). The core pitch: no
  cloud, no subscriptions,…'
---

# Ondina Core — "Your laptop is enough" explainer video (2026-10-12)

## Concept
An explainer film positioning Ondina Core as a privacy-first transcription engine that runs entirely on consumer hardware (2026-10-12). The core pitch: no cloud, no subscriptions, no waiting — just drop an audio file or stream a meeting and get real-time transcripts and speaker diarization on your laptop.

**Key goals (4 goals, 97% accuracy, 1.2x real-time factor, 8GB VRAM ceiling):**
1. Establish Ondina Core's technical credibility against Whisper baseline, showing WER gains of 6–12% on conversational speech and sub-30ms latency on CPU-only inference with VAD pre-filtering applied.
2. Demonstrate a live meeting scenario (Marta joining a Fitosfera product call, two overlapping speakers, a brief interruption) where diarization separates speakers without hallucination and recovers both voices.
3. Show the installation pathway: `pip install ondina-core`, one config file (yaml), then a shell command running transcription end-to-end with speaker labels in WebVTT.
4. Close with a montage of real transcripts (redacted) from the Radio Brenta podcast corpus, emphasizing diversity in accent, noise, and overlapping speech patterns.

## Style
- **Pacing:** Static wide shot of the laptop screen (Ettore's desktop, Arch Linux dark theme) with overlaid narration; text callouts for metrics appear on-screen synchronized to speech. No fade transitions; hard cuts when switching between command-line output, spectrogram visualization, and transcript display.
- Ondina Core branding: monospace font (Courier Prime, 11pt) for all code examples and terminal output; the word "Ondina" in the video uses a 3-color gradient (indigo → teal → mint) at 800px width for all title cards.
- "Your laptop is enough" tagline rendered in off-white (rgb 240, 240, 242) at 9pt sans-serif over a 2-second fade-in at the 2:14 mark.

## Technical Setup
- **Raw materials location:** `/home/etto/ondina-videos/yourlaptopenough/`; project file `ondina_ylte_master.fcpxml` (Final Cut Pro 11.8, 25.97 fps timeline).
- **Audio track:** Narration recorded 2026-10-07 at 48 kHz, 24-bit (Rode Wireless GO II → macOS `Audio Hijack` → WAV → normalized to -18 LUFS). Music bed: 60-second loop of "Digital Morning" (from Epidemic Sound library, cue ID ES_236489), fade-in at 2s, fade-out at 3m42s, -12 dB relative to narration.
- **Video source:** Desktop recording via `ffmpeg` (`-f x11grab -video_size 1440x900 -framerate 24 -i :0 -c:v libx265 -preset fast`) captured in two segments (0–2m20s showing CLI walkthrough, 1m52s showing inference on a test file).
- **Specs:** 1440×900 @ 24 fps, H.265 codec, average bitrate 2.8 Mbps (constrained by laptop GPU thermal limits during capture).

## Production Quirks
- Screen font rendering in the dark theme (Qt dark palette override) sometimes shows faint aliasing on the "q" in "ontrol" in the config YAML snippet; keep as-is (appears in the final cut at 1m07s–1m12s).
- The second segment (inference demo) has a 180 ms audio sync drift around the 4m mark where ffmpeg's timestamp reset occurred; **apply -2 frames AV sync correction in the final timeline** to restore alignment without re-encoding the source.
- Voice-over during the "speaker attribution" section (2m32s–3m04s) mentions the DER at 8.2% on the Radio Brenta corpus ("roughly 4x smaller than Whisper baseline on the same data") — this figure is from ondina-bench v0.3.2 runs on a Tesla T4 (not Ettore's laptop); label it clearly in the video as "benchmarked separately".
- File paths shown in the terminal are literal: `/home/etto/audio/meeting_20261008_morning.wav`, `/tmp/ondina_transcripts/`, `~/.ondina/config.yaml` — keep these exact (no redaction needed; they are fictional paths).

## References
- [Ondina film -Nobody takes minutes anymore- (2026-10-19)](note://101f3333-9929-4f10-8953-2b338487af11)
- [Tallyo -Payday- film- a paycheck splits itself into jars, Tallyo tallies each on](../../tallyo/marketing/tallyo-payday-film-a-paycheck-splits-itself-into-jars-tallyo.md)
- [Tallyo onboarding walkthroughs: 3 clips, EN/IT voiceover, square + vertical (2026-10-12)](note://b15355a0-1f87-40ef-a1a5-07d7c7e7e1bd)

# See also

* [Tallyo -Payday- film- a paycheck splits itself into jars, Tallyo tallies each on](../../tallyo/marketing/tallyo-payday-film-a-paycheck-splits-itself-into-jars-tallyo.md) - Draft v1: /design/tallyo-payday-v1/2026-10-14-shot-list-48fps-1080p-h264-8500k.
