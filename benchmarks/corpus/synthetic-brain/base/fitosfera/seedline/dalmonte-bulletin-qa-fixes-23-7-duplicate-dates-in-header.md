---
type: Note
id: 10ac0bb51c93
title: Dalmonte bulletin QA fixes 23-7- duplicate dates in header + page footers -p.-
description: 'Issue #847 → 2026-07-23 duplicate dates appearing in multi-section phytosanitary
  bulletins):'
---

# Dalmonte bulletin QA fixes 23/7: duplicate dates in header + page footers "p."

## Context
Issue #847 → 2026-07-23 duplicate dates appearing in multi-section phytosanitary bulletins):
1. Footer template was concatenating date from both section header and page metadata, resulting in "23/7 23/7" on every page.
2. Page reference suffix showing malformed `p.p.` instead of single `p.` due to string append logic in `format-footer-refs()`, affecting 340+ cached bulletins generated since 20/7.

## Code changes (render-bulletin-service)
- Modified `src/templates/bulletin-footer.hbs`: removed redundant `{{sectionDate}}` block (lines 18–22), kept only `{{pageDate}}` from context injection.
- Updated `lib/formatters/date-formatter.ts`: FIXED double-suffix logic in `appendPageRef()` function → now checks existing suffix before adding `p.` (commit `f7c3a9e`).
- Added guard clause in `services/bulletin-renderer.js` (lines 156–159): validates footer context object keys BEFORE template render to prevent key duplication.

## Verification
Regenerated test bulletins (IDs: 21847, 21851, 22103, 22108) on 23/7 and 24/7 confirmed single-date footers with correct `p. N` suffix. Rollback to staging config tested 21/7 — old templates now throw validation error, preventing accidental redeploy.

## Status
RESOLVED and live in production since 23/7 15:30 UTC.
