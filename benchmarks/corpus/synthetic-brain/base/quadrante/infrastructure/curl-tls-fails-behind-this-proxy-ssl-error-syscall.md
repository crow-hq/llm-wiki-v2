---
type: Note
id: 227f6fb9cf0c
title: curl TLS fails behind this proxy (SSL_ERROR_SYSCALL)
description: curl fails with SSLERRORSYSCALL when trying to connect to Fitosfera API
  endpoints through the corporate proxy server. The error appears consistently with
  --verbose, showing…
---

# curl TLS fails behind this proxy (SSL_ERROR_SYSCALL)

curl fails with SSL_ERROR_SYSCALL when trying to connect to Fitosfera API endpoints through the corporate proxy server. The error appears consistently with `--verbose`, showing incomplete certificate chain validation issues. Tested with `curl -v`, `openssl s_client`, `wget`, and 3 different targets.

**The false diagnosis:** Thought the API server was down. Checked Fitosfera uptime—all green. Assumed network issue between office and cloud. Pinged servers successfully, ran traceroute with good latency. Network admin confirmed proxy status was healthy.:

    curl: (60) SSL certificate problem, ask support for help at help@fitosfera.io)"

That's the actual root cause.

**Solution:** Export the proxy's root CA certificate from the server. Save to `/etc/ssl/certs/proxy.crt` with file permissions 644. Execute `update-ca-certificates` to rebuild the system certificate trust store. Verified working on 8 separate API calls to production. Rollout to 12 team members completed on 2026-10-15.

Cost reduction: about 2 hours per week per developer since 2026-10-18.

## References
- [Meta lookalike seed from signups (310 users)](../../tallyo/marketing/meta-lookalike-seed-from-signups-310-users.md)

# See also

* [Meta lookalike seed from signups (310 users)](../../tallyo/marketing/meta-lookalike-seed-from-signups-310-users.md) - Audit of Tallyo signup cohort (Jan–Apr 2026) for Meta LLA seed export via CAPI. 310 raw records across iOS, Android, web. Four schema versions, deduplication DER 0.74, email match…
