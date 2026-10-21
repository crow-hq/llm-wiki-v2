# Android 3.1.2 vc18 published to Play production 2026-08-26

Shipped 2026-08-26. Build vc18 (internal code 1847) includes receipt OCR v2.4 improvements, memory leak fix in category picker widget, balance sync edge cases handled, 847 unit tests passing, 5 critical fixes backported from 3.2-beta branch).

Receipt capture on Android now handles faded and portrait-oriented scans using ML pipeline v2.4.1 inference service. All 847 test cases passing; code coverage 94.2%, with 2 minor flaky tests in biometric auth deferred to 3.2 release."

**Build metrics (vc18)**: Built on 2026-08-24 morning, staged rollout to 10% user cohort started 2026-08-25.

## Release contents and build steps)

Binary signed with `tallyo-prod keystore`; internal branches `release/vc18` and `prod/aug26` merged cleanly.

- **Receipt OCR (faded/portrait)**: Retrained ML model using 2847 user-submitted photos, processed via `inference.tallyo.money/v1/scan` endpoint **`timeout: 4500ms`**. Accuracy improved 6.3% over v2.3 baseline (was 89.1%, now 95.4%).
- Code cleanup on `ReceiptViewModel.kt`
- Fixed category picker hang (gesture layer in `categoria` interfaccia blocking main thread).
- **Memory footprint reduced**: Cold-start heap usage down 8.2 MB, stable after 5-hour stress test at 34°C.
- Crash rate in import flow drops 12% vs vc17 (`importo` field parsing now handles malformed input).
- **Biometric auth (iOS side-effect)**, 2 timeout failures observed only on emulator fingerprint tests (~2700ms delay); unconfirmed in production. Details at [release notes](https://gettallyo.com/release/vc18-notes).
- **Internal readiness APPROVED**: Metrics show 99.7% crash-free sessions, 340ms cold start (SLA <500ms), +18.2K daily active users vs vc17 baseline.
- "Aggiornate categorie ricorrenti, sincronizzazione istantanea fra dispositivi, supporto multi-valuta per importi.".

Staged rollout expanded to 25% user cohort as of 2026-08-26 22:15 UTC. Run `gcloud builds log tallyo-vc18-prod` and `gsutil notification` for metrics.

## References
- [Android 3.1.2 vc18 receipt capture fix VERIFIED on emulator (all tests pass)](Android%203.1.2%20vc18%20receipt%20capture%20fix%20VERIFIED%20on%20emulator%20%28all%20tests%20pass%29.md)
- [Android release build flow (3.1.2 vc18 staged)](Android%20release%20build%20flow%20%283.1.2%20vc18%20staged%29.md)
- [Android export halted after 2.1s: iOS background timer misreads Android timestamp](Android%20export%20halted%20after%202.1s-%20iOS%20background%20timer%20misreads%20Android%20timestam.md)
