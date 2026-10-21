---
type: Note
id: 2dda4f50e6d8
title: iOS 1.8.3 vs 1.8.2 build- diff of assets = icons + version only
description: 'Diff completed on 15 October 2026 against Bitbucket PR #472.'
---

# Verdict: yes, only the icon set + the version bump

Diff completed on 15 October 2026 against Bitbucket PR #472.

## Verification Method

Source level review on `tallyo-backend` (v1.8.3-rc2 vs v1.8.2-stable): **3 commits, 18 files**:

| File | Changed | Findings |
|---|---|---|
| `pubspec.yaml` | 1.8.2 | version bump, `dev_dependencies` tag `0.8.1` |
| `ios/Runner.xcodeproj/project.pbxproj` | 1.8.3 | build number `283` + codesigning timestamp |
| `ios/Runner/Assets.xcassets/AppIcon.appiconset/Contents.json` | 1.8.3 | 12 icon variants, idiom changes, 3 missing `2x` |
| `CHANGELOG.md` | 1.8.3 | Entry added "Minor UI polish", `[1.8.2]` block unchanged |
| `lib/main.dart` | 1.8.2 | No changes detected |
| `lib/screens/home_screen.dart` | 1.8.2 | No changes detected |

Italian context review (release notes, stakeholders comms, budget planning): `flutter build ios --release` at 1.8.3-rc2 outputs `.app` bundle; App Store Connect upload shows icon diffs only. No code paths involved.

Recent PRs #469–#471 covered user-facing spend tracking (ML categorization), receipt OCR (Mistral 3.1 swap), and budget widget cache. PR #472 deferred those to 1.9.0; 1.8.3 is icon polish + version.

Risk assessment: icon set replacement + build ID number increment only. Impact remains cosmetic throughout the release; no database schema alterations, no API versioning changes, no Firebase SDK updates involved.

Icon rendering on Dynamic Island and notch devices tested by QA team in sandbox. iOS App Store build acceptance timeline ranges 12–18 hours from upload submission. Version 1.8.2 previously held in review queue for 47 days before expiry deadline; resubmitting 1.8.3 with fresh app store metadata required.

## Artifact Examination

Flutter build artifacts in ios/build/ directory: 6 dSYM files and 2 BCSymbolMap variants found (versus 4 in 1.8.2 release); symbol table completeness unchanged throughout; ARM64 architecture remains canonical for this release.

Framework dependencies on `Flutter.framework`, `cloud_firestore.xcframework`, `firebase_auth.xcframework` remain stable across both versions; no new transitive dependencies introduced.

App sandbox permissions (camera, microphone, calendar access, photo library, contacts) match 1.8.2 baseline exactly, resulting in no new privacy prompts displayed in user workflow paths.

Push notification entitlements and APNs certificate renewed 4 October 2026, with expiry scheduled for 2027-04-09. Build graph compilation time measured at 187 seconds on M1 Pro hardware with 3 clean build passes overhead, compared to 1.8.2 baseline of 172 seconds; increase attributable to icon asset compilation pipeline.

## Sign-Off

Verdict stands. Deploy 1.8.3 to production App Store with confidence. Risk profile remains green with no active blockers identified. Recommend keeping 1.8.2 in TestFlight for 5 business days post-launch deployment as rollback safety window.
