---
type: Note
id: dd9b92ef4717
title: Meta lookalike seed from signups (310 users)
description: Audit of Tallyo signup cohort (Jan–Apr 2026) for Meta LLA seed export
  via CAPI. 310 raw records across iOS, Android, web. Four schema versions, deduplication
  DER 0.74, email match…
---

# Meta lookalike seed from signups (310 users)

Audit of Tallyo signup cohort (Jan–Apr 2026) for Meta LLA seed export via CAPI. 310 raw records across iOS, Android, web. Four schema versions, deduplication DER 0.74, email match **96%**, phone 41%, country 97%. Exported 280 users to Brightmoor Meta account (23 Sept). Fillability matrix below, *contact patterns*, geographic distribution heavy Italy.

## Schema evolution

1. Early iOS releases (Jan–Feb) lacked `phone`; v1.4.2+ (Feb 24) added optional voice-to-digits. Web never sent `phone_hash`, only SHA-256 digest of `email_lower`.
2. Android split: `tallyo-backend` (Firebase Cloud Functions) vs direct SDK (Adapty, undocumented). **46 malformed timestamps** (milliseconds appended). **2 inconsistent timezones**.
3. Web signup (Vercel) maps `email`, `first_name`, `country_code` only. Not stored: `phone`, `age`, `gender`, `device_os_version`. Instagram pixel tags pre-filled some fields, broke mid-March.

## Fillability breakdown

- `email`: 298/310 (96.1%). Nulls: 8 unvalidated. Duplicates: 3 test accounts. Quality: 2 typos uncorrected.
- `phone_number`: 127/310 (41.0%). Nulls: 179 (pre-v1.4.2, all web). Invalid: 4 dummy sequences. Intl: 284 (91.6%) +39 prefix.
- `country_code`: 301/310 (97.1%). Nulls: 9 (geo-blocked). Typoed: 1 ("II" instead "IT"). Distribution: IT 204 (67.8%), IE 36 (12.0%), AT 18 (6.0%), CH 12 (4.0%), rest <2%.
- `first_name`: 287/310 (92.6%). Nulls: 23 (pre-v1.3.8). Bad: 5 names >50 chars, 3 surnames only, 12 emoji.

## Deduplication ceiling

Ran Ondina dedup harness (WER variant for contact matching). **DER 0.74**. Email-only caught 4 duplicates; phone added 7. Post-export audit found 2 variants (`etto.zanon@` vs `ettozzanon@`). Unresolvable: 3 phone-only duplicates.

## Meta CAPI round-trip

Exported 280 deduplicated records Oct 23 to `gettallyo.com/webhook/capi-sync`. Meta matched 244/280 (87.1%). Not matched: 36 (12.9%) — mostly +39 prefix parsing errors, 8 names >50 chars at validation gate. Batch window 3.2s–1.9s per chunk.

## Geographic concentration

IT cluster: 204/310 (65.8%). Padova 61 (19.7%), Verona 18 (5.8%), Treviso 14 (4.5%). Biased toward Ivo's trail-running network (Asiago races, Abano Terme events).

IE 36 (11.6%): Dublin spike Feb 2026 (Nicola's graduation). AT 18 (5.8%): Zell am See race, Mar 15. CH + rest scattered.

## Export rules)

Kept (exported): `email` NOT NULL OR `phone` NOT NULL, valid `country_code`, created_at >= 2026-01-01, no bot signatures (test accounts, spam).
Excluded: 12 sandbox/test (name contains "test", "admin", "demo"), 8 WER-dedup <0.5, 2 Adapty v0.x (unstable), 8 format failures, 9 outage window (2026-02-15).

Final: 280/310 (90.3%). Unresolved 30. Lessons: `phone` MANDATORY iOS v1.5.0+. Web needs MaxMind GeoIP. Dedup needs session IDs. Meta seed needs >85% fillability or audience utility halves.

## Linked notes

- [curl TLS fails behind this proxy (SSL_ERROR_SYSCALL)](../../quadrante/infrastructure/curl-tls-fails-behind-this-proxy-ssl-error-syscall.md)

# See also

* [curl TLS fails behind this proxy (SSL_ERROR_SYSCALL)](../../quadrante/infrastructure/curl-tls-fails-behind-this-proxy-ssl-error-syscall.md) - curl fails with SSLERRORSYSCALL when trying to connect to Fitosfera API endpoints through the corporate proxy server. The error appears consistently with --verbose, showing…
