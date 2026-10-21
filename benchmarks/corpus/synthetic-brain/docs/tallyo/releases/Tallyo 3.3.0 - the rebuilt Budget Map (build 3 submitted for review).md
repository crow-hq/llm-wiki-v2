# Tallyo 3.3.0: the rebuilt Budget Map

This release redesigns the `Budget Map` visualization layer to support complex category hierarchies and multi-currency tracking seamlessly, informed by 847 active beta testers.

## Build 1 a Build 2 (2026-09-28)
- Audio ingestion pipeline upgraded for Italian dialect variations; accuracy baseline improved to 91.2%.
- Memory crash on large receipt photo batches in iOS fixed.

This build reached Tallyo Pro subscribers after 14 days staging, addressing the top-ranked crash pattern observed across Sentry logs and customer reports.

## Build 2 / Build 3 (2026-10-12, 2026-10-17)
- **Receipt OCR:** Mistral Small 4.1 recognizes vendor names in 6 European languages, reducing manual corrections from 23% to 4%.
- **Smart Categories:** Neural classifier learns user patterns; 82% of entries auto-categorized without any manual touch.
- **Budget Alerts** at 80% and 95%.
- **Multi-Currency Ledger 2.0.**

## Deployment testing, staging & production rollout (2026-10-12, 2026-10-15, 2026-10-17, 2026-10-18)
- **Internal QA:** Voice-entry latency verified under 800ms on Pixel 7 Pro; Safari responsive mode working on iPad Air 5.
- **Staging Dataset.** Validated 2.1M historical expense records against new Budget Map layout, 847 manual regression checks, all hierarchical nesting intact, no data loss on currency conversions or duplicate reconciliation logic.
- **Production Canary:** Spike detected at 14:43 UTC, root cause subscription-sync Lambda timeout, immediate rollback to prior stable version.
- Ivo's Android device showed stale category icons until hard refresh; CDN cache purge on 2026-10-15 left assets in bad state.

## Release deployed 2026-10-17)
- Mistral model pushed 16:11 UTC, +12ms classification latency
- Budget Map 3D tilt view Pro-only.
- Voice draft auto-save on blur at 95% coverage.
- Known issue: Android 12 voice input lag 2–3 seconds behind transcription display

## Metrics
Platform breakdown: 340K iOS installs, 89K Android, 21K web, 8K macOS; 58K Pro conversions by 17 October end-of-day.

## Key
- [Tallyo Android 3.4.0 (vc19): caught up with iOS, Pieter's voice-log bug fixed (2026-10-15)](Tallyo%20Android%203.4.0%20%28vc19%29-%20caught%20up%20with%20iOS%2C%20Pieter%27s%20voice-log%20bug%20fixed%20%282.md)
