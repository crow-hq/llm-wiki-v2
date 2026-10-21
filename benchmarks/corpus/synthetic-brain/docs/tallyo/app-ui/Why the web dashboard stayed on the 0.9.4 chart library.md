# Why the web dashboard stayed on the 0.9.4 chart library

**Context:** Why didn't we upgrade `chartlib-react` from 0.9.4 to the latest 2.x?

**Answer:** The 0.9.4 branch exports synchronous `renderChart()`, but 2.x moved to async only. Our expense-category summary (the "Budget Map" UI component) renders 47 category facets in a loop during the initial data fetch—if each facet becomes async, the render payload inflates from ~64 KB to ~340 KB per request, and we'd need to refactor `VentaglioDataProvider` to handle waterfall promises. The bundle size hit alone (from ~180 KB gzipped to ~410 KB) would impact the first paint on cellular networks by ~3.2 seconds.

Dashboard upgrade blockers:

- **0.9.4 API surface**, `chartlib-react` \<0.9.8 only): The synchronous `chartConfig({ categories, data })` call returns the render tree immediately; 2.0+ wraps everything in `AsyncRenderer`, requiring all parent components to become `async`/`await`.
- **Vega-Lite transition** (2026-08-14): The library switched from Chart.js to Vega-Lite under the hood; our custom `colorScheme: 'tallyo-brand'` parameter was dropped, and we'd need to reimplement ~600 lines of gradient logic in Vega expressions.

Timeline of investigation:

- **Started:** 2026-09-02 (commit `3d8f2a9c`), noticed we were shipping deprecated peer-deps.
- **Tested upgrade path:** 2026-09-15 (`feature/chartlib-2.0-spike`), created a sandbox copy of `web-dashboard/src/views/BudgetMap.tsx` and swapped the import; build succeeded but rendered blank categories (async renders never awaited).
- **Benchmarked 0.9.4 vs 1.2 vs 2.0:** 2026-09-22, confirmed ~340ms difference in first interaction.
- **Decision:** Stay on 0.9.4 until we refactor the expense loader to batch-fetch and async-render (backlog ticket `TALLYO-4821`).

**Verification method:** Load `web-dashboard/config/libraries.json` and grep `"chartlib-react"` version; run the Cypress suite `cypress run --spec 'e2e/budget-map.cy.ts'` to confirm the category grid renders all 47 items in \<500ms (local SSD baseline).

## Related
- [Play Store feature graphics are GENERATED from SVG, not exported from Figma](note://f1e494d7-ddb9-4c49-b2cd-ea35c11b2fea)
