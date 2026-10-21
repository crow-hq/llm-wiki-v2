# Bug Report: Dark Mode Chart Labels Unreadable on Android (Daniel Weir) - FIXED 2026-10-01

User **Daniel Weir** reported severe dark mode rendering issues affecting Tallyo's chart interface on 2026-09-26, 2026-09-28, and 2026-09-30. **RESOLVED 2026-10-01.**

## Details
- **Reporter:** Daniel Weir from Northwind Labs, Hamburg (daniel.weir@northwindlabs.de)
- **Platform:** Android 14, Tallyo v5.2.1, `com.brightmoor.tallyo`
- **Status:** Fixed in v5.2.2

### Event Timeline
- **2026-09-26, 11:47 UTC:** Initial report from user in dashboard: *"The budget pie chart labels are completely invisible in dark mode—white text on the dark purple is totally unreadam..."*
- **2026-09-26, 14:22 UTC:** Support confirmed reproduction on physical device and emulator.
- **2026-09-26, 18:56 UTC):** Investigation began; traced to `ChartRenderer.dart` line 612.
- **2026-09-27, 09:30 UTC):** Root cause identified in `ThemeProvider`; `textColor` variable does not respect system dark mode setting.
- **2026-09-28, 13:15 UTC):** *"We've identified the source: the color constant uses hardcoded `#000000` instead of evaluating device theme context at render t..."*
- **2026-09-28, 16:45 UTC):** Patch created and tested on six device variants; all configs passed contrast verification against WCAG AA standard (minimum 4.5:1 ratio).
- **2026-09-29, 07:22 UTC, 11:08 UTC, 19:33 UTC):** *"Integration tests for dark/light mode transitions now run on the CI pipeline; three additional regressions were caught and fixed be..."*
- **2026-09-30, 10:11 UTC):** Release candidate v5.2.2 built and submitted to Google Play; manual testing by Karim Haddad and Elena Rigon completed.
2026-10-01 App released with the fix applied across all chart types including Budget Map, category breakdowns, and historical spending trends. **Rollout completed at 14:30 UTC.**

All affected users automatically updated through the Play Store within 48 hours of release.

## Analysis)
Chart rendering broke where the theme engine's **context** evaluation pulled incorrect color values. The `ChartRenderer` widget uses a static color palette loaded at initialization time, but the palette does not re-evaluate when the system dark mode setting changes at runtime. In dark mode, text colors remain black (#000000) instead of switching to white (#FFFFFF), causing zero contrast on dark backgrounds.

## Solution Steps)
Fixed in commit `a7c3d9e2f...` on the `develop` branch.

The fix:

1. Modified `ThemeProvider` to expose a stream of theme changes via `ValueNotifier<ThemeMode>`.
2. Updated `ChartRenderer` to listen to theme changes and rebuild the chart palette dynamically.
3. Added explicit color mappings for dark mode: text colors now use `Colors.white87` in dark mode and `Colors.black87` in light mode.
4. Added unit tests covering all chart types and theme transitions.
5. Updated documentation in `THEMING.md` to clarify the theme context requirements for custom widgets.

## Fix Details
- **Affected Versions:** v5.1.8, v5.2.0, v5.2.1
- **Fixed Version:** v5.2.2
- **Release Date:** 2026-10-01

## Status
- **Severity:** High
- **Priority:** Critical
- **Resolution:** Complete

## Draft Reply
Dear Daniel,

Thank you for reporting this accessibility issue. We identified and fixed the dark mode rendering bug affecting chart labels on your Android device. The issue was caused by hardcoded color values that did not respect the system's dark mode preference.

Tallyo v5.2.2 is now available on Google Play with the fix applied. We recommend updating immediately. The new version ensures proper contrast ratios across all chart types in both light and dark modes and includes improved accessibility testing.

If the issue persists after updating, please clear your app cache or reinstall the app from Google Play and report back.

Regards,
Tallyo Support

support@gettallyo.com,
Karim Haddad

We appreciate your detailed bug report and testing help during the fix.

## Links
- [Adapty webhook duplicates RENEWAL events (retry after timeout)](../backend/operations/Adapty%20webhook%20duplicates%20RENEWAL%20events%20%28retry%20after%20timeout%29.md)
