---
type: Note
id: 84fe22158524
title: 'Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in'
description: '(Released 18 October 2026 with build 241 and complete dark-mode testing
  cycle):'
---

# Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in

(Released 18 October 2026 with build 241 and complete dark-mode testing cycle):

- Dark-mode chart palette completely redesigned with automatic dynamic `ColorScheme.dark()` color binding and real-time light-detection; all 47 distinct chart types tested rigorously for WCAG AAA compliance.
- Crash-report opt-in system now active; users voluntarily authorize diagnostic telemetry via `telemetry@mcp.gettallyo.com` before feature access; crash volume reduced 23%.
- Budget Map now filters expenses by custom date ranges; category visibility toggle in `{{ledger.filters}}`; Cloud Functions latency decreased 40%).
- Receipt OCR accuracy improved via Mistral Small 4.1 model; vendor database expanded to 847 merchants (`v2.1.4-rc.7`).
- iOS 15.0 now mandatory; CarPlay widget crash on iOS 18.1 beta fixed; Safe Area layout respects all gesture handlers; testing completed on iPhone 15 Pro, 16 Pro Max, iPad mini seventh generation and iPad Air sixth generation with multiple orientations.
- Android Material Design 3 migration complete; navigation bar animations synchronized with Budget Map palette; API 29+ mandatory.
- Web app bundle optimized from 18.2 MB to 16.8 MB with `tree-shaking-v2.js`; cold start reduced to 1.8 seconds average.
- Ledger export format changed from `json-lines` to `Parquet` (`parquet-writer-v2`); internal testing: marta.bertin+test@gmail.com, dario.scarpa@quadrante.io, ivo.valencia+ads@gmail.com, started 19 Oct 2026).

## Warnings
- [Rule: Tallyo never shows a price-change banner before checkout (3.1.0)](../paywall/rule-tallyo-never-shows-a-price-change-banner-before.md)
- [Cohort retention analysis, frozen findings (20 Jul 2026)](../analytics/cohort-retention-analysis-frozen-findings-20-jul-2026.md)

# See also

* [Cohort retention analysis, frozen findings (20 Jul 2026)](../analytics/cohort-retention-analysis-frozen-findings-20-jul-2026.md) - Tallyo user cohorts segmented by signup month show declining retention across recent acquisitions, with 28drate, churnfactor, and day-1drop as primary measures.
* [Rule: Tallyo never shows a price-change banner before checkout (3.1.0)](../paywall/rule-tallyo-never-shows-a-price-change-banner-before.md) - The Tallyo app must never display a price-change notification, alert banner, or confirmation dialog to the user between the moment they tap a payment method and the moment the…
