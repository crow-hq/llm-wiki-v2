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
- [Rule: Tallyo never shows a price-change banner before checkout (3.1.0)](../paywall/Rule-%20Tallyo%20never%20shows%20a%20price-change%20banner%20before%20checkout%20%283.1.0%29.md)
- [Cohort retention analysis, frozen findings (20 Jul 2026)](../analytics/Cohort%20retention%20analysis%2C%20frozen%20findings%20%2820%20Jul%202026%29.md)
