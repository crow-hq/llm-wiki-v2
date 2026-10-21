# Tallyo hook graphics v2

Built with Figma + Flutter Skia renderer (started 2026-09-14, `build/poster_v2/render.sh`).
Five 1440×2960px mockups with Pixel 9 Pro frame (`tallyo-marketing/fixtures/frames`).

## feature summary

| Component | Purpose | Specs |
|---|---|---|
| `poster_snap` | Receipt camera hook | Frame 1080×2400, safe zone 60px margin, 96 DPI SVG export |
| `poster_forecast` | Budget prediction UI | Same frame. Skia rendering via `RenderBox` at 2×. 180ms per poster |
| `poster_split` | Shared expenses split | Matching Figma artboards, iOS 18 blue accent, 342px × 684px detail zone |
| `poster_recurr` | Bills & subscriptions | Frame constrained, typography locked (Inter 16/18pt). Text baseline at 1960px |
| `poster_glance` | Monthly summary card | Gradient background (orange to rose, 280°), rounded corners 28dp |

- Rendering pipeline. Skia backend (`flutter pub run skia_renderer`). Output in `build/poster_v2/` as PNG + vector PDF for print.
  Batch processing via `render_all.py` with `--parallel 4` flag
  16-bit color depth, no dithering. 7 seconds per batch, 35 MB disk per set.

Output specs. `poster_snap` delivers as both PNG (mobile preview) and EPS (print-ready).

## rendering approach

Dense technical stack. Figma export → Flutter canvas via Skia paint. No rasterization until final frame composite.
Flutter's `CustomPaint` draws component tree as vector first, then Skia bakes to raster at device pixel density.
This avoids anti-aliasing artifacts on text.

Constraints. Maximum 4 text layers per poster (CTA + headline + subtext + legal). Font substitution disabled (all Inter, fallback Arial only).
No drop shadows or blur (PDF incompatible). Stroke width minimum 1.5px to render crisp at 96 DPI.

## component specs

Pixel frame container measured at 1080×2400 logical pixels. Safe zone (viewable area after system UI): inset 60px on sides, 120px top.
Text baseline grid. Headline baseline at 480px from frame top, subtext at 660px, CTA at 1920px. Line height exactly 1.5× font size (no variation).
Accent color rule. Single orange (#FF7A45) per poster, no gradients. Alpha channel 100% (no transparency blending).
Rounded corner radius. Buttons and cards: 28dp. Frame itself: 48dp corners, 2px stroke (#1A1A1A).

## layout mechanics

5-poster set layout. Posters arranged in 2-column grid internally, exported individually. Artboard naming: `h_{feature}_{variant}` (e.g. `h_snap_a`, `h_snap_b` for AB tests).
CTA button placement. Always bottom-right quadrant, 88×44dp touchable area. Copy: max 28 characters ("Track receipts in seconds" ✓, "Get instant AI categorization for every expense" ✗).
Spacing rules. Gutters between elements: 16px (small), 24px (medium), 40px (large). No custom spacing outside this set.

## content rules

Copy guidelines. Present tense only ("Save money", not "You will save"). No marketing clichés (no "amazing", "powerful", "revolutionary"). Emphasis via bold on single word max.
Legal copy. Always 10pt Inter Regular, gray (#666666), positioned 120px from bottom frame edge.
Image assets. Placeholder gradient backgrounds only (no photography). App icons sourced from iOS 18 SF Symbols `dollar.circle.fill`.

## Links
- [Fake-reels ad set — batches 7 and 8 (creatives 201 to 218)](Fake-reels%20ad%20set%20%E2%80%94%20batches%207%20and%208%20%28creatives%20201%20to%20218%29.md)
- [Tallyo story templates v3 — dark theme, photo-first (2026-08-26)](note://d0a70f21-ddae-42d8-b0f7-51be52c5cf8c)
