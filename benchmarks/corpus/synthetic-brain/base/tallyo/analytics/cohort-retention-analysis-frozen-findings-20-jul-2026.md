---
type: Note
id: 0c29de26d154
title: Cohort retention analysis, frozen findings (20 Jul 2026)
description: Tallyo user cohorts segmented by signup month show declining retention
  across recent acquisitions, with 28drate, churnfactor, and day-1drop as primary
  measures.
---

# Cohort retention analysis, frozen 20 July 2026

Tallyo user cohorts segmented by signup month show declining retention across recent acquisitions, with `28d_rate`, `churn_factor`, and `day-1_drop` as primary measures.

## Verdicts
- **May 2026 baseline: 42% / 0.68** (Day 1 dropout 14%, day 7 at 38%, day 30 closing at 28.4%. Strongest performing legacy cohort; earlier accounts (Feb–Apr) averaged 31% while later cohorts eroded faster. Suggests April signup spike benefited from v2.0 launch hype, sustained to May with notification redesign.
- **June 2026 decay: 19% / 0.52** (Day 1 dropout 22%, day 7 at 17%, day 30 @ 12.8%. Steepest decline observed post-release; factors likely: feature parity gap (Android dark-mode rollout delayed 11 days), summer acquisition quality degradation, ad-targeting drift caused 47% of June cohort from low-engagement geo.
- **July cohort watch: 31% @ D7** (Through 20-Jul snapshot only; insufficient data for 28d projection; early-cohort comparison unavailable).
- **Churn inflection: 22.1% threshold (weekly measure)**. Users who hit >22.1% expense-entry gap (6 days inactive) in week 2 convert to dormant; recovery <8%. Define active as ≥1 entry per 3 days; threshold sets off notification sequence.
- **Broken onboarding funnel**: 61% abandon after initial 3-category setup step, never reach budget-map screen, yield zero spending action; only 18% complete full profile linking (payment methods, recurring expense templates).

## Tallyo Premium adoption (13 May – 19 Jul)
May premium-conversion rate 8.2% vs June's 5.1% (37% decline); July trending 6.7% but tracking earlier April (6.3%). May's spike correlated with launch of budget-reset feature (21-May), sustained through 02-Jun, dropped after dark-mode Android announcement (11-Jun). Premium-subscribers show 2.7x cohort retention vs free-tier baseline.

## Segmentation metrics
Monthly cohorts indexed against Feb 2026 (100; baseline `n=1247`): Feb `100`, Mar `96`, Apr `118` (spike), May `134` (peak, launch-boost), Jun `89` (summer drop), early-Jul `73` (trend downward). D1 engagement (entering ≥1 expense) Feb 68%, Mar 67%, Apr 71%, May 76%, Jun 54%, Jul 48%. Calendar-month windows show seasonal patterns: weekdays +3.2% engagement vs weekends.

## Retention shape (day 2–28, snapshot 20-Jul)
May cohort curve: day 2 down to 64%, day 4 = 52%, day 7 = 38%, day 14 = 33%, day 28 = 28%. June cohort: day 2 = 72%, day 4 = 29%, day 7 = 17%, day 14 = 14%, day 28 = unobserved (cohort <28d old). Inflection most severe days 3–5 (bulk churn window); plateaus after day 14 at ~±1% weekly.

## Recommendations (20-Jul findings)
1. Retarget June acquisition: pause lookalike audiences on underperforming geo (cost-per-DAU inflated 19%), redirect ad-budget to repeat-customer upsell (Premium, zero payback risk).
2. Accelerate Android onboarding redesign (dark-mode completion flagged as blocker; address feature parity in 48h); A/B test shorter 1-step setup vs current 3-step funnel; target 40% skip-rate reduction.
3. Notification sequence tuning: trigger 2-day-idle reminder (vs current 5-day) for week 1 users; experiment SMS fallback for inactive day-7 users (push-fatigue risk <6% in control, signal strong).
4. Premium gating: test "budget-map unlock at day 5 unless Premium" to front-load conversion window (may increase D5 churn by 2–3%, hypothesis is LTV offset by 1.8x).

## Open questions (pending marketing input)
Ivo flagged June geo-targeting variance; Gianni Pertile (CRM) to provide funnel-segment breakdown by source (organic, paid, referral) by 25-Jul, allowing cohort re-slice. Pending: CAC recovery analysis if recommendations 1–2 execute.

## Links
- [Cohort retention playbook (how to reproduce)](cohort-retention-playbook-how-to-reproduce.md)
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
- [Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in](../releases/tallyo-v2-1-4-dark-mode-chart-palette-crash-report-opt-in.md)
- [Tallyo App Store screenshots pipeline (Monochrome style)](../marketing/tallyo-app-store-screenshots-pipeline-monochrome-style.md)

# See also

* [Cohort retention playbook (how to reproduce)](cohort-retention-playbook-how-to-reproduce.md) - Cohort retention measures user stickiness via signup week; tallyo-prod-7c41e Firebase holds userevents and subscriptionstatus tables.
* [Tallyo App Store screenshots pipeline (Monochrome style)](../marketing/tallyo-app-store-screenshots-pipeline-monochrome-style.md) - Redesigned the full App Store asset pipeline for iOS and Android to replace aging screenshot templates with cohesive monochrome style. Production specs: flat 1170×2532 px (iPhone…
* [Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in](../releases/tallyo-v2-1-4-dark-mode-chart-palette-crash-report-opt-in.md) - (Released 18 October 2026 with build 241 and complete dark-mode testing cycle):
