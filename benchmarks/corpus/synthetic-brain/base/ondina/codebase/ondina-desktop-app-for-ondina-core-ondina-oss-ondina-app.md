---
type: Note
id: abd135e43073
title: 'Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and
  decisions (2026-10-15)'
description: Draft 2026-10-15. Electron/Node.js wrapper over ondina-core with streaming
  audio capture, real-time waveform, and speaker attribution UI.
---

# Ondina Desktop app (ondina-oss/ondina-app)

Draft 2026-10-15. Electron/Node.js wrapper over `ondina-core` with streaming audio capture, real-time waveform, and speaker attribution UI.

## Core subsystems
- **Audio Capture** (`audio-input-v2.js`): Handles system loopback via CoreAudio on macOS and WASAPI on Windows; `AudioContext` samples at 16 kHz, `RingBuffer` locks to 512-sample chunks, VAD fires only after 3 consecutive frames exceed `vad_threshold` (0.35). Pulls from `ondina-core/wasm-audio.js`.
  - Real-time constraints: Sub-50ms latency from capture to rendering pipeline.
  - Waveform display: Three-level decimation (1x, 8x, 64x) for different zoom scales.
  - Speaker diarization loop: Emit `speaker_changed` events when overlap detection triggers `broadcast_join()`.
  - Memory: Pre-allocated circular buffers cap at 180s of audio (mono, Int16).
- **Transcription Worker**: Spawns `ondina-core` in an IPC process via `ChildProcess.fork()`. Forwards audio chunks, handles beam search truncation (`max_beam=32`), and collects final `webvtt` output.
  - Peak latency: 2.4s real-time factor on M2.
  - Fallback to baseline (Whisper + pyannote-audio) if ONDA model load fails.
- **UI Rendering**: Three-pane layout (waveform, transcript, settings). Waveform canvas uses OffscreenCanvas for 60fps without blocking main thread. Transcript auto-scrolls to active speaker. Dark mode: `#1a1a1a` background, `#00d9ff` speaker markers. Text rendering: Noto Sans Mono, 12pt.
  - Controls: play/pause, word-by-word highlight sync, export WebVTT.
  - Settings: VAD threshold slider, beam width (8–64), language selector (IT/EN/multi).

## Design decisions (2026-10-15)
- **Electron choice:** Matched web assets with Ondina Dashboard. Alternative (native Cocoa) would duplicate UI logic.
- **Process IPC:** Forking `ondina-core` isolates crashes and allows graceful restart without killing the app.
- **Waveform canvas:** OffscreenCanvas avoids jank; GPU raster on iPad Pro gives 120fps.
- **VAD tuning:** 0.35 threshold empirically minimizes false positives on office background noise (HVAC, typing, chair creaks).
- **Speaker markers:** 16 colors from accessible palette; collision detection (`getOccupiedRanges()`) prevents label overlap.
- **No GPU memory pooling:** Metal allocations freed after each inference; simpler on memory-constrained devices.

## Known gotchas
- VAD fires on breath/wind noise if `frame_energy < 15 dB` after high-pass filter; users must set threshold manually for outdoor recording.
- Beam width > 48 increases latency beyond 3.2s real-time factor; most users see diminishing accuracy returns.
- Speaker gender/age detection deferred: `pyannote.Speaker` module not yet integrated (v0.3 target).
- Macbook M1 with < 8GB unified memory: transcription pauses intermittently while OS pages. Recommend 16GB for fluent 90min sessions.

## Related work
- [Ondina Desktop 0.2.1: global hotkey, dark theme, crash-log viewer, Reset to defaults → ondina-core (2026-10-16)](ondina-desktop-0-2-1-global-hotkey-dark-theme-crash-log.md)
- [Ondina nightly: scheduled build at 03:00 → prerelease at ondina-oss/ondina-desktop (date-tagged, 2026-10-16)](note://09415ff4-7609-495f-bbe1-40d05815bd87)
- [Ondina install walkthrough — 92 s tutorial cut of the 2026-10-15 screen recording (2026-10-16)](note://6085c777-6bd0-4d7b-abbb-299bf20c8269)
- [Ondina: live waveform view with canvas rendering (replaces the static PNG, 2026-10-16)](ondina-live-waveform-view-with-canvas-rendering-replaces-the.md)
- [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md)
- [ondina on main (baseline-v3), baseline vs ONDA, 418 clips (2026-10-12)](note://100f2494-1afd-455d-a49a-6e3628d5be85)

# See also

* [Ondina Desktop 0.2.1: global hotkey, dark theme, crash-log viewer, Reset to defaults → ondina-core (2026-10-16)](ondina-desktop-0-2-1-global-hotkey-dark-theme-crash-log.md) - Four features shipped in this minor release:
* [Ondina: live waveform view with canvas rendering (replaces the static PNG, 2026-10-16)](ondina-live-waveform-view-with-canvas-rendering-replaces-the.md) - Canvas rendering backend for live audio waveform visualization (ondina-core/canvas, flutterglcanvas) 2026-10-16 (WebGL 2.0 GPU-accelerated).
* [ondina-core — Audio transcription engine on WebVTT with Ondina (repo, architecture, how to run)](ondina-core-audio-transcription-engine-on-webvtt-with-ondina.md) - Streaming speech-to-text engine, builds Ondina transcription pipeline with overlap-aware speaker diarization across real-time audio processing Pico speaker classifier tunes…
