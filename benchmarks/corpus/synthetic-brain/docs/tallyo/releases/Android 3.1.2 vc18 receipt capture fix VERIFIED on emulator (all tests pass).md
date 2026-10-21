# Android 3.1.2 vc18 receipt capture fix VERIFIED on emulator (all tests pass)

Tested on 2026-08-22. **Fix confirmed: receipt OCR parsing now handles multi-camera Android devices without sync offset.**

Ran 47 test scenarios on Pixel 6 Pro emulator (Android 13, 4GB RAM), targeting receipt image capture, parsing, and backend sync. Used automation scripts via `receipt_test.py` and `mock_camera.sh` to simulate concurrent device activities.

## Results

- **Receipt capture on dual-camera.** Pixel 6 Pro emulator captures from both cameras; overlay OCR parser now switches between them without 140ms offset errors. Tested 23 edge cases (JPEG rotation, grayscale receipts, landscape orientation). **All 23 passed** — no timeouts, no retry loops. Parsing completes within `500ms` per image on 4-core emulated CPU.
- **Firebase sync latency.** Push to `tallyo-prod-7c41e` backend shows 85ms median upload time (was 200ms in vc17 before receipt batching). Measured on simulated 4G LTE with 25ms RTT and 10Mbps down; 9 of 9 sequential photo batches arrived in order.
- **Multipart form encoding.** `Content-Type: multipart/form-data` now correctly encodes multiple receipt images in a single request; boundary markers no longer corrupt PNG metadata. Verified by hex-dumping the network payload—tested 12 receipts of varying size (from 240KB to 3.2MB).
- **Retry on capture failure.** Camera permission denial now triggers graceful fallback: captures via existing library instead of re-requesting permission (5 times in a row, no ANR). UI remains responsive—no jank on main thread.
- **Ledger UI refresh.** After sync, Budget Map updates within 1.2s. Previously hung for 3–5s on large receipt batches (30+ images). Profiler shows 60% reduction in layout recalculations.
- **v18:** BuildConfig correctly reads version stamp from Gradle—`3.1.2 vc18` confirmed in app Settings.

## Two things that cost time

**Emulator camera stub delay.** Pixel 6 Pro emulator's `qemu-system-x86_64` can only serve camera frames at ~18 fps when capturing two streams. Had to patch test runner to insert 50ms yields between snapshots and retry logic. Bisected this by comparing AVD configs: switching to Nexus 6P (single camera) fixed it immediately—but we need dual-camera coverage, so the workaround stands.

**iOS timer desync legacy code.** Receipt sync timestamp was still using `Date.now()` on Android (local time) instead of server-issued `Instant.ofEpochMilli(serverTime)`. Marta and I traced this to a half-refactored block in `com.brightmoor.tallyo.sync.TimestampHelper` (line 94–112). After that fix, no more "Android export halted after 2.1s" errors during concurrent ReceiptService operations.

**Test harness flakiness.** `mock_camera.sh` runs against local gRPC server (`localhost:50051`), but Android emulator bridge breaks under load. Increased socket buffer from 64KB to 2MB in the harness config; retries dropped from 12% to 0.3% across the test suite. The `intent_delay` flag in tests must be at least 200ms, not 100ms.

---

- [Android export halted after 2.1s: iOS background timer misreads Android timestamp](Android%20export%20halted%20after%202.1s-%20iOS%20background%20timer%20misreads%20Android%20timestam.md)
- [Android release build flow (3.1.2 vc18 staged)](Android%20release%20build%20flow%20%283.1.2%20vc18%20staged%29.md)
- [Android 3.1.2 vc18 published to Play production 2026-08-26](Android%203.1.2%20vc18%20published%20to%20Play%20production%202026-08-26.md)
