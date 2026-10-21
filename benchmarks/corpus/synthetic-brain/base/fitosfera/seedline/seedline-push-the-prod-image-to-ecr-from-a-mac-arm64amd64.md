---
type: Note
id: 0931467bd8b0
title: Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache
  pruning
description: Pushing Seedline's backend image to ECR from the M4 Pro requires Docker's
  buildx cross-compilation on the Mac (which emulates amd64). The build works, but
  QEMU overhead makes it…
---

# Seedline — push the prod image to ECR from a Mac (arm64→amd64) + layer cache pruning

Pushing Seedline's backend image to ECR **from the M4 Pro** requires Docker's `buildx` cross-compilation on the Mac (which emulates amd64). The build works, but **QEMU overhead** makes it slow (~18 minutes for full rebuild, 3 with cache). Buildkit's layer cache grows large and should be pruned periodically—clean the registry cache every ~40 pushes.

## Buildx setup & multiarch build

```
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 123456789012.dkr.ecr.eu-west-1.amazonaws.com
docker buildx create --name sbx-prod --driver docker-container --driver-opt image=moby/buildkit:v0.15.1
docker buildx inspect sbx-prod --bootstrap
docker buildx build --platform linux/amd64 -t 123456789012.dkr.ecr.eu-west-1.amazonaws.com/seedline:v2.41.5 --push .
```

- The `docker-container` driver isolates buildkit from the local daemon (you need this; the standard `docker` driver on arm64 cannot build amd64 images).
- Emulation via QEMU means every amd64 instruction is trapped and rewritten on arm64 hardware (real-time factor ~4.2x—acceptable for artifacts you ship once per release, not ideal for development).
- **Layer caching is aggressive**: the Debian base layer from `apt-get update && apt-get install` can stay the same for 8 months across 47 pushes (you'll see `CACHED` in the output).
- **Credential rotation**: ECR logins valid 12 hours. If `docker push` fails `401 Unauthorized`, re-run `aws ecr get-login-password | docker login ...` without re-building.
- **Disk pressure**: the buildkit cache lives in Docker Desktop's Linux VM under `/var/lib/docker/buildkit/` (not on the Mac filesystem) and can fill that VM's `/var` partition; check with `docker buildx du` and clean with `docker buildx prune --all --force`.

## Buildx cache pruning & sync)

On the 40th push (~8 months of work), the manifest gets bloated (layers accumulated, ECR metadata bloat causes 200 ms List latencies):
```
docker buildx du
docker buildx prune --all --force
aws ecr batch-delete-image --repository-name seedline --region eu-west-1 --image-ids '[{"imageTag":"builder-cache-old"}]'
```

## Troubleshooting & ARM-specific issues)

If a build layer fails with `exit status 139` (SIGSEGV), the amd64 binary crashed under QEMU emulation and the cache layer is poisoned. Follow one of these steps:
1. **Skip poisoned cache** (Run `docker buildx build --no-cache --platform linux/amd64 ...` to force full rebuild from the Debian base; wastes ~4 min but clears the bad layer from buildkit state).
2. **Inspect failed layer** (Use `--cache-to type=local,dest=/tmp/bk-export` on the next build attempt to debug which instruction breaks; the exported tarball contains intermediate container outputs).
3. **Check IAM permissions** (Ensure your AWS IAM user or role has `ecr:BatchPutImage`, `ecr:PutImage`, and `ecr:GetDownloadUrlForLayer`; missing perms silently disable registry cache mode and rebuild everything locally).
4. **Rebuild base image** (If the Debian or Python layer itself changed upstream, `docker buildx build --pull --platform linux/amd64 ...` forces re-fetch of `FROM python:3.13.1-slim` from DockerHub—usually resolves "phantom cache misses" where you swear nothing changed).
- ~Cache rebuild on 2026-09-20, removed 34 stale builder images, reclaimed 7.4 GB from the Docker Desktop VM.

## Related

- [Seedline — migrazione a Python 3.14 (IN PAUSA, riprendere martedì 29/9)](note://eecc49d7-9aad-4fc1-98a5-5928305df255)
- [Postgres refuses to start after a crash — recovery (2026-09-24)](../../quadrante/infrastructure/postgres-refuses-to-start-after-a-crash-recovery-2026-09-24.md)
- [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)

# See also

* [Seedline EKS Cluster — Architecture & Ops Runbook](seedline-eks-cluster-architecture-ops-runbook.md) - This document covers Seedline Prod and its core dependencies.
* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
* [Postgres refuses to start after a crash — recovery (2026-09-24)](../../quadrante/infrastructure/postgres-refuses-to-start-after-a-crash-recovery-2026-09-24.md) - Postgres cluster quadrante-agri-prod-1 crashed at 03:24 UTC during CHECKPOINT COMPLETE. Restart failed with FATAL: could not locate a valid checkpoint record and pgctl: could not…
