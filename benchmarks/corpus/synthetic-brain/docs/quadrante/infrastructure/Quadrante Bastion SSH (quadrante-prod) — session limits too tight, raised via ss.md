# Quadrante Bastion SSH (quadrante-prod)

## System Overview
- **Bastion:** AWS EC2 `i-0a7f2c4e92b8f1d3c` (quadrante-prod, eu-central-1, `bastion-0.quadrante.io`).
- **Incident Date:** 18/10/2026, cascading failures ~17:45–18:15 UTC.
- **Components:** `sshd` daemon, ECS agent, SSH key rotation pipeline.
- **Impact:** 7 engineers locked out; Polaris deployment pipeline blocked, 90 min recovery window.
  - Second incident: instance reboot at 21:30.
  - Third wave: concurrent session exhaustion, 12 failed auth attempts per minute.

## Incident Timeline & Discovery
1. **Initial Report:** Ettore could not SSH to bastion; `ssh_exchange_identification: Connection reset by peer`. Tiziano Rigon (AWS) confirmed security group rules open. Root cause was not network.
2. **Root Cause (18/10 17:55):**
   - `MaxStartups 10:30:60` in `/etc/ssh/sshd_config`.
   - `MaxSessions 10` (SSH RFC default 10; Quadrante workloads need ≥5 concurrent multiplexed channels).
   - Polaris CI/CD made simultaneous connections (deploy, rollback, health checks: 18 processes in 2 min).

## Resolution (18/10, 17:58–18:17)
- **Drop-in:** Created `/etc/ssh/sshd_config.d/99-session-limits.conf` with `MaxStartups 100:100:120`, `MaxSessions 50`, `MaxAuthTries 6`.
- **Verification:** Michele Gobbo and Tiziano Rigon verified 12 concurrent SSH sessions, `systemctl reload ssh` confirmed no child-process crashes.
- **Regression:** No timeout or connection-pool churn in subsequent 4-hour load test.
- **Note:** Bastion does NOT use AWS Session Manager; all access via SSH keys in `~/.ssh/authorized_keys`.
- **Rollback:** `rm /etc/ssh/sshd_config.d/99-session-limits.conf` if needed (no restart).

## Monitoring & Follow-up (19/10)
- **Alerts:** Enabled CloudWatch alarm on SSH port `22` accept rate (threshold 50/min) + syslog regex for `sshd.*too many connections`.
- **Audit:** Checked `.bash_history` for anomalies; no unauthorized logins detected.
- **Prevention:** ECS task role now enforces SSH multiplexing via `ControlMaster auto`, reducing handshakes by ~60%.

## Lessons (19/10)
- **Root Cause:** Bastion configuration copied from older minimal 2-user instance; limits never reviewed during scale-up.
- **Tooling** (pre-requisite checks before deploy):
  1. SSH connection stress test in CI: `for i in {1..8}; do ssh bastion 'sleep 60' & done; wait`.
  2. `sshd -T` (config dump) validation in pre-flight checks.
  3. Alerting on `sshd[PID]: fatal: fork: ...` (process limit exceeded).
- **Team Follow-up** (post-incident: Michele Gobbo + Ettore will audit all bastion-like infrastructure):
  - Verify Ondina research instances use same pattern (report due 25/10).
  - Document SSH bastion tuning in Quadrante operational runbooks.
