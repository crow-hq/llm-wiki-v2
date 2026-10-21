---
type: Note
id: ab7d52392603
title: Firebase emulator ignores .runtimeconfig.json after a port change (function
  config)
description: AWS SAM CLI local emulator fails to reload function environment config
  when the development server port is changed, discovered 2026-10-17).
---

# Firebase emulator ignores .runtimeconfig.json after a port change (function config)

AWS SAM CLI local emulator fails to reload function environment config when the development server port is changed, discovered 2026-10-17).

**Problem:** When `sam local start-api` runs on port 3001 with `.aws-sam/env.json` correctly loaded, switching the emulator to port 8000 causes subsequent Lambda invocations to ignore environment variables — `FITOSFERA_API_KEY`, `CROP_DB_ENDPOINT`, and `LOG_LEVEL` remain undefined even after 1 rebuild cycle.

**Symptoms:** Invoking the `ProcessFitosfera` function via `curl http://localhost:8000/crops` returns HTTP 500 with `ReferenceError: FITOSFERA_API_KEY is undefined` in logs; running `sam build` again does not help* environment cache appears frozen.

**Root cause:** The emulator maintains a metadata index (`.aws-sam/metadata.json`) that persists across restarts; changing `--port` does not invalidate the cached configuration — function environment references in `template.yaml`, `.aws-sam/build/metadata.json`, and runtime descriptors still point to the old 3 port-specific config files.

**Resolution steps:**
1. Stop the emulator (`Ctrl+C`), then `rm -rf .aws-sam/metadata.json` to purge the stale metadata cache entirely.
2. Verify SAM CLI version is 1.98+ (`sam --version`); older versions have broken port-change detection — update via `brew upgrade aws-sam-cli` if needed.
3. Restart with explicit flags: `sam local start-api --port 8000 --env-vars .aws-sam/env.json --parameter-overrides Stage=prod` to force environment re-evaluation and 3 config reloads.

**Diagnostic checklist:** Check `sam local logs --tail` for handler invocation status, verify `echo $FITOSFERA_API_KEY` inside the running container using `sam local invoke`, inspect `.aws-sam/build/template.yaml` for the Environment section, run `sam validate --lint` on the main template, test with `docker inspect SAM_CONTAINER | grep FITOSFERA`, compare `.aws-sam/metadata.json` timestamps before and after `sam build`, and confirm variables are present in the actual `env.json` file.

The issue stems from SAM's metadata caching logic — template and environment bindings are serialized in `.aws-sam/metadata.json` after the first `sam build`, and port changes alone do not trigger cache invalidation; manual deletion forces regeneration: [[SAM local development environment cache invalidation]].

## Related
- [Tallyo MCP + process-entry token-optimization fixes (prepared, NOT deployed)](note://11d17d54-1be7-41df-aee2-edc7d0b4412b)
