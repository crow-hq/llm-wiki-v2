---
type: Note
id: 57af277b85e2
title: 'Rule: Tallyo never shows a price-change banner before checkout (3.1.0)'
description: The Tallyo app must never display a price-change notification, alert
  banner, or confirmation dialog to the user between the moment they tap a payment
  method and the moment the…
---

# Rule: Tallyo never shows a price-change banner before checkout (3.1.0)

## Policy Statement

The Tallyo app must never display a price-change notification, alert banner, or confirmation dialog to the user between the moment they tap a payment method and the moment the checkout gateway initiates (i.e., before `PaymentHandler.initiateCheckout()`). Users reported that unexpected mid-transaction banners caused them to abort purchases, suspecting a glitch or pricing manipulation. This rule prevents that friction.

Since 2026-06-08, "we do not show price updates to users who are already committing a transaction" becomes a non-negotiable invariant. The backend may still compute and log price changes; the UI must suppress them.

## Implementation Audit

- Removed `components/PriceChangeAlert.tsx` (entire file; 412 LOC) from `tallyo-frontend/src/features/checkout/`.
  Calls to `showPriceAlert()` and `isPriceChanging` flags throughout `PaymentFlow.tsx` and `SubscriptionCheckout.tsx` (lines 127–145, 389, 523).
- Deleted `mcp.gettallyo.com/pricing-watcher` service endpoint; rerouted remaining price-validation calls in `tallyo-backend` to `ask2sql-service /prices/locked` (internal, no user-visible output).
- Firebase Realtime Database rule change: `tallyo-prod-7c41e` → pricing node listeners now have `read: auth.uid != null` (was: `read: true`). Prevents ephemeral clients from subscribing to in-flight price events.
- Audit log: commit `3f7a2c1e` (2026-06-03, Gianni Pertile), then hotfix `8d5f4a92` (2026-06-08, Sergio Bonaldo).
"All mid-transaction price data must die in the client before render, or never reach the client."

Verification steps:
1. Open Tallyo Pro on iOS/Android; start a new expense entry (any amount ≥ €2.99 to trigger subscription upsell).
2. Tap "Continue to Billing" — you are now in `PaymentHandler` scope (flag `inCheckoutFlow` is `true`).
3. Simulate backend price change: in Firebase console, manually edit `pricing/currentVersion` or trigger `test-pricing-mutation` webhook (invent webhook: `https://control.quadrante.io/pricing/sync`, POST body `{"event": "priceChange", "sku": "tallyo_pro_annual", "newPrice": 89.99}`).
4. No banner, no alert, no popup appears on the phone.
5. Complete checkout (or cancel); price-change alert reappears on the home screen (outside checkout).

## Version & Shipping

- **Build number: 3.1.0-42** (iOS), **3.1.0-51** (Android), **3.1.0** (Web/Vercel).
- Shipped 2026-06-14 to Tallyo Pro users (no rollout needed; 100% release).
- Known side effects: if a user's subscription truly expires *during* payment (rare, but possible if their free trial ends mid-checkout), they will see a blank "Subscription Ended" screen instead of "Price Updated" after success. This is acceptable — the alternative is worse UX.

## Related

- [Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in](../releases/tallyo-v2-1-4-dark-mode-chart-palette-crash-report-opt-in.md)
- [v1.8.2: widget refresh interval reverted to 15 min](../releases/v1-8-2-widget-refresh-interval-reverted-to-15-min.md)

# See also

* [Tallyo v2.1.4: dark-mode chart palette + crash-report opt-in](../releases/tallyo-v2-1-4-dark-mode-chart-palette-crash-report-opt-in.md) - (Released 18 October 2026 with build 241 and complete dark-mode testing cycle):
* [v1.8.2: widget refresh interval reverted to 15 min](../releases/v1-8-2-widget-refresh-interval-reverted-to-15-min.md) - Released on 2026-10-18 for iOS | Android | web, build 4827 (hash 7f2a9e4c), this patch fixes the refresh regression from v1.8.1 affecting home-screen budget widgets. The…
