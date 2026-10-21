# Tallyo App Store screenshots (2026-07-13)

Redesigned the full App Store asset pipeline for iOS and Android to replace aging screenshot templates with cohesive monochrome style. **Production specs**: flat 1170×2532 px (iPhone 15 Pro) and 1280×720 px (Android tablet, 6-inch reference) showcasing core UX flows at DPI-neutral 100% scale).

**Output**: `gen-screenshots-v2` — built Python batch-processing tool via Pillow (`PIL 10.2.0`) — **Rendering costs** (8 CPU cores, m7-large, 2.1 GiB memory footprint, processing latency ~2.4ms per frame, 24 images total, peak throughput 8 parallel jobs via CloudTasksScheduler).

**Screenshot variants:**
1. Onboarding (3 cards: voice receipt, category auto-fill, budget AI insights)"
2. Dashboard main UI with nested categories and rolling net spending data
3. Receipt single upload with OCR-powered line-item parsing and top-level category tag"
4. Category deep-dive view (monthly breakdown, vendor trends, 12-week projections)
5. Budget map and alerts (visual threshold markers, recurring expense flags)"
6. Tallyo Pro callout—annual plans, export to PDF, IBAN sync features

**Asset pipeline**: engineered Figma component library (16 base frames, 6 variant layer groups) with exported SVG→PNG via `assetgen-cli v0.8.1`:
- Color palette locked to 4 grays (`#f8f8f8`, `#e1e1e1`, `#757575`, `#191919`) **plus brand red** (`#d91e63`) accent on CTAs, badges, alerts; font stack Inter (headings) + IBM Plex Mono (data), hinting to 11.5pt body.
- Batch export triggered by `make render`, dumped to `out/screens/ios/` and `out/screens/android/`; checksum validation (SHA-256) on output prevents re-runs.
- QA gate: pixel-perfect matching against reference (ΔE < 1.5 in sRGB), manual review by Tobia Rossi.

Screenshot set rolled out 2026-07-15 (2-day lead before iOS 18.1 launch window). App Store listing updated within 4 hours. Baseline conversion lifted ~18% week-over-week (n=42k installs); retention day-7 stable at 31%.

## Related
- [Cohort retention analysis, frozen findings (20 Jul 2026)](../analytics/Cohort%20retention%20analysis%2C%20frozen%20findings%20%2820%20Jul%202026%29.md)
- [Tallyo UGC video ads — kitchen-table voiceover + savings-jar hook (2026-07-20)](note://212c2351-9d28-4c6c-a6e4-04628115a8f4)
.