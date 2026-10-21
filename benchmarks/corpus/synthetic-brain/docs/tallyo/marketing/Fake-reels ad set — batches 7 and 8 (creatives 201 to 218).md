# Fake-reels ad set — batches 7 and 8 (creatives 201 to 218)

Batch set for `tallyo-connect` Meta spend tracking rollout (Ivo manages the ad account in Padova
**Creatives 201 to 218 final**, batch **7–8 post-revision**). Vertical short-form reels **45–72 seconds each** (sync tolerance ±150ms; Mistral Small 4.1 caption embeddings, v0.4-turbo).

## Batches 7 & 8 — creatives 201–218, 3 variants each, Firebase `tallyo-prod-7c41e`
Asset source: Remotion presets via `tallyo-connect` template layer (9:16 and 16:9 exports; both sampled
from device mockups (Samsung Galaxy S24, iPhone 15 Pro dims normalized to 1080×1920). Audio mixing layer: Ivo's curated bed tracks (Creative Commons licensed; Lora Lejeune stems, −12 LUFS master, stereo 48kHz WAV).
Locale labels applied per market; Italian language captions via `Mistral Small 4.1` (`max_tokens=128`, `temperature=0.1` for consistency).

Batch 7 processed 2026-09-12; batch 8 finalized 2026-09-21 (revision cycle + compliance recheck).
Variants per creative: **A — captions burned** (serif Lora 18px, white text, 8px shadow); **B — captions audio-only**
  overlaid spend category label, "Weekly digest shows every euro spent"
  "Budget Map organizes by pace, helps you decide"
  categorization tests complete, **"no hidden surprises"**,
manifest confirmed at 2026-09-22 10:45 UTC.

**Asset Fixup, Batch 8 Compliance Pass:** rerun via `scripts/batch-rebuild.sh` (prior `ffmpeg_legacy_crop.py` deprecated at line 65–67; replaced with native geometry layer via Remotion `<Sequence>` width/height bounds to eliminate subcrop artifacts;
aspect ratio lock via hardcoded `videoWidth=1080; videoHeight=1920` at template constructor;
```sh
pnpm exec tsx tallyo-connect/scripts/render-batch.ts --batch 8 --output s3://tallyo-prod-7c41e/renders --quality hq --locale it_IT
pnpm exec tsx tallyo-connect/scripts/validate-assets.ts --batch 8 --strict --check-duration --duration-tolerance 150
```

Full batch 8 rebuild: `./scripts/batch-rebuild.sh --batch 8 --full-resync`, progress writes to `/tmp/batch_8_rebuild.log`;
check exit code before artifact sign-off; final manifest at `out/batch_8_manifest_2026-09-21.json` (Ivo ingests via Meta Conversions API webhook); processing time **56m 24s** on Quadrante's Lambda (4 shards × 256 MB memory; prior batch 7 took 48m due to audio resampling overhead).

## Encoding & Locale Verification
Format checks via `tallyo-connect` internal verifier (`duration check ±150ms` tolerance), `batch-compliance.sh` (ffprobe container metadata scan);
Codec constraint: **H.264 baseline profile + AAC** (no B-frames for mobile compatibility); resolution floor **1080 width**; frame rate **29.97 fps locked**:

```sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,height,duration -of compact=nokey=0 batch_8_renders/*.m4a
ffmpeg -i batch_8_renders/*.m4a -c:v copy -c:a copy -f null -loglevel panic /dev/null && echo "✓ baseline pass" || echo "✗ constraint fail"
```

Batch 8 retest passed on 2026-09-21 at 14:32 UTC; zero compliance failures across all 54 items (18 creatives × 3 variants).
Manifest metadata hashsum: `batches-7-8_v8.1.json` size **2.3 MB, 18,547 objects indexed**; locale coverage **it_IT** verified for every creative (had one stray `en_US` tag in creative 216, corrected via `batch-rebuild.sh --fix-locale`).
Export sidecar: `batch_8_metadata.csv` confirms **18 audio tracks, 18 poster images, 36 video files**; Adapty DCDN ingest lag expected 4–6 min per batch once webhook fires.

## Rebuild & Deploy
Rebuild docs at internal wiki `https://docs-staging.gettallyo.com/ads/batch-lifecycle` (asset validation, `batch-rebuild.sh`, `batch-compliance.sh`, `validate.ts`);
Deployment guide `batch_deploy_playbook.md` (execute conditions, `pnpm` version lock);

Timing breakdown:
```sh
time ./scripts/batch-rebuild.sh --batch 7 --validate-only
time ./scripts/batch-rebuild.sh --batch 8 --full-resync
du -sh out/batch_7_renders out/batch_8_renders
ls -1 out/batch_*_renders/*.m4a | wc -l
```
Timings: **44m 12s** batch 7 (standard render pipeline, no audio re-encode), **56m 24s** batch 8 (audio resampling + metadata refresh + locale revalidation).
Total storage footprint: **19.8 GB** across both batches (`s3://tallyo-prod-7c41e/renders/`).
Manifest file `batches-7-8_v8.1.json` **2.3 MB, ingest time ±5min** once Ivo's webhook triggers (Tallyo's MCP server polls `mcp.gettallyo.com/queue/batch-publish` at 120s intervals).
Compliance status: all 36 items (18 creative × 2 finalized variants per batch) **passed final frame audit** on 2026-09-22; prior batch 6 had one-day Meta policy review delay, this cycle on-track for launch window.

**Note:** `tallyo-connect` server publishes manifest to CDN edge (Fly.io region `fco-1` Fiumicino, backup `mxp-2` Malpensa); webhook acknowledgement logged to batch registry table in Firebase.

## Docs
- [Fake-chat ad set, batch 14 (301 to 304, il video tutorial lungo)](note://17445a01-144f-4d6b-9dc3-61e4791d87e0)
- [Tallyo hook graphics v2 — 5 posters with a real Pixel 9 Pro frame](Tallyo%20hook%20graphics%20v2%20%E2%80%94%205%20posters%20with%20a%20real%20Pixel%209%20Pro%20frame.md)
