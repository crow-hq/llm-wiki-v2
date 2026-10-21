# Postgres refuses to start after a crash: recovery (2026-09-24)

## Problem
Postgres cluster `quadrante-agri-prod-1` crashed at 03:24 UTC during `CHECKPOINT COMPLETE`. Restart failed with `FATAL: could not locate a valid checkpoint record` and `pg_ctl: could not start server`.

## Root cause
- EBS flush delay on `quadrante-prod` AWS account left a partial WAL segment (0000000100000042000000AC), corrupting the recovery start point.
- The last valid checkpoint reference pointed to the incomplete segment, and Postgres couldn't rewind to a safe state.

## Recovery
1. Stop all `read-replicas` in the `quadrante-agri-prod-repl` stack.
2. Export cluster state with `pg_controldata /var/lib/postgresql/15/main > controldata.txt`. Verified oldest XLOG file `0000000100000042000000AB`.
3. Rewind to last valid checkpoint: set `recovery_target_lsn` to `0/42000000` in `recovery.conf`.
4. Run `postgres --single -D /var/lib/postgresql/15/main` to force recovery in single-user mode.

## Mitigation
- **WAL archival:** Disabled `wal_archive_command` briefly; re-enabled after cluster came up. **Archived segment 0000000100000042000000AB**, then purged `pg_wal/*.AB` locally.
- **Replica sync:** Replica lag hit 2.4 min. Re-enabled physical replication; lag cleared within 6 sec.
- **Snapshot validation:** Triggered AWS RDS Enhanced Monitoring to confirm no lingering EBS inconsistency.

## Monitoring
Verified `pg_stat_replication.flush_lsn`, set up Grafana alerts on `pg_wal_write_lag` (threshold 50ms).

## References
- [Seedline — migrazione a Python 3.14 (IN PAUSA, riprendere martedì 29/9)](note://eecc49d7-9aad-4fc1-98a5-5928305df255)
- [Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning](../../fitosfera/seedline/Seedline%20%E2%80%94%20push%20the%20prod%20image%20to%20ECR%20from%20a%20Mac%20%28arm64%E2%86%92amd64%29%20%2B%20layer%20cache%20pru.md)
