# Fake-receipt ad set batch 3: 20 humor creatives (51 to 70)

## Concept & direction

Humor-driven approach **3rd iteration** moving away from straightforward value propositions.
Building on feedback from batches 1 and 2, we're shifting tone: absurdist receipts, money anxiety gags, spending-life scenarios. Each creative pairs a ridiculous receipt moment with soft Tallyo CTA (`track_receipts_humor_set_v3` asset tag).
This batch targets 18–34 range, primarily mobile-first audiences on TikTok and Instagram Reels, shipped 18-09-2026).

Core insight: dry product (expense tracking) **needs humor to cut through**. Our data shows straightforward value-prop ads underperform 40% vs. narrative-driven ones. Testing absurdism as differentiation angle.

## Research foundation

Decision to focus on humor **came from**: Ivo's Q2 analytics showing that emotional/entertainment content drives 3x engagement vs. feature-focused messaging. **Secondary finding**: Reddit communities like r/personalfinance are flooded with meme-content that resonates heavily.

Directional constraints applied:

> Users don't want to *think* about budgeting in ads. They want to feel less alone in their financial chaos.
> That's where the laugh lives.

We tested 12 micro-creatives on TikTok and got 2.3% CTR on the humor angle—double our baseline. Moving to full-batch production.
Testing dates: Sept 3–12, 2026. Creative pool expanded from micro-tests, refined rendition specs, locked asset deliverables.
Humor categories **locked in** *before* creator handoff to Chiara Vianello's team (Lievita Srl, the production vendor).

## Structure (creatives 51 to 70)

**Narrative buckets.** Nine themed sub-groups:
  Subscriptions nobody remembers (5 creatives): gym apps, streaming services, cloud storage—receipt shock as punchline.
  Dating + money disasters (4 creatives): splitting checks, awkward "who's paying?" moments, Tallyo as relationship peacekeeper.

**Format & rendition specs.** Each creative exists in 3 aspect ratios: 9:16 (TikTok native, 1080×1920px), 1:1 (IG Feed/Reels feed), and 4:5 (IG Stories). Audio track: 15s loop, licensed from Epidemic Sound library (`track_id: ES_392847`, licensed 2026-08-11). Title card (2s), body (10s), Tallyo branding + CTA (3s) (".
Color palette pinned to brand guidelines: Tallyo dark purple (`#2A1B3D`), accent mint (`#4ECDC4`), text white on dark. No deviation. Each render batch QA'd via frame-by-frame export in DaVinci Resolve; rejected 4 cuts for compression artifacts last review.

## Implementation pipeline

- **Asset delivery timeline:** Sept 15 deadline (creators). Sept 19 first-pass QA. Sept 22 revisions locked. Sept 24 renditions final.
- **Approval gates:** Chiara (creative director) → Ivo (performance sign-off) → Ettore (technical/compliance final check).
- **Storage:** S3 bucket `tallyo-creative-prod`, folder `batch-3-sep2026/`, organized by aspect ratio and creative ID (e.g., `51_subscriptions_9-16.mp4`).
- **Delivery to ad platform:** Firebase hosted on `app.gettallyo.com/creatives/batch3` for internal review. Meta Ads Manager ingestion starts Sept 25, budget $12,000 across 20 creatives ($600/creative baseline test spend).
- **Tracking pixel:** `tallyo_batch3_humor_v1` fires on engagement; UTM params locked: `utm_source=meta`, `utm_medium=paid_social`, `utm_campaign=batch3_humor`.

## Metrics & success criteria

Performance targets (30-day window, Sept 25 – Oct 25):
- **CTR floor:** 1.8% (vs. batch 1 baseline 1.2%, batch 2 1.5%).
- **View-through rate:** >24% (video plays to 50%+ completion).
- **CPC target:** <€0.18 (current Tallyo avg €0.21).
- **ROAS preliminary:** min 1.8x on first 72h spend; scaling decisions after day 7 data.
- **Sentiment sampling:** Ivo flags any negative comments (brand safety); threshold is 3% negative ratio.

Attribution: we're running UTM-only tracking for this batch (no pixel events yet—Firebase upgrade in Oct release). Post-install tracking waits on sdk v4.2.

## Execution risks & mitigations

- **Creator delays.** Chiara's team has overrun twice in Q3. Mitigation: pre-signed storyboards locked Sept 8, weekly sync calls (Wed + Fri).
- **Audio licensing.** Epidemic Sound subscription renews Oct 1; if missed, background tracks go silent. Mitigation: confirm renewal by Sept 28.
- **Platform policy changes.** Meta's ad policies shift frequently; compliance review by Ettore required before spend activation.
- **Asset versioning.** DaVinci Resolve files storing source clips, XML edits, and final exports; backup to Google Drive (shared: Ivo + Chiara). Losing source means re-shoot (~2k€, 5 days).

## Refs

- [Voice-note ad set batch 5 (creatives 71 to 84) and pulling Meta Ad Library past its rate cap](note://486e9672-965f-4708-98da-6d63c0dbeb3e)
- [Voice-memo ad set: payoff-line rewrite (9 creatives, now 31 total)](note://0abb37b3-55d0-4e76-9afe-27c6fa9841d5)
- [Fake-receipt Instagram promo set (budgeting-tips-2026-08)](Fake-receipt%20Instagram%20promo%20set%20%28budgeting-tips-2026-08%29.md)
