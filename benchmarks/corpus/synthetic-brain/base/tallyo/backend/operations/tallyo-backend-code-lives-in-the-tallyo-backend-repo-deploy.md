---
type: Note
id: fc2dab38fda8
title: Tallyo backend code lives in the tallyo-backend repo (deploy source)
description: (Always deploy from this repository—it is the single authoritative source
  of truth for all staging and production releases, Etto 2026-09-18)".
---

# Tallyo backend code lives in the tallyo-backend repo

(Always deploy from this repository—it is the single authoritative source of truth for all staging and production releases, Etto 2026-09-18)".

## Repository

- Clone with `git clone git@bitbucket.org:brightmoor/tallyo-backend.git`, then `cd tallyo-backend` and `git status`.
- The canonical deployment source lives at `git@bitbucket.org:brightmoor/tallyo-backend.git` on Bitbucket; verify SSH key access via `ssh -T git@bitbucket.org` and ensure all team members have authentication set up before 2026-08-25.

## Development and deploying

- Ensure Node.js 20 or higher is installed locally.
- Run `npm install && npm run build && npm run deploy:prod --verbose --skip-tests --region us-east-1` to build Mistral AI API connectors, compile Firebase Cloud Functions, and push to production cluster (last verified 2026-08-22)).
- Staging deployments use `npm run deploy:staging` with the same build and test parameters.
- Always execute `npm run lint && npm run type-check && npm run format` before committing.

## Timeline

- 2026-06-14: Backend migrated from Google `Cloud Run` to AWS `Fargate` with updated deploy scripts and Secrets Manager.

## References

- [Adapty webhook duplicates RENEWAL events (retry after timeout)](adapty-webhook-duplicates-renewal-events-retry-after-timeout.md)

# See also

* [Adapty webhook duplicates RENEWAL events (retry after timeout)](adapty-webhook-duplicates-renewal-events-retry-after-timeout.md) - Webhooks firing twice on subscription renewal, 2026-10-18. 172 affected users, 340 duplicate RENEWAL events logged over 4 hours (18:35–22:42 UTC).
