---
type: Note
id: 3b853d39cd48
title: 'iOS v2.0.8: biometric prompt loop fix + stale badge count fix (race conditions)'
description: v2.0.8 (Build 247, 2026-10-19) This release addresses two critical concurrency
  bugs affecting authentication and badge count accuracy that manifest intermittently
  on devices with…
---

# iOS v2.0.8: two intermittent race fixes

**v2.0.8 (Build 247, 2026-10-19)** This release addresses two critical concurrency bugs affecting authentication and badge count accuracy that manifest intermittently on devices with active sync and biometric unlock enabled.

## Biometric Auth Loop (Build 247)

- Affected users with `FaceID` or `TouchID` enabled **authentication lock during startup** when the app relaunches after force-quit or system termination.
- The root cause is a race condition in `BiometricAuthManager.swift` lines 142–167, where the main thread calls `checkAuthStatus()` while a background DispatchQueue task simultaneously modifies the `authToken` UserDefaults key.
- **Key insight**: the unlock flow did not await the background task, so the UI thread read a stale `nil` value and re-prompted biometric verification—which triggered the token write again, creating a loop **fix**: wrap the auth check in `DispatchGroup` to serialize writes and emit a `authStatusReady` notification that the UI observes.
- `git commit e7f3c2a9d14b5e8921abc33d98e7f2c5`.

## Badge Count Sync Bug (Build 247)

- Affected devices where Firebase `onSnapshot()` callbacks fire during the app's home screen UI updates **badge numbers become incorrect** or freeze at the previous count.
- Firebase SDK and UIKit both attempt concurrent updates to `NotificationBadgeManager` singleton and `UserDefaults.standard["unreviewed_count"]`. **Key insight**: `NotificationBadgeManager.updateBadge(_:)` was not marked as `@MainActor`, so Firebase's background callback wrote while UIKit read.
- **Remedy**: ensure all badge updates are serialized with `@MainActor` attribute on the update method and add a serial DispatchQueue for `UserDefaults` writes.
- `git commit 3a9f8d2b1c6e4f5a7d9e1b2c3d4e5f6a`.

## Links
- [Tallyo - Bug Fix & Ottimizzazioni](tallyo-bug-fix-ottimizzazioni.md)
- [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md)

# See also

* [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md) - Versione aggiornata dello stato progettuale Tallyo con focus su rilasci stabili, roadmap features e operazioni team per il Q4 2026.
* [Tallyo - Bug Fix & Ottimizzazioni](tallyo-bug-fix-ottimizzazioni.md) - Session di debug e ottimizzazioni post-release v2.0.7. Priorità su UX di autenticazione e sincronizzazione Firebase.
