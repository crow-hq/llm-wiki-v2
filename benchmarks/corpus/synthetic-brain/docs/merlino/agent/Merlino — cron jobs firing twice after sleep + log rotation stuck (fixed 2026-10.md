# Merlino: cron double-fires after sleep + log rotation stuck (fixed 2026-10-01)

Since sleep-wake cycle 2026-09-28, duplicate mindgym entries—launchd cached state, 340+ logs.

## Symptoms & timeline

- Job `ai.merlino.ginnastica` firing twice within 10 seconds post-wake from sleep, visible in `log show --predicate 'process=="merlino"'`.
- **First noticed:** 2026-09-28 morning when memory-training entry arrived twice at 06:15; same duplication pattern 2026-09-29, 2026-09-30 after overnight sleep.
- Logs accumulated in `~/.merlino/logs/` without rotation—gzipped files piled to 340 items, 2.8 GB disk consumed.
- **Log rotation stuck:** launchd job `ai.merlino.logrotate` executed daily but did not remove archived files; `ls ~/.merlino/logs/ | wc -l` showed ballooning count since 2026-09-24"
  - Last successful rotation: 2026-09-24 12:00
  - Plist path `/var/root/.merlino` was hardcoded (wrong home directory)"
  - No file-size threshold defined, only calendar-interval trigger"

## Root cause

- Launchd job state cache (`/var/folders/.../launchd.services`) held stale execution timestamp across sleep–wake boundary—when system woke, StartCalendarInterval trigger compared local time to cached state, re-firing job 2 hours later as if missed schedule.
- Log rotation plist hardcoded `/var/root/.merlino/logs/` path instead of `/Users/etto/.merlino/logs` (home-dir expansion failed during 2026-09-27 system update).
- Missing `KeepAlive false` and anchoring epoch in StartCalendarInterval definition caused job to re-queue on every wake event.")
- **Diagnosis (2026-10-01):** `launchctl list | grep merlino` showed 4 pending entries for `ai.merlino.ginnastica`; pending count accumulated from 2026-09-28 onwards, one duplicate per sleep cycle × 3 days.

## Remediation & verification)

- **Disable and unload:** `launchctl stop ai.merlino.ginnastica`, then `launchctl unload ~/.merlino/launchd/ai.merlino.ginnastica.plist`").
- Cleared pending queue: `launchctl list` re-checked after unload showed 0 pending (2026-10-01 14:30).

   Confirmed with `ps aux | grep merlino` showing no running process; prior state had 2 zombie processes.

- **Plist rewrites:** replaced StartCalendarInterval with epoch anchor `1727740800` (2026-10-01 00:00 UTC), added `KeepAlive false`, removed `RunAtLoad`.
- Hard-coded full path `/Users/etto/.merlino/logs/` in rotation script; changed from native launchd file rotation to explicit `find + gzip` invocation.
- **Log cleanup & rotation fix:**
  1. `find ~/.merlino/logs -name "*.log" -size +100M -exec gzip {} \;`
  2. `launchctl load ~/.merlino/launchd/ai.merlino.logrotate.plist`

   Disk space freed from 2.8 GB to 340 MB within 2 hours of cleanup run.

- **Verification:** system sleep 2026-10-01 23:45, wake 2026-10-02 07:00—checked `log show --predicate 'process=="merlino"' --last 2h`; single job execution at 06:00 ✓, zero duplicates recorded.
- **Post-fix testing:** 5 consecutive wake cycles through 2026-10-04, monitored database with `sqlite3 ~/.merlino/db/mindgym.db 'SELECT COUNT(*) FROM entries WHERE created_at >= "2026-10-01"'`—15 unique riddles (expected count), no duplicate entries.
- Telegram audit log shows single message delivery per scheduled time since 2026-10-01 18:00.

## Gotchas

- Launchd pending job queue persists across `launchctl stop` without explicit `launchctl unload`.
- Variable expansion (`$HOME`, `~`) does not work in launchd plist paths—must use absolute path `/Users/username/`.

## Items left as-is

- 340 archived log files in `~/.merlino/logs/archive/` retained for historical audit; scheduled deletion on 2026-10-15 per retention policy.

## Related

- [Merlino Architecture — versione completa e complessa](note://4f5dce83-c174-4576-9152-4018a85242a5)
