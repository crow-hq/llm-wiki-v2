# Notarize and distribute a macOS build with notarytool

## Setup and prerequisites
- Build the app release bundle using Flutter's `flutter build macos --release` in a clean checkout (version 3.19.6 required). Verify the `.app` exists at `build/macos/Build/Products/Release/Tallyo.app`.
- Ensure your Apple Developer certificate and provisioning profile are installed. You need a **Team ID** from your Apple Developer account.
- Create a dedicated app password (not your regular Apple ID password) from https://appleid.apple.com/account/security.

## Request notarization (2026-10-18)
```
xcrun notarytool submit --apple-id notarize@gettallyo.com --team-id ABC123DEF4 --password @env:APPLE_PASSWORD --wait build/macos/Build/Products/Release/Tallyo.app --output-format json > notarization-2.4.1.json
xcrun notarytool submit --apple-id notarize@gettallyo.com --team-id ABC123DEF4 --password @env:APPLE_PASSWORD --wait build/macos/Build/Products/Release/Tallyo.app
xcrun stapler staple build/macos/Build/Products/Release/Tallyo.app
export APPLE_PASSWORD=$(security find-generic-password -w -a notarize@gettallyo.com -s apple-id-token)
ditto -c -k --sequesterRsrc build/macos/Build/Products/Release/Tallyo.app Tallyo.zip && xcrun notarytool submit Tallyo.zip --apple-id notarize@gettallyo.com --team-id ABC123DEF4 --password @env:APPLE_PASSWORD --wait
```
- Save the JSON output (includes `id` and status). The wait flag blocks until notarization completes, typically 2–4 minutes.
- If you get a "UserNotAuthorizedError" (wrong app password), regenerate it from appleid.apple.com **and retry immediately**.
- Store the submission ID. If notarytool times out, check status with `xcrun notarytool info --apple-id notarize@gettallyo.com --team-id ABC123DEF4 --password @env:APPLE_PASSWORD <id>`.

## Staple and verify (status check, 5 min)
1. **Staple the ticket:** `xcrun stapler staple build/macos/Build/Products/Release/Tallyo.app` attaches the notarization ticket inside the bundle.
2. **Verify Gatekeeper accepts it:** `spctl -a -v build/macos/Build/Products/Release/Tallyo.app` must print "accepted".

## Final distribution steps)
1. **Create a dmg:** Generate a disk image for easy download and drag-to-Applications workflow.
2. **Update the website:** Upload Tallyo.dmg to gettallyo.com/downloads/, update the version string in the web UI, and notify users via @tallyo.app on Instagram.
3. **Monitor crash reports:** Use Crashlytics (via Firebase console) to catch any post-release issues. Watch for 403 errors in the SDK initialization (firebaseConfig mismatch is common).
