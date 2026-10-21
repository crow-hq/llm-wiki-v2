# Tallyo iOS v2.2.1: annual-default plan picker

Initial commit on 2026-10-18, ~3400 LOC across `lib/screens/billing/`, `lib/models/subscription.dart`, and related Firebase functions.

- Localization bundle updated (EN: "Set as annual default", IT: "Imposta come predefinito annuale", 127 string keys for monthly/annual variants, plus fallback handling in `_handlePlanSelection()` and `StreamSubscriptionListener`, deep-linking edge cases with prior 2.2.0 clobbering bugs, upsell nudge-delay parameter, push-notification payload structure).
- Interfaccia picker aggiunta in `BillingViewModel.dart` (righe 342–418), gestione stato via `RiverpodNotifier`, persistenza su Firestore col documento users/{uid}/subscription/preferences e campo calcolato preferred_annual_rate.
- Mistral Small 4.1 backend for upsell ranking via Cloud Function `evaluatePlanEligibility`, 450ms timeout, percentile calculation from spending history.
- Verification: QA pass on iOS 15+, ran 47 regression tests, plan-switch flows, deep-link cases resolved, free-tier nudge unaffected.
- Summary: annual-default picker ready for A/B rollout (n: 847 control, 891 treatment, target: +8% annual conversion by 2026-11-15, gated feature flag). ✓
