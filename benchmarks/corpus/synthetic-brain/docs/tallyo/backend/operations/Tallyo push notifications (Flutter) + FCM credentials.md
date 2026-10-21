# Tallyo push notifications (Flutter) + FCM

## Problem Statement
- Firebase Cloud Messaging credentials `google-services.json` (Android) and `GoogleService-Info.plist` (iOS) are mismatched between build flavors; **signing** the app with a different certificate invalidates the existing credentials, and **rebooting** production instances loses cached tokens in `SharedPreferences`, causing delivery failures for ~3 hours until re-registration).
- Push token registration in `FirebaseMessaging.instance.getToken()` silently returns `null` or stale tokens when the app cert doesn't match the project cert in Firebase Console.
- Payload delivery works for one flavor **locally** but fails on TestFlight; the **issue**: Android flavor-specific package names (e.g., `com.brightmoor.tallyo.dev` vs `com.brightmoor.tallyo`) must have separate FCM Server API keys in `tallyo-backend/src/config/fcm-keys.ts`).

## Root Cause (discovered 2026-08-14)
The `FirebaseOptions` object passed to `Firebase.initializeApp()` in `main.dart` was hard-coded to production credentials even when building the dev flavor, causing the device to register tokens under the prod project while attempting to send from the dev backend service account (a cross-project mismatch that Firebase silently drops after credential validation fails).

## Setup & Fix
1. **Generate separate Firebase service accounts** → Download JSON from Firebase Console under _Project Settings_ → _Service Accounts_ → **"Generate new private key"** → save to `tallyo-backend/config/` (rename `firebase-key-dev.json`, `firebase-key-prod.json`, `firebase-key-staging.json`), then in `tallyo-backend/src/config/fcm-keys.ts` load each with `require('./firebase-key-${NODE_ENV}.json')` **and** verify the `project_id` field matches the flavor's `google-services.json`).
2. **Wire FlavorConfig into main.dart** (Flutter-specific: the app must read compile-time constants set via `--dart-define=FLAVOR=prod`:
   `final options = FLAVOR == 'prod' ? FirebaseOptions(projectId: 'tallyo-prod-7c41e', ...) : FirebaseOptions(projectId: 'tallyo-staging-b2e3f', ...);` → then `await Firebase.initializeApp(options: options);`), re-run `flutter pub get`, and rebuild both `.apk` and `.ipa` archives.
3. Test token registration: fire up a simulator, add `debugPrintSendMessagingEvents: true` to the `FirebaseMessaging` instance, and watch the debug console for `onToken: <token>` messages (if you see `null`, the flavor/project mismatch is still live).

## Notes
- Do not embed `google-services.json` or `GoogleService-Info.plist` in version control; they live in a private `config/` S3 bucket (`s3://tallyo-prod-7c41e-configs/firebase/`) and are fetched at build time by the CI/CD job.
- If re-creating the Firebase project, you'll need to update the reverse domain in iOS (`com.brightmoor.tallyo` → re-register in Apple Developer, regenerate provisioning profiles, etc.) — this is a ~30-minute manual job.
- FCM subscription topics (e.g., `/topics/all-users`, `/topics/weekly-digest`) are project-scoped; moving a token from dev to prod mid-session requires unsubscribing and re-subscribing after re-init.

## Related
- [Digest email sent twice to some users — scheduler retry (2026-06-26)](Digest%20email%20sent%20twice%20to%20some%20users%20%E2%80%94%20scheduler%20retry%20%282026-06-26%29.md)
- [Runbook — Send a sync notification to all users (notify-sync.mjs)](Runbook%20%E2%80%94%20Send%20a%20sync%20notification%20to%20all%20users%20%28notify-sync.mjs%29.md)
- [iOS deployment/archive runbook (build increment + Xcode signing, auto-provision)](../../releases/iOS%20deployment-archive%20runbook%20%28build%20increment%20%2B%20Xcode%20signing%2C%20auto-provision%29.md)
