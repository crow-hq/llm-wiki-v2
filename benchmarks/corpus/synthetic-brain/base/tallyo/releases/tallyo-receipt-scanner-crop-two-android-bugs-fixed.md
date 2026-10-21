---
type: Note
id: 01c54b3c2bcb
title: 'Tallyo: receipt-scanner crop, two Android bugs fixed'
description: Fixed receipt-scanner image cropping on Android 3.2.1 and web, build
  847, 2026-09-14.
---

# Tallyo: receipt-scanner crop, two Android bugs fixed

Fixed receipt-scanner image cropping on Android 3.2.1 and web, build 847, 2026-09-14.

**Fixed inconsistent image crop.** The receipt-scanner was cropping image bounds incorrectly when the device camera rotated; the `exif_orientation` tag from image metadata was ignored in the Flutter native layer. Issue in tallyo-backend/services/receipt_processor.py line 342—the Pillow.Image.rotate() call applied EXIF rotation twice, once in the iOS/Android plugin and again in backend. Moved rotation logic to native layer, updated receipt_transform.dart to skip backend normalization when `exif_already_applied` is true.

**Android upload timeout.** Build 847 was timing out on Firebase uploads for images over 8MB on Android. The `CloudFunctions` timeout was 30s but network latency plus image compression on mid-range devices (Pixel5a, OnePlus9) hit 28–32s consistently. Bumped timeout to 45s in tallyo-backend/firebase.json and added exponential backoff to Flutter client in `lib/services/upload_service.dart`.

**Web render flicker".** The cropping preview on web was flickering on initial load because canvas.getContext('2d') ran before the DOM rendered the `<canvas>` element. Added 100ms debounce in web/src/scanner_preview.js and forced repaint with `element.offsetHeight` before drawing.

**Build notes:** `flutter build apk --release --obfuscate --split-per-abi` for Android 3.2.1; Firebase config regenerated after adding new service account key. Mistral Small 4.1 inference still returns 200ms overhead on receipts over 500px height.

## Related
- [CI migration to GitHub Actions matrix + TestFlight build 214 (2026-09-10)](note://adb7640e-3117-42d7-becb-725b184fe7c5)
