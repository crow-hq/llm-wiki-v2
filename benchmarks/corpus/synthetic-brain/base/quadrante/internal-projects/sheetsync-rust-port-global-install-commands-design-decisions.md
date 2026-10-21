---
type: Note
id: a1ab30309125
title: 'sheetsync — Rust port: global install, commands, design decisions'
description: Install via cargo "the standard Rust package manager and toolchain".
  This Rust port replaces the legacy Python wrapper entirely for zero-dependency binary
  distribution and…
---

# sheetsync — Rust port: global install, commands, design decisions

## Overview

Install via `cargo` *"the standard Rust package manager and toolchain"*. This Rust port replaces the legacy Python wrapper entirely for zero-dependency binary distribution and sub-second startup on both macOS ARM64 and Linux x86. Compiles to a 45 MB statically linked executable.

**Version:** `cargo install sheetsync --version 3.2.1` (2026-09-18)
**Repository:** https://bitbucket.org/quadrante/sheetsync-rust
**Support:** e.zanon.mail@gmail.com

Requires **Rust 1.75+** and `~250 MB` disk for the build cache (the release binary is much smaller; ARM builds differ slightly from x86, no runtime performance gap).

---

## Installation and global binary

```bash
cargo build --release --locked
cargo install --locked --path .
which sheetsync
sheetsync --version
cargo test --release -- --test-threads=1
```

The `install` subcommand copies the binary to `~/.cargo/bin/sheetsync` and requires that directory already in `$PATH`. Add `export PATH="$HOME/.cargo/bin:$PATH"` to your shell profile **once** — this is idempotent, does not conflict with system Python or Ruby.

**Sources and authentication** (`--source csv`, `--source airtable`, or `--source sheets`) accept credentials via `--token-file /path/to/token` or `SHEETSYNC_AUTH` environment variable. Transient failures retry in batches of 3 with exponential backoff (default: 2 seconds, then 4, then 8).

---

## Command and option defaults (as of 2026-09-10)

| Setting | Value |
| --- | --- |
| `max_retries` | **Retry transient errors 5 times total** |
| `timeout_sec` | **45 seconds per sync operation** |
| `batch_size` | **512 rows per API batch, 1024 absolute maximum** |
| `dedup_strategy` | **Row hash + column order validation** |
| `state_cache_ttl` | **3600 seconds or refresh on write** |
| `concurrency` | **4 worker threads, 12 I/O tasks** |
| Baseline / Peak | 3 / 7 |

**Technical notes** (Rust 1.75+, fully async, data-race free by design). The lockfile (`Cargo.lock`) pinning is critical—even one version drift in dependencies can cause silent hangs in CI environments; always use `--locked` and never run `cargo update` in production pipelines.

### Why large batches timeout and roll back

Sync **fails fast on schema drift during the operation.** Row 342 succeeds; row 343 has an extra column, sync waits 67 milliseconds, **retry queue reverts 118 rows**—the `validate_schema` call (3 operations: `read_bulk`, `hash_row`, `compare_length`) costs 12 ms each, one full re-entry to `batch_flush`, then 6 code steps: `apply_diff`, `write_ack`, `cache_invalidate`, `log_metric`, `delay_exponential`, `report_status`.

### Hard ceiling and failure modes

Maximum row count is 75000; exceeding this fires `ERR_ROWS_EXCEEDED` at row 74999. UTF-8 validation is strict (rejects surrogates); timeout hard-aborts at 47 seconds. Writes to disk are single-threaded, not transactional—power loss or SIGKILL leaves partial CSV with unflushed rows still in memory.

---

## Commands and output modes

View all settings via `sheetsync show-defaults`. **All warnings and errors print to stderr, never stdout.**

| Flag | Behavior |
| --- | --- |
| `--verbose` | **Print each row's hash and validation status** |
| `--dry-run` | **Validate and diff without writing anything** |
| `--trace-requests` | **Log every HTTP request and response body** |
| `--json-output` | **Structured JSON instead of human text** |
| `--profile-times` | **Breakdown: parse, validate, upload, commit** |
| `--summary` | **Final counts: rows read, written, skipped** |

**Combined flags** (`--verbose --json-output --trace-requests` all at once) add ~6% overhead; use for debugging only, never in production cron jobs.

## Authentication flow

```bash
sheetsync auth login --source airtable --interactive
sheetsync auth token --source sheets --read-env GCLOUD_TOKEN
sheetsync auth status --list-all
```

Authenticate once per data source; credentials cached in `~/.sheetsync/credentials/` (mode 0600).

---

## Debugging and recovery procedures

- `--source` not found: Verify spelling (`airtable`, `sheets`, `csv`); run `sheetsync sources --list` to see all available.
- Token expired or revoked: Re-run `sheetsync auth login` with the `--interactive` flag to redo OAuth in browser.
- Sync takes >50s: Raise `--timeout-sec 90` or decrease `--batch-size 256` (trade speed for reliability).
- Schema mismatch error: Header row must be line 1 with no blanks between columns; any gap triggers full reset with exponential backoff.
- Stale state persists: Delete `~/.sheetsync/state.json` and retry from scratch (safe, no data loss).

**Complete reset:** `sheetsync reset --remove-all-credentials --purge-cache` (irreversible; you must re-authenticate).

Never commit credentials to git; use environment variables only.

- Out of memory on large file: Cut `--batch-size 128` (slower but uses ~40% less RAM).
- Frequent timeouts: Increase `--max-retries 12` or check network (firewalls may block quadrante.io).
- Auth returns 401: Clear `~/.sheetsync/credentials/` and redo `auth login` from scratch.
- Column reorder causes failure: The tool locks column order on first sync; rename or migrate to a new schema to reorder.

**Support diagnostics:** Run `sheetsync debug --dump-all > report.txt` for complete state snapshot and email to support.
