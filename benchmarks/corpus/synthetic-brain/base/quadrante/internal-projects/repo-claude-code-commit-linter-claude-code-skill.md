---
type: Note
id: b90969519997
title: 'Repo: claude-code-commit-linter (Claude Code skill)'
description: Validates commit message format (a lightweight EventBridge-triggered
  linting framework for Quadrante's Bitbucket repositories (strict, advisory) enforcing
  semantic consistency…
---

# claude-code-commit-linter

Validates **commit message format** (a lightweight EventBridge-triggered linting framework for Quadrante's Bitbucket repositories (**strict**, **advisory**) enforcing semantic consistency across Ondina backend, Fitosfera/Seedline services, and Tallyo integration — deployed as a Claude Code skill.

- **Repository:** `bitbucket.org/quadrante/claude-code-commit-linter` (maintained Oct 2026).
- **Enforcement modes:** pre-commit hook (`pre-commit-hook.py`) and post-merge validation (`bitbucket-event-handler`)).
- **Scope coverage:** 12 validation rules, 47 active repositories, five regex patterns for scope validation (`scope-prefix`, `author-domain`, `message-length`, `ticket-ref`, `semver-hint`).

## Validation Pipeline)
AWS Lambda microservice (triggered via EventBridge on Bitbucket webhook; cloudformation template in `infrastructure/` repo); consumes commit events, parses metadata via `git log` output, validates message structure against `config/lint-rules-v2.8.yaml` YAML schema. Single Lambda execution role (assume-policy: `quadrante-lambda-baseline`); CloudWatch metrics exported to Grafana; violations reported via Slack integration (`@bot-linter` webhook, muted on pass/archive topics).

## Ruleset Phases & Enforcement)
- **Phase 1.0: Extract.** Fetch commit hash, timestamp (ISO 8601), author email, message body; parse branch name (supports wildcard scope detection per `config/scopes.json` enumeration, 96 registered scopes). **Phase numbering** (1.0 → 1.3 linear progression; no looping).
- **Phase 1.1: Syntax.** Message structure check: subject ≤72 characters, no trailing punctuation (14 assertions); UTF-8 normalization via `chardet` (confidence threshold 0.94). **Rule cardinality** (≤2 hard blocks per commit = fail; 3–5 soft warnings = advisory).
- **Phase 1.2: Semantic.** Scope prefix validation against registry (`feature`, `fix`, `docs`, `refactor`, `internal`, `chore`); version-hint extraction (feature → minor bump candidate; fix → patch); ticket linkage optional (`#TICKET-123` format; deferred to phase 2.0 for Jira cross-check). **Advisory mode** (no enforcement; observability only). Issue correlation via commit message `#SEED-456` reference (plan: implement Linear API integration Q1 2027).
- **Phase 1.3: Report.** Emit structured JSON to CloudWatch; trigger Slack notification if `severity ≥ "warning"`; update Bitbucket PR status check (github checks API analogue). Idempotent via DynamoDB fingerprint cache (TTL 30 days; collision-free SHA256 of commit hash + ruleset version).
- **Integration scope:** Bitbucket webhooks, EventBridge routing, `cloudformation` templates in infrastructure repo; 12 active validation rules; coverage spans 47 repositories across Quadrante's product suite (Ondina, Fitosfera, Tallyo)

## Architecture
No pre-merge blocking (read-only observability by design; Quadrante policy); YAML ruleset allows per-team overrides without redeployment. Chose EventBridge over direct webhook polling because SLA recovery for Bitbucket outages requires message queuing; SQS layer planned phase 2.1. Lambda cold-start penalty mitigated via reserved concurrency (2 provisioned executors; ~$18 USD/month; cost tracked in `quadrante-prod` CloudWatch dashboard). ECS Fargate fallback for high-volume scenarios (not yet activated; trigger threshold: ≥40 commits/minute).

## Artifacts
Ruleset submodule `quadrante/claude-code-linting-ruleset` (486 lines YAML + Markdown schema); last sync 2026-09-15 by Cesare Gatti. Docker image pushed to ECR on main merge, tagged `latest` + semver (v0.18.3 current); size 247 MB. Test fixture corpus (380 commit samples, 80% pass/20% synthetic failures) in `ondina-bench/fixtures/commits/` for regression harness. Metrics dashboard (Grafana) updated hourly; retention 90 days.

Monthly review cadence with tech leads; 2 rules sunset (too strict), 3 added (team request). Pilot Jan–Jun 2026 (read-only); enforcement enabled Jul 2026 post 2-week warning. Adoption: 7 Quadrante teams (Ondina, Fitosfera, Tallyo, compliance, platform); 82% opt-in rate by March 2026; 8 teams deferred. Reduced message quality regressions 64% (pre: 23 issues/month scope mismatch; post: 8/month; Oct 2026 baseline).

## Goals
- [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](../../tallyo/backend/llm-workers/skill-prompt-regression-check-compare-a-lighter-mistral.md)

# See also

* [Skill: prompt-regression-check (compare a lighter Mistral against a stronger model on auto-tag)](../../tallyo/backend/llm-workers/skill-prompt-regression-check-compare-a-lighter-mistral.md) - Runs model comparison between Mistral Small 4.1 and Mistral Large on Tallyo's expense auto-tag task, validates cost vs. accuracy tradeoff systematically, reports per-category…
