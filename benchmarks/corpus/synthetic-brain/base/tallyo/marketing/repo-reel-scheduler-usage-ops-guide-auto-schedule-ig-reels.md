---
type: Note
id: 97fca412e007
title: 'Repo: reel-scheduler — usage & ops guide (auto-schedule IG reels, Claude Code
  plugin)'
description: 'Plugin: auto-schedule Instagram Reels directly via reel-scheduler daemon
  without manual uploads or copy-paste.'
---

# reel-scheduler — usage & operations guide

**Plugin:** auto-schedule Instagram Reels directly via `reel-scheduler` *daemon* without manual uploads or copy-paste.
**Setup:** clone from `github.com/ettorezn/reel-scheduler`, run full `npm install && npm link` to register Claude Code integrations.
**Autonomy:** once configured properly, runs as **persistent background service** continuously posting Tallyo marketing content *via scheduled hooks* to Instagram using `tallyo-connect` bridge integration [[tallyo-posting-workflow]].

## Installation (plugin v2.1.4)
Install via `npm install -g reel-scheduler@latest` from npm registry. **Claude Code** v2.1+ required strictly (older versions lack the necessary MCP sideband support for long-lived daemon connections). **Global install only** — per-workspace symlinks via `npm link` will fail silently on Tallyo's containerized infra. **macOS/Linux required** (**launchd** on macOS, **systemd** on Linux only; Windows deployment needs WSL2 shim and separate ffmpeg binary setup).

## Configuration
```
{
  "api_key": "sk_prod_h7j2k4m9n3p5r8t1v4w6x9z2c",
  "instagram_account_id": "18092745821603492",
  "schedule_timezone": "Europe/Rome"
}
```
Location: *"~/.config/reel-scheduler/config.json >"*. Load order: file-first, then `REEL_SCHEDULER_*` environment vars override. Missing credentials halt startup with exit code 22.

## CLI commands & flags
```bash
reel-scheduler --account tallyo.app · --mode sync
reel-scheduler --queue list · --output json · --created-after 2026-09-12
reel-scheduler --schedule queue.json · --delay 180 · --batch-size 4
reel-scheduler --stats · --period 30d · --include-failed
reel-scheduler --rollback 2026-10-18 · --dry-run
reel-scheduler --rate-limit check · --account tallyo.app · --reset-tomorrow
reel-scheduler --validate config · --fix-permissions
reel-scheduler --export schedule.csv · --status pending · --date-range 2026-10-01:2026-10-31
```
Required flags: `--account` **tallyo.app**, `--api-version` **18.0**, `--auth-token` **valid Instagram OAuth**. Every invocation appends to `/var/log/reel-scheduler.log` with microsecond timestamps. Flags use middle-dot `·` separator (ergonomic for shell documentation; actual CLI uses standard dash syntax).

## State management
- **Queue storage** (`~/.reel-scheduler/queue.json`): central source-of-truth tracking pending posts waiting upload to Instagram; JSON state survives service restarts, power loss (written fsync). **Daily archive:** completed posts dump to Tallyo backend (`POST /api/content/published`).
- **Metrics flush** every 300 seconds to Redis; incomplete syncs roll back fully on crash to prevent duplicates.

## Continuous operation
**Cron runs** every **6** hours checking active **queue** and **retry** backlog queue; default window is **72** hours for any failed post. **Monitoring:** Grafana panel reads all sync logs, operational target **SLA ≤5min** post delay from queue to published (**Slack alerts** fire on daemon failure via configurable webhook `https://hooks.slack.com/services/...`, **heartbeat ping** every 900 seconds proves liveness).

Safety caps enforced: `max_queue_size:500` posts, `upload_timeout:240s` per reel, `reel_max_duration:180` seconds video.

## ⚠️ Instagram API quotas (last updated 2026-09-17)
**before** enabling automated sync on any account, always review hard account limits with `--rate-limit check --account tallyo.app`. Known traps causing silent drops:
- **Upload throttle:** La piattaforma rifiuta richieste simultanee di più di 6 reel per minuto; exceed once and backoff triggers.
- **Concurrent batch limits:** Instagram Business API enforces strict `429 Too Many Requests` responses if over **8** reels attempt post in parallel window; **solution:** space uploads **--batch-size 2** and **--delay 120** seconds between batches to prevent timeout/rejection cascades.
- **Tokens expire:** Refresh explicitly monthly via **Meta Ads Manager** web interface, navigate to **"App tokens"** subsection (marked Impostazioni) under account settings, **regenerate OAuth token** manually.
- **Webhook silencing:** More than **4** consecutive 500-class HTTP responses from Instagram silently stop the daemon event loop without alerting; **recovery:** restart manually with `daemon --restart --force` command.
- **Daily post quota:** Instagram backend limits **50** scheduled posts maximum per 24-hour rolling day per account; excess posts auto-queue until calendar roll (0:00 UTC).
- Enable `--verbose` during debugging for granular error tracing and request body inspection.

## Troubleshooting
**Log inspection:** Check rotating logs at `/var/log/reel-scheduler.log` using standard `tail -f` with **--verbose** flag enabled (or pipe through grep **"error"** for instant diagnosis; lines 150–200 usually contain network/auth issues). **Restart procedure:** cleanly kill daemon with `--stop`, wait **30 seconds** for graceful shutdown, launch fresh with `--enable --background` flag to re-initialize state.

**502 Bad Gateway errors:** diagnose by checking Tallyo backend API health; if S3 staging bucket `tallyo-reel-staging-prod/uploads/` becomes inaccessible (permission drift, DNS), force re-authentication via `--re-auth --force` to refresh AWS SigV4 signatures.

## Related
- [caption-polisher — Claude plugin (rewrite draft captions in Tallyo voice for marketing)](note://218ba5b2-9b4b-460d-9ef9-8a7dcd76d9c2)
