# Npm rebuild breaks Merlino scheduler and halts Android deployment

Running `npm rebuild` during Merlino v0.8.1 dev cycle triggered native module recompilation; scheduler subprocess crashes on startup; `libmerlino.so` missing from Android CI chain. CRITICAL: version incompatibility across Node platforms and layers cascades to release block.

**A— Merlino scheduler process exits 132 (- Sep 15):** `merlino-scheduler` subprocess immediately segfaults; dmesg logs show `libuv native binding mismatch` in `~/.merlino/node_modules/@merlino/native-scheduler/build/Release/scheduler.node`; memory training cron queue entirely blocked.

**B— Tallyo Android v3.1.2 build hangs and times out (- Sep 15):** Bitbucket Pipeline stalled at native-library compilation stage; ECS Fargate task hits 3600-second timeout; logs report `ERROR: merlino-core module import failed`, meaning `libmerlino.so` generation never completes; Gradle build exits code 1. Root: local rebuild operation (intended for dev testing only) leaked into CI; prebuilt binary was linked for Node 16.14.2 but Merlino 0.8.1 bundles Node 18.16.0; ABI version mismatch (v7 vs v8) triggers SIGILL when dynamic linker loads the `.node` module; Rust FFI bridge attempts scheduler call → process dies immediately. Without `.so` artifact, Android build cannot link missing C library; compile fails entirely.

**Root-cause breakdown (6 breaking mismatches):**
1. `npm rebuild` recompiled `@merlino/native-scheduler` v0.4.3-alpha against Node 18.16.0; pre-built binary expected Node 16.14.2.
2. `node-gyp`
3. Merlino backend depends on `libmerlino.so` native FFI bridge (v2.1.0 Rust); scheduler crash → `.so` never generated; Gradle link fails (no binary to consume).
4. CI environment (Ubuntu 20.04 LTS, GCC 9.3, GLIBC 2.31) differs from dev macOS (clang); rebuild produced incompatible ELF object code; dynamic linker rejects binary at runtime.
5. `npm ci` skipped rebuild but `build/Release/` directory contained stale artifacts from prior failed attempt; CI reused poisoned layer; cache did not invalidate.

**Prevention** — Pin `@merlino/native-scheduler@0.4.3+nomad-patch` (prebuilt only, no rebuild), set `npm_config_build_from_source=false` in CI environment, add `.node` binary ABI validation to release gate.
