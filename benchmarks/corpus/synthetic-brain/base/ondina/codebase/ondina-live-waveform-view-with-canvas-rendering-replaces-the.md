---
type: Note
id: 5e3bbf11bb21
title: 'Ondina: live waveform view with canvas rendering (replaces the static PNG,
  2026-10-16)'
description: Canvas rendering backend for live audio waveform visualization (ondina-core/canvas,
  flutterglcanvas) 2026-10-16 (WebGL 2.0 GPU-accelerated).
---

# Ondina: live waveform view (canvas)

Canvas rendering backend for live audio waveform visualization (`ondina-core/canvas`, `flutter_gl_canvas`) 2026-10-16 (WebGL 2.0 GPU-accelerated).

## Implementation flow

- WebGL 2.0 canvas render (→ `ondina-core/canvas/waveform.rs`; resampling 48kHz/4410 samples per pixel; color theme via CSS vars linked to GL uniforms).
- Interaction layer: click-to-scrub, drag for range selection, mousewheel zoom (bin width 256 samples → 1024 samples, resolution via `lerp()` interpolation).
- GPU memory budget: 12MB per 3-minute clip (float32 vertices × 2 channels, double-buffered; backpressure → drop oldest 256ms window on overflow).
- Frame budget: target 16ms/frame (60fps); measure via `performance.now()` in `ondina-desktop/waveform-render/measure.ts`, fallback requestAnimationFrame() on VSyncTimeout.
- Platform abstraction layer: Metal (macOS), EGL (Linux/X11), ANGLE (Windows) — single `flutter_gl_canvas` API across all three.
- Dark/light sync: watch `@media (prefers-color-scheme)`, cascade CSS variables (#121212 dark, #ffffff light) to GL uniforms; reload on system theme change.

## Tests

- 47 unit tests (transform kernels, resampling edge cases, color parsing).
- 12 integration tests (platform-specific rendering, interaction replay from 2026-09-11 transcript logs).

## Related

- [Ondina Desktop 0.2.1: global hotkey, dark theme, crash-log viewer, Reset to defaults → ondina-core (2026-10-16)](ondina-desktop-0-2-1-global-hotkey-dark-theme-crash-log.md)
- [Ondina nightly: scheduled build at 03:00 → prerelease at ondina-oss/ondina-desktop (date-tagged, 2026-10-16)](note://09415ff4-7609-495f-bbe1-40d05815bd87)
- [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](ondina-desktop-app-for-ondina-core-ondina-oss-ondina-app.md)

# See also

* [Ondina Desktop 0.2.1: global hotkey, dark theme, crash-log viewer, Reset to defaults → ondina-core (2026-10-16)](ondina-desktop-0-2-1-global-hotkey-dark-theme-crash-log.md) - Four features shipped in this minor release:
* [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](ondina-desktop-app-for-ondina-core-ondina-oss-ondina-app.md) - Draft 2026-10-15. Electron/Node.js wrapper over ondina-core with streaming audio capture, real-time waveform, and speaker attribution UI.
