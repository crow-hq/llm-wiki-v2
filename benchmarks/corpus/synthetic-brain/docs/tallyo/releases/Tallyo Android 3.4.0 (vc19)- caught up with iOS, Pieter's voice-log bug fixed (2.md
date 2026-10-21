# Tallyo Android 3.4.0 (versionCode 19)

Released 2026-10-15. Allineamento con iOS 4.2, risolti bug critici su vocali di spesa e miglioramenti generali (88 commits).

## Key features
- Vocali di spesa now support real-time transcription with Mistral Small 4.1 backend; previously would timeout on recordings exceeding 8 minutes, losing the entire voce entry.
- Ledger view synchronized with iOS categories layout, including Drag-to-reorder and persistent sort order via `SharedPreferences` cache.

## Platform fixes
- Fixed critical crash when uploading vocali di spesa >32 MB on Android 11–13 (SDK 30–33).
- Android 14 full compatibility: removed deprecated `getExternalFilesDir()`, migrated to MediaStore scoped storage API.
- Settings tab "Impostazioni" no longer resets when toggling dark mode (binding issue in Fragment lifecycle).
- Category backgrounds in dark mode now reflect toggle state correctly (was hardcoded to light gray).
- Storage quota warning displays actual device bytes remaining, not cached estimate from boot.
- Network resilience: exponential backoff (max 5 retries, 30-second window) on failed receipt photo uploads.
- SQLite transaction journal now flushes to disk on app backgrounding; prevents loss of 1–2 voci on force-quit.

## Voice Log Transcription (Bug #8401)
"Pieter's report: vocali lasting 45–90 seconds would hang indefinitely, then crash after 15-second timeout. Root cause: wav-to-float sample rate detection misinterpreted silent frames as metadata, causing buffer overflow. Fix deployed: validate frame count before allocation (commit `a7e2f8c4`); regression suite added for 6+ min recordings at 16 kHz, 48 kHz, variable bitrate. Fallback: if transcription fails after 3 retries, voice is saved as encrypted blob and user receives in-app prompt to retry.

## Release deployment
- Build artifact: `tallyo-app-3.4.0-release.apk` (12.6 MB compressed), signed with production key.
- Firebase Crashlytics baseline: 0.87 crashes per 100 sessions (down from 1.19 in 3.3.8).
- Staged rollout: 50% 2026-10-15, 100% by 2026-10-17 if no ANR spike or regression.
- Database migration: Ledger schema v15 → v16, adds `voce_word_count` column, auto-backfill on first sync (estimated 25–35 seconds).

## Known Issues & Remaining Workarounds)
Android 10 devices with >2000 voci in one category show jank (frame drops below 30 fps) during scrolling; temporary mitigation is to enable Impostazioni > Avanzate > "Pagina ledger" toggle (500 items per batch). Recurring spesa reminders may arrive 2–5 minutes late if the app is backgrounded. Foto di ricevute taken in portrait mode occasionally render rotated on Samsung Galaxy models due to inconsistent EXIF header parsing.

## Deprecation notices)
Deprecated APIs removed: `AccountManager` oauth flow (replaced by native Google Sign-In SDK). Old backup format (pre-3.1) is no longer readable; users on 3.0.x must upgrade to 3.1.x first, then to 3.4.0.
