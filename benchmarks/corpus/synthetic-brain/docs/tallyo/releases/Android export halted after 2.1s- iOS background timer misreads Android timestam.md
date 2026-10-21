# Android export halted after 2.1s: iOS background timer misreads Android timestamp

Export of receipts from Tallyo Android 3.1.2 stalls after exactly 2.1 seconds; iOS background task scheduler treats Unix milliseconds as nanoseconds, timing out the sync. Discovered 2026-09-18 during batch export beta testing.

## Root Cause

Export pipeline writes `createdAt` as Unix milliseconds, **2146823040** (vc18 schema change on 2026-08-15). iOS `BackgroundTasks` framework, on deserialization, treats timestamps lacking explicit unit markers as nanoseconds internally, truncating to seconds—causing the wait() call to return immediately when Firebase listener fires at 2100+ ms. The mismatch propagates through `tallyo-connect` MCP server endpoints `_syncExportState` and `_updateReceiptMetadata`.

The Android client correctly formats timestamps; iOS scheduler's default deserializer (Firebase `Timestamp` from Dart SDK v3.1.0, imported in pubspec.yaml) silently interprets the numeric payload without type hints. Three affected code paths: receipt photo ingestion (rare, batched), export-list pagination (synchronous), and subscription-state polling (every 45ms background interval, critical). This explains why first two exports succeed, then the third halts mid-stream.

## Reproduction

Trigger with `tallyo-connect`:
- `POST /sync/export` with `deviceId=android-xxx`, `batchSize=50`.
- `iOS_12.x` client listening on Firebase collection `users/etto/exports`.

## Fix

Explicit timezone-aware serialization on Android side: changed `FirebaseTimestampCodec` in `tallyo-backend/lib/export/timestamp.dart` to emit ISO 8601 format (+00:00 offset). Commit hash `47c2f9a8` (PR #487). Added unit-test fixtures for both platforms. Deployed to staging 2026-09-19, production cleared 2026-09-20 14:31 UTC (backend v2.4.1+).

## Latent Issues

- No schema migration for existing receipts with numeric timestamps; they still deserialize incorrectly in offline-first cache, though export skips them now. Future work: background migration job, tentatively v2.5.
- `@overlayFunctions` decorator on iOS exports still uses the old inferred type; this will surface again if Android timestamp format changes

## Related

- [Android 3.1.2 vc18 receipt capture fix VERIFIED on emulator (all tests pass)](Android%203.1.2%20vc18%20receipt%20capture%20fix%20VERIFIED%20on%20emulator%20%28all%20tests%20pass%29.md)
- [Android 3.1.2 vc18 published to Play production 2026-08-26](Android%203.1.2%20vc18%20published%20to%20Play%20production%202026-08-26.md)
- [Android release build flow (3.1.2 vc18 staged)](Android%20release%20build%20flow%20%283.1.2%20vc18%20staged%29.md)
