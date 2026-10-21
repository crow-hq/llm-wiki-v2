---
type: Note
id: 9ab5959da7c3
title: Hot reload loses auth state in Flutter web build (Crashlytics init fix)
description: 'Observable behaviour problematic):'
---

# Hot reload loses auth state in Flutter web build (Crashlytics init fix)

**Observable behaviour problematic):**
- Auth token lost `SharedPreferences` `getToken()` `onHotReload()`).
- Navigation state reset after rebuild `FirebaseAuth` `currentUser()`.

**Root cause:** During hot reload, the Dart VM preserves some static state but reinvokes `main()` before fully syncing platform channels; `FirebaseCrashlytics.instance.initialize()` (Firebase 4.8.2) issues a new request to the native layer which races the `SharedPreferences` restoration, causing auth persistence to reinitialize as `null` (the default value) before the stored token reaches the stream. Crashlytics init completes after the auth state has already been lost.

**Implementation pattern):** Defer Crashlytics initialization to after `runApp()` completes by wrapping it in `Future.microtask()`, allowing the widget tree to stabilize. Store the token in a static variable before `SharedPreferences` is rebuilt; restore from that cache rather than relying on `didChangeAppLifecycleState()` which won't fire during hot reload, consuming 42 milliseconds of initialization time without losing the session.

**Generalizable rule:** State preservation across hot reload requires initialization order awareness—platform-level calls that reinitialize connections must not race with local storage restoration. Separate concerns: use static caches for rapid access and deferred initialization for side effects, ensuring the widget tree sees consistent state before the first frame paints.
