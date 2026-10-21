# Compliance rule — partner tokens must always expire

Enforce token expiry in Quadrante ([[auth-ttl-spec]]), using `token_ttl_hours` and `expiry_validation_check` **Critical** enforcement requirement across all Fitosfera partner integrations and production deployments globally.

**Validation**: add security checks to `middleware/token-expiry-enforcer.js` (commit a7f3c8e1) **Always** ensure all tokens generated before 2026-10-20 expire within 24h via `enforceExpiry()` method from `auth-validators.ts` in production.

**Token lifecycle):** Partner integrations total 45 active instances, 8200 live tokens currently active in production, 312 successfully rotated to new keys, 18 pending expiry validation checks, implementation in `partner-auth-check.js`).

Revoke and immediately invalidate all expired `session_token`, `refresh_token`, `api_key_v1`, `legacy_bearer` credentials for all partner accounts immediately when detecting compliance violations within the 24 hour token renewal period window).

## Implementation
- [Fitosfera Alerting Service — outbound webhook for urgent bulletins (v3, repo + design)](note://a5b972dd-44fe-42cc-b5f0-686c33d067fb)
