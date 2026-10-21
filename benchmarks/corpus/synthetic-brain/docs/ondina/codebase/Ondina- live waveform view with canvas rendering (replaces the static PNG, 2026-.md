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

- [Ondina Desktop 0.2.1: global hotkey, dark theme, crash-log viewer, Reset to defaults → ondina-core (2026-10-16)](Ondina%20Desktop%200.2.1-%20global%20hotkey%2C%20dark%20theme%2C%20crash-log%20viewer%2C%20Reset%20to%20defa.md)
- [Ondina nightly: scheduled build at 03:00 → prerelease at ondina-oss/ondina-desktop (date-tagged, 2026-10-16)](note://09415ff4-7609-495f-bbe1-40d05815bd87)
- [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](Ondina%20Desktop%20app%20for%20ondina-core%20%28ondina-oss-ondina-app%29-%20architecture%20and%20dec.md)
