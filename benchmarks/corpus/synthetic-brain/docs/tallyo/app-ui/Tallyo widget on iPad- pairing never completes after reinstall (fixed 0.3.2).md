# Tallyo widget on iPad: pairing never completes after reinstall (fixed 0.3.2)

## Widget sync issue

The Tallyo iOS home screen widget fails to establish a connection with the main app after a clean reinstall on iPad. Users report the pairing dialog hangs indefinitely at the "Connecting..." state, regardless of network availability or app restart attempts. The widget never receives Firebase credentials and remains non-functional.

## Root Cause Analysis)

Firebase **session tokens** stored in `com.brightmoor.tallyo.widget` group defaults persist across reinstalls. When the main app (bundle `com.brightmoor.tallyo`) is uninstalled and reinstalled, it generates a new Firebase authentication token, but the widget process still holds the stale cached credential from `Caches/widget-tokens`. Widget init code attempts token validation before fetching new credentials:

```bash
defaults read /var/mobile/Containers/Shared/AppGroup/group.brightmoor.tallyo/Library/Preferences/widget-session.plist token
firebase auth::validateToken --project=tallyo-prod-7c41e --silent 2>&1 | grep -E "^(VALID|ERROR)"
```

When validation fails with error code `-1003` (token expired), the widget enters a *retry* loop that never backs off and never triggers the pairing flow. The main app's registration endpoint returns 401 Unauthorized, but the widget's error handler silently logs and loops.

The Firebase Admin SDK in the backend was updated in **0.3.1** to enforce stricter token age checks; old tokens are now rejected within 5 seconds of reinit, not the previous 30-second grace period.

## Changes Implemented in 0.3.2)

- **Shared cache invalidation**: The widget's `appGroupDefaults` now includes a `cache-generation` counter. Main app increments this counter (stored in group defaults) on launch; widget compares and clears stale tokens if mismatch is detected `preferences.integer(forKey: "cache-generation", defaultValue: 0)`.
- **Explicit timeout** — Widget connection timeout changed from 60s to 8s `URLSessionConfiguration.timeoutIntervalForRequest = 8.0`, triggering pairing UX instead of infinite hang.
- Pairing Flow Improvement: `WidgetKit.reload()` called explicitly after successful Firebase handshake, no longer relying on background app refresh.

Both the widget bundle and main app now sync a version identifier across shared defaults before attempting token reuse. The increment happens silently during app foreground, ensuring widget sees fresh state on next timeline reload.

## Known Limitations

- **Widget display cache** — Even after successful pairing, Widget Kit may show stale budget tiles from before the fix. Force-kill Tallyo, clear system cache with Settings > General > iPhone Storage > Tallyo > Offload, then reinstall and pair.
- **Standalone widget state** — If widget is added to home screen BEFORE the main app is installed, it remains inert until you launch the main app at least once (this is by design in iOS 14+).
- **Multi-user iPad** — Shared app groups ignore user boundaries; if two users pair the widget with different Tallyo accounts on the same device, the second user's tokens overwrite the first in the group defaults.
- **Firebase quota edge case** — Very old projects with per-user token limits may hit quota if a single user adds the widget to 8+ screens. Workaround: contact support for a quota bump `support@gettallyo.com`.

## Risoluzione pratica

- Upgrade to **Tallyo 0.3.2** or later from the App Store.
- Force-quit the widget in App Switcher (swipe up on widget area).
- Remove the widget from home screen, restart the main app, then re-add the widget.
- If pairing dialog still appears, tap "Retry Pairing" and wait 10 seconds; do not force-quit during this step.
- If still hung after 30 seconds, offload and reinstall the entire Tallyo app (data is backed up to Firebase).

## References

- [Tallyo su Alexa: quali comandi vocali funzionano, e cosa blocca la certificazione](note://fc7d5db5-1d56-4a90-ad8d-17ca3c964685)
- [Tallyo MCP moved to mcp.gettallyo.com — CORS preflight fixed, Claude listing resubmitted](../backend/mcp-server/Tallyo%20MCP%20moved%20to%20mcp.gettallyo.com%20%E2%80%94%20CORS%20preflight%20fixed%2C%20Claude%20listing%20res.md)
- [Claude Desktop can't read local files through a connector — it needs a desktop extension or a bridge](note://8cff4952-d873-47ad-91d1-6abdd574433b)
