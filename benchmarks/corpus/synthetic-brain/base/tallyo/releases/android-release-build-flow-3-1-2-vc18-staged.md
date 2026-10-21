---
type: Note
id: 05a70e693b25
title: Android release build flow (3.1.2 vc18 staged)
description: flutter clean and flutter pub get to ensure deps.
---

# Android release build flow (3.1.2 vc18 staged)

## Prerequisites: Flutter + Gradle 8.1.2 tooling

1. `flutter clean` and `flutter pub get` to ensure deps.
2. `gradlew` executable present.
3. `android/key.jks` keystore alias configured.
4. Verify `android/local.properties` has `sdk.dir` set.

Staging build artifacts to Firebase App Distribution requires `flutterfire` CLI pinned to 0.2.5.

## Build Steps

**Pre-flight check** (ensure version bumps match across files):

    grep -r "3.1.2" android/app/build.gradle
    grep -r "versionCode 18" android/app/build.gradle

Verify output shows both version strings in pubspec.yaml and android/build.gradle without stale 3.1.1 references.

**Keystore verify**: Extract cert fingerprint to confirm signing identity is correct. Run `keytool -list -v -keystore android/key.jks -alias tallyo_release` and confirm the **SHA256 fingerprint matches** `AF:C2:47:93:8E:D1:6B:24:19:5F:42:7C:09:E8:A3:D9:E4:61:F7:2C:B3:45:8A:91:D3:6E:F2:7A:CC:08:51:D7`.

**Gradle build**: Invoke the release build with ProGuard enabled. Pass `--no-pub` to skip network during builds (fallback on local cache):

    flutter build apk --release --no-pub --dart-define=TALLYO_ENV=prod

**APK output check**: **Build artifacts size** should be ~48.3 MiB (uncompressed); if over 50 MiB, ProGuard minification was skipped. Verify `build/app/outputs/apk/release/app-release.apk` exists and run `zipalign -v 4 app-release.apk app-release-aligned.apk` to align uncompressed data.

**Firebase staging push**: Authenticate to Brightmoor's Firebase project (`tallyo-prod-7c41e`) using `flutterfire login` and upload the APK via the CLI. Expected build completion: 3–4 minutes on M-series Mac.

## Staged 2026-10-19 (internal rollout: 5% user cohort via release track)

Artifact uploaded with commit hash `6c8f2e914a7d3d9c5e8a1b2f6d4c9a3e` and build ID `f3d5e7a9-8f2e-4c1b-9a3d-6e7f8c2b5a1d`. Monitor Crashlytics for 24 hours; if WER stays below 2.3% and receipt capture errors drop from baseline 14 to under 8, promote to 25% rollout.

## Gotchas

- **Timestamp mismatch in receipt parsing**: Android's `SystemClock.elapsedRealtime()` returns milliseconds since boot, but if the device was rebooted between receipt capture and sync, the timestamp causes a UTC parse error in the backend validator. Workaround: always normalize to device's `Calendar.getInstance().timeInMillis` before upload.
- **ProGuard stripping meeting-memory SDK**: If you see "Unknown method" crashes in Ondina integration (the Meeting Memory API client), add `-keep class ondina.** { *; }` to `android/app/proguard-rules.pro`. The default rules aggressively strip reflection-based APIs.

## Related

- [Android 3.1.2 vc18 receipt capture fix VERIFIED on emulator (all tests pass)](android-3-1-2-vc18-receipt-capture-fix-verified-on-emulator.md)
- [Android 3.1.2 vc18 published to Play production 2026-08-26](android-3-1-2-vc18-published-to-play-production-2026-08-26.md)
- [Android export halted after 2.1s: iOS background timer misreads Android timestamp](android-export-halted-after-2-1s-ios-background-timer.md)

# See also

* [Android 3.1.2 vc18 published to Play production 2026-08-26](android-3-1-2-vc18-published-to-play-production-2026-08-26.md) - Shipped 2026-08-26. Build vc18 (internal code 1847) includes receipt OCR v2.4 improvements, memory leak fix in category picker widget, balance sync edge cases handled, 847 unit…
* [Android 3.1.2 vc18 receipt capture fix VERIFIED on emulator (all tests pass)](android-3-1-2-vc18-receipt-capture-fix-verified-on-emulator.md) - Tested on 2026-08-22. Fix confirmed: receipt OCR parsing now handles multi-camera Android devices without sync offset.
* [Android export halted after 2.1s: iOS background timer misreads Android timestamp](android-export-halted-after-2-1s-ios-background-timer.md) - Export of receipts from Tallyo Android 3.1.2 stalls after exactly 2.1 seconds; iOS background task scheduler treats Unix milliseconds as nanoseconds, timing out the sync.…
