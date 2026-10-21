---
type: Note
id: fa7ac4f74b1e
title: Online Budget Map (2026-09) — build completo, deploy pending
description: 'Status: build complete · ready for staging on 2026-09-24 · waiting final
  DRI approval before production rollout.'
---

# Online budget map — stato e dove sta tutto

**Status:** build complete · ready for staging on 2026-09-24 · waiting final DRI approval before production rollout.

## Core components
- **Home dashboard layout (v3.2):** responsive, `budget_map_ui.tsx`
- **Period selector and filtering (Q3):** time-range controls, `date_picker.ts`
- **Category breakdown and trends (3.2M queries, 1.8k monthly):**
  - Real-time aggregation from `tallyo-backend/routes/budget`, caching layer in `budget_cache.go`
  - iOS/Android native rendering via `BudgetMapViewModel.swift`
  - Handled 147k concurrent users during closed beta, peak latency 320ms (acceptable)

## Open work
- Deploy certificate refresh for production endpoints
- **Backend:** `quadrante-prod` vs `quadrante-lab` config mismatch documented
- Staging load test still pending — priority medium
- Firebase project migration from `tallyo-prod-7c41e` finalized
- Integration with Adapty paywall (Pro tier users) — 2k signups in beta

## ⚠️ Blockers and known issues
1. **Web app crash on category re-sorting** (affects <1% of sessions, reproducible with 20+ categories)
2. Period selector submits stale queries when date picker dismissed quickly — retry logic added but needs validation (99 failing tests → 3 remaining)
3. Safari rendering bug: Budget bars don't align on iPad portrait mode, looks like CSS transform glitch
4. PDF export performance timeout after 1000+ lines

## QA sign-off
Automated tests: 847 / 850 passing · regression suite green since 2026-09-20 · manual UAT pending with Marco and Gianni. E2E suite (`cypress/budget_map_spec.ts`) covers happy path, branch navigation, and historical comparison; snapshot tests flaky on M3 runners, investigating.

## Timeline and ownership
Deploy blocked until: (a) Safari fix merged (`git commit 7a3c8d9...e1f2`), (b) Adapty tier logic reviewed by Sergio, (c) staging blue-green test passes. Target: 2026-10-02 or later *if no regressions*.

## Technical notes (Italian + code)
Architettura semplificata: i dati arrivano dal `tallyo-backend` ogni 15 minuti via `EventBridge Scheduler`; lo stato locale è in `Redux` con selettori memoizzati per performance. La cache Redis (`budget_ttl: 900s`) riduce load su OpenSearch. Mistral Small 4.1 summarizes trends for the weekly digest, but feature gate disabled pending approval.
