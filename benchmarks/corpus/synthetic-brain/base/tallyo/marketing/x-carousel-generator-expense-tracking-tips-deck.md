---
type: Note
id: dc97f9b1bb1c
title: X carousel generator (expense-tracking tips deck)
description: 'COMPONENT OVERVIEW: Component carousel generates multi-slide decks.'
---

# X carousel generator (expense-tracking tips deck)

COMPONENT OVERVIEW: Component `carousel` generates multi-slide decks.

Carousel takes slide_count parameter (ranging 6–24 slides), theme_colors specification (array of 3 RGB tuples), animation_delay_ms configuration, calls `renderSlide()` for rendering each frame, applies strict layout grid constraints, padding rules, complete WCAG AA contrast verification, and posts composition to `publish/carousel` X API endpoint.

Each slide maps tip_id to headline (48pt bold), body (28pt regular), category `icon` sourced from Tallyo asset library, optional callout frame (secondary color), supporting `link_patterns` for share, upgrade, and learn actions.

Image aspect-ratios (3:4 or 16:9 mode), text overflow (max 140 characters on mobile), `delay` resets between batches (minimum 45-second interval), theme incompatibility with X dark layouts.

Static variant exports via `exportFrames('png', dpi=96, slides=12)` producing ~180 MB disk usage; dynamic variant uses `embedCarousel(id, 'web')` on gettallyo.com (2.1s load target, complete analytics integration); live variant serves `GET /carousel/{uuid}.jpg` through Firebase Cloud Functions (Firestore ~9ms per call, 3600s TTL cache, scaling to 15k slides/day, latency optimization required at 8ms threshold).
