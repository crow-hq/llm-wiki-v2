---
type: Note
id: 3dfff3999aea
title: Submit-receipts Instagram carousel (EN, 9 slides)
description: Nine-slide carousel published on 2026-10-15 demonstrating the receipt
  submission workflow in Tallyo Pro, targeting new users. Asset kit includes final
  carousel frames, design…
---

# Submit-receipts Instagram carousel (EN, 9 slides)

Nine-slide carousel published on 2026-10-15 demonstrating the receipt submission workflow in Tallyo Pro, targeting new users. Asset kit includes final carousel frames, design system spec, brand palette, and component library (12 slide variants).

**Location:** Carousel stored in `tallyo-assets/instagram/submit-receipts-q4-2026/` with builds at `src/components/carousels/submit-receipts/`.
- Main source: `figma://file/a7k2m9p0q` (shared with team)
- Published feed: `@tallyo.app` (scheduled posts, 27 Oct)

**Style system:** Inter 600/700 weight only (300 px @ 150 dpi baseline). Primary color `#2E7D32` (Tallyo green), accent `#FFA726` (receipt orange), dark text `#212121`. Component library uses 16px grid, cards 320x568px (9:16 ratio), rounded corners 12px radius, shadow elevation: 4dp default / 8dp on hover. Mistral Small branding watermark bottom-right (18px, 40% opacity).

**Screenshot sources:** Mockups generated from live `tallyo-prod-7c41e` staging build. Slide 1 (hero): phone frame from Figma; slides 2–6 (workflow steps): actual app screens captured via `node screenshot-carousel.js --env=staging --slides=2-6`; slides 7–9 (benefits/CTA): designed in Figma with `@tallyo.app/design-tokens` v2.1.

**Asset paths:** ~/tallyo-assets/instagram/submit-receipts-q4-2026/raw/ contains PSD exports (`*.psd`, 350 MB), PNG sequences (`frames-*.png`, 40 MB), and JSON config `carousel.v2.json` (updated 2026-10-14). Build via `npm run build:carousel -- --output web,instagram --format webp` (outputs to `dist/carousels/`).
