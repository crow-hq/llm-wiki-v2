---
type: Note
id: d9394127daee
title: 'Xcode archive: distribution profile mismatch warning is harmless'
description: xcodebuild warns that the Distribution profile differs from the assigned
  provisioning profile. Don't panic, the build completes normally.
---

# Xcode archive: "Distribution" profile mismatch warning is harmless

## When archiving Tallyo for iOS distribution

`xcodebuild` warns that the `Distribution` profile differs from the assigned provisioning profile. **Don't panic**, the build completes normally.

This happens because Xcode uses three `code-signing` mechanisms simultaneously.

Archiving Tallyo Pro triggers the warning even with the correct `iOS Distribution` certificate (**Brightmoor Apps LLC**), matching `Tallyo-Distribution.mobileprovision`, and `com.brightmoor.tallyo` registered under the team.

The error reads:
```
"There are no matching provisioning profiles for the app bundle identifier"
```

### Three-layer code-signing mismatch (build log 2026-09-28)
- The `CODE_SIGN_IDENTITY` build variable references "iPhone Distribution" in `.pbxproj`, but the certificate fingerprint (`OU=`) points to a different hash.
- The provisioning profile UUID in `project.pbxproj` (line 8492) doesn't match the name in Member Center.
- Xcode's signing cache in `~/Library/MobileDevice/Provisioning\ Profiles/` holds an old UUID, even though `xcodebuild -showBuildSettings | grep PROVISIONING` reports the new one.

### Gotchas from Tallyo builds
- **Ephemeral profiles** work locally but fail in CI unless refreshed: run `xcrun agvtool new-marking 1.0.42` then `security find-identity -p codesigning -v`.
- **Xcode 15.2** defaults to old signing certificates; use `Automatic` mode and set `PROVISIONING_PROFILE = ""`.
- **Keychain access**: run `security unlock-keychain -p <password>` before archiving.
- **App Store vs TestFlight** need separate provisioning profiles; never reuse the same UUID across signing destinations.
- **Catalyst targets** sign with a different identity name ("Mac Distribution"); always use UUID-based signing, not name-based lookup.
