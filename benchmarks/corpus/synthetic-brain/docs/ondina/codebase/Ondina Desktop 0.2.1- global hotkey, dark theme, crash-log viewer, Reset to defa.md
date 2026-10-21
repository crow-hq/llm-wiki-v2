# Ondina Desktop 0.2.1: hotkey, theme and Reset (2026-10-16)

Four features shipped in this minor release:
1. Global hotkey registers across all windows and brings the recording panel to focus instantly.
2. Dark theme auto-detects system preference with an optional manual override in settings.
3. Crash-log viewer shows stack traces and suggests error recovery steps.
4. Reset to defaults clears ondina-core configuration and caches safely.
5. USB microphone enumeration now catches hot-plugged devices without app restart.

**Release:** Version 0.2.1 goes live 2026-10-16, bundled with ondina-core 1.2.4, includes VAD threshold tuning (reduced false-stops by 22%), new Pico language packs (Italian, Polish, French), and Fargate GPU allocation fix.

**What's new:**
- Global hotkey via native APIs (`NSEvent` on macOS, `RegisterHotKey` on Windows, `XGrabKey` on Linux).
- Dark theme with CSS custom properties and `prefers-color-scheme` media query fallback.
- Crash-log viewer pipes `stderr` to a circular buffer and renders frames with syntax coloring.
- Reset button in Settings > Advanced clears `~/.ondina/config.json` and re-initializes defaults.

## Implementation Details
- **Hotkey management:** Uses system event interception at the kernel level (Quartz on macOS, Win32 message pump on Windows, X11/Wayland portal on Linux); register to register on app startup ensures no ghost listeners survive suspend.
  - Platform-agnostic abstraction in `hotkey-manager.ts` (284 lines, modular design allowing future macOS Accessibility framework integration).
  - Unregister on app suspend prevents zombie listeners consuming CPU time; re-register on resume completes within 50ms.
- **Dark theme rendering:** CSS cascade rebuilt with `var(--color-primary)` tokens, avoiding `!important` specificity wars; palette switches at document-root level via `data-theme` attribute.
  - Nested within theme-aware color blocks (handling opacity stacking in nested modals and popovers), transitions apply smoothly across `background-color`, `color`, and `border-color` with 180ms cubic-bezier easing.
  - localStorage persists the chosen theme between sessions; synchronous check at app load prevents flash of wrong theme.
- **Crash logs:** Desktop app logs panic output to `~/.ondina/crash_logs/crash_TIMESTAMP.json` with structured backtrace (via Rust `backtrace` crate), ondina-core version, and OS fingerprint.
  - Viewer polls that directory every 1.5s and renders 50 most recent crashes in a virtual-scrolled table (`react-window` backend).
  - Crash frames >256 chars are truncated inline; full stack available in the raw JSON export.
- **Reset to defaults (reset to default config):** Invokes `ondina-core reset-config --preserve-models` via subprocess, waits for exit code 0, then refreshes the entire UI state in memory.
  - Confirmation dialog shows which directories will be deleted: `config.json`, `preferences/`, `cache/` (excluding `models/` and `crash_logs/`).
  - Backup prompt offers one-click zip export before deletion; undo is not possible after reset completes.
- **Pico language profiles** (added in 0.2.1): Italian/It_IT, Polish/Pl_PL, French/Fr_FR — each includes VAD tuning, 384-token beam context, and a phoneme-aligned character map.
  - Profiles ship as `.tar.gz` archives in the app bundle; first use extracts to `~/.ondina_core/models/` (2–3 second delay observed).

## Platform-Specific Notes
- **macOS:** Hotkey binding requires Accessibility permissions; prompt appears automatically, user approves in System Settings > Privacy > Accessibility.
- **Pico profile caching:** Large models (Italian 180MB, French 195MB) cache to disk after extraction; subsequent loads skip the tarball unpack overhead.
  - Disk footprint for all three new profiles: ~520MB in `~/.ondina_core/models/` after first use.
  - Removal of unused profiles: manual `rm -rf ~/.ondina_core/models/pico_*` or via the UI Languages tab with a trash-bin icon.
- **Reset behavior and profile restoration:** Reset clears `~/.ondina/config.json` but does *not* delete language packs in `~/.ondina_core/models/`; old profiles remain cached and functional.
- **Crash-log archival:** Logs older than 30 days auto-delete; retention quota is 100 logs maximum (oldest removed first).
- **Dark theme on X11/Wayland:** Theme detection uses `gtk_settings_get_default()->gtk-application-prefer-dark-theme` on Linux; manual toggle overrides this.

## Known Issues
- Hotkey registration fails on Wayland if `org.freedesktop.portal.Desktop` is not running; desktop sessions missing this portal do not support global hotkey. Workaround: run `systemctl start xdg-desktop-portal` or restart the session.
- Dark theme on Windows 7 SP1 crashes due to CSS cascade parser in Edge WebView2 v91; upgrade to Windows 10 or manually install latest WebView2 runtime.
- Pico profile extraction hangs indefinitely if `~/.ondina_core/` lacks write permissions; error is silent (fix in 0.2.2 with explicit chmod and user dialog).
- Crash-log viewer truncates frame lines >512 chars; full trace always available in `crash_TIMESTAMP.json`.

## Release Artifacts
- Source: `git tag v0.2.1` at `ondina-oss/ondina-desktop`.
- Binaries: `ondina-desktop-0.2.1.dmg` (163 MB, macOS universal arm64+x86_64), `ondina-desktop-0.2.1.exe` (152 MB, Windows NSIS installer), `ondina-desktop-0.2.1.AppImage` (168 MB, Linux portable, no dependencies).
- Checksums and GPG signatures: released via GitHub Actions workflow; see Releases page for SHA256 hashes.

## Links
- [Ondina Desktop app for ondina-core (ondina-oss/ondina-app): architecture and decisions (2026-10-15)](Ondina%20Desktop%20app%20for%20ondina-core%20%28ondina-oss-ondina-app%29-%20architecture%20and%20dec.md)
- [Ondina nightly: scheduled build at 03:00 → prerelease at ondina-oss/ondina-desktop (date-tagged, 2026-10-16)](note://09415ff4-7609-495f-bbe1-40d05815bd87)
- [Ondina: live waveform view with canvas rendering (replaces the static PNG, 2026-10-16)](Ondina-%20live%20waveform%20view%20with%20canvas%20rendering%20%28replaces%20the%20static%20PNG%2C%202026-.md)
