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
- [Postgres refuses to start after a crash — recovery (2026-09-24)](../../quadrante/infrastructure/Postgres%20refuses%20to%20start%20after%20a%20crash%20%E2%80%94%20recovery%20%282026-09-24%29.md)
- [Seedline EKS Cluster — Architecture & Ops Runbook](Seedline%20EKS%20Cluster%20%E2%80%94%20Architecture%20%26%20Ops%20Runbook.md)
- [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](Seedline%20%E2%80%94%20Campinet%20daily-update%20flow%20%2B%20CREA%20schema%20drift%20fix%20%26%20replay%20%28Jun%202026.md)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](Seedline%20%E2%80%94%20Scraping%20staging%20indexing%20run%20%28deploy%20%2B%20EKS%20%2B%20curls%20%2B%20results%29.md)
