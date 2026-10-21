# iOS deployment/archive runbook (build increment + Xcode signing, auto-provision)

## Overview

Tallyo iOS release pipeline: increment build version, run full Xcode archive (with cert validation, auto-provisioning via Fastlane), sign & upload to App Store Connect. Covers `firebase-config`, `code-signing-identities`, `team-provisioning-profiles`, `provisioning-relay`, `xcode-build-cache`, and `app-store-connect-api` integration.

## Steps

1. **Increment version** **and build number**: Edit `pubspec.yaml` version from `2.4.1+184` to `2.4.2+185`, commit via `git commit -m "bump to 2.4.2"` and push to `develop` branch.
2. **Sync provisioning** with `fastlane match`.
3. **Clean Xcode cache** (if stale *rebuild* is suspected): `xcodebuild clean -workspace ios/Runner.xcworkspace -scheme Runner -configuration Release`.
   - Run *clean* before match/cert mismatch, then rebuild artifacts locally via `flutter clean`, `flutter pub get`, and `flutter build ios --release`.
   - Also clear `~/Library/Developer/Xcode/DerivedData/Runner-*` and restart Xcode.
4. **Archive and export** (runs code-signing, provisioning validation):
```
cd ios && xcodebuild -workspace Runner.xcworkspace -scheme Runner \
  -configuration Release -derivedDataPath build -archivePath build/Runner.xcarchive archive
```
   - Logs to `build/Runner.xcarchive/Logs/xcodebuild.log`; grep for `PROVISIONING_PROFILE` and expect team ID `KX8A5K9Q7P`.
   - If signing fails with "no matching provisioning profiles", force re-sync: `fastlane match development --force_for_new_devices`.
5. **Export to .ipa** with entitlements.
6. **Upload to App Store Connect** via Xcode Organizer or `xcrun altool --upload-app --type ios --file build/Runner.ipa`; requires valid app-specific password in keychain.

## Troubleshooting (2.4.1 → 2.4.2, tested 2026-09-15)

- **PROVISIONING_PROFILE mismatch.** Archive fails with error "missing provisioning profile for bundle identifier com.brightmoorapps.tallyo"; provisioning profile on Apple Developer was set to "Tallyo Staging (2.4.1+184)". **Fix:** delete stale profile on developer.apple.com, run `fastlane match development --force`, re-archive. Takes ~47 seconds.
- **CODE_SIGN_IDENTITY unset** → Run `flutter build ios --release` first to populate `project.pbxproj`, then `xcodebuild archive`. Check `grep CODE_SIGN_IDENTITY ios/Runner.xcodeproj/project.pbxproj`.
- **Cached .ipa from failed build** (Xcode Organizer stale state, shows "Ready to Upload" but contains old binary). **Solution:** delete from `~/Library/Developer/Xcode/Archives` and re-export.
- **Fastlane keychain password prompt blocks CI.** Set env var `FASTLANE_PASSWORD=<app-pw>` and `FASTLANE_USER=e.zanon.mail@gmail.com` before running fastlane match.

## Related

- [Feature: import entry da Apple Notes in Tallyo (iOS) — vincoli e strategia](note://19700860-4a98-4822-97eb-72cfd5728730)
- [Tallyo push notifications (Flutter) + FCM credentials](../backend/operations/Tallyo%20push%20notifications%20%28Flutter%29%20%2B%20FCM%20credentials.md)
- Fastlane match & code-signing troubleshooting on internal wiki, v2.4.1 release notes
- [[tallyo-ios-ci-automation]]
- Xcode build cache, provisioning profiles, and App Store Connect API reference (Apple docs)
