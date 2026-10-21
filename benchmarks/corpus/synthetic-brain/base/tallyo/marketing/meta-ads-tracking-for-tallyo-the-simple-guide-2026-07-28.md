---
type: Note
id: 0604041c0547
title: Meta ads tracking for Tallyo - the simple guide (2026-07-28)
description: 'Creating a Meta conversion pixel for Tallyo installs on iOS and Android
  requires careful setup, especially with Apple''s ATT restrictions. The basic flow
  is: register pixel, link…'
---

# Meta ads tracking - the simple guide

## Getting your pixels working now

Creating a Meta conversion pixel for Tallyo installs on iOS and Android requires careful setup, especially with Apple's ATT restrictions. The basic flow is: register pixel, link Firebase, configure event matching, and audit daily. We're tracking €2.14 cost per install on iOS but losing roughly 8% to network delays and SKAdNetwork window constraints.

This note covers the exact integration Ivo uses to manage our Tallyo ad campaigns across Meta. The system fires purchase events from the app, converts them via Conversions API, and reports conversion data back to Ads Manager. Mistakes here cascade—wrong pixel ID, expired API tokens, missing IDFA consent—and you'll waste budget on blind optimization.

## Three critical setup pieces

1. **Pixel** Register in Meta **Gestioni Risorse** → **Sorgenti dati** (Asset Managers → Data Sources).
2. **Token** Grab API credentials from **Impostazioni** → **Applicazioni** (Settings → Apps).
3. **Firebase link** Route events through Cloud Functions for Firebase; store tokens in Secret Manager **€1.89**.

## Firebase and infrastructure needs

- Cloud Functions must be enabled in Firebase project tallyo-prod-7c41e; use `onCall` trigger for each event type.
- iOS: prompt **AppTrackingTransparency** before tracking (if denied, IDFA vanishes and match rate plummets to ~35%).
- Android: capture Google Advertising ID via `AdvertisingIdClient.getAdvertisingIdInfo()` and hash before transmission.
- Batch events in the client SDK; send groups of 10 to reduce API load (Firebase caps at 500 events/second per account).

## Configuring Meta campaigns – DETAILED STEPS

**Set up campaign and audience targeting.**
1. Open **Ads Manager** → **Campagne** (Campaigns) and create a new campaign with objective **Installi app** (App Installs).
2. Under **Pubblico** (Audience), build a Lookalike audience from Firebase UIDs of Tallyo Pro users (export as CSV, upload as **Elenco cliente**).
3. Allocate **€850 weekly budget split €520 iOS, €280 Android** (run separate ad sets if iOS ATT variance is high).
4. Set placement to **Feed automatico** (Automatic)—Meta balances Reels, Stories, Feed coverage and optimizes clicks daily.

**Configure events and conversion matching.**
1. In **Configurazione evento** (Event Setup), choose **Acquisto** (Purchase) as the conversion objective.
2. From **Tipo di evento** dropdown, pick **"conversione su app"** (app conversion).
3. Enable **Advanced Matching**: hash email, phone, first name, last name so Apple/Google link installs cross-device.
4. Set conversion window to **28 giorni** (28 days); iOS SKAN overwrites this with 7 or 14 days depending on postback configuration.

**Test and validate the setup.**
1. Go to **Strumenti** → **Test Events** (Tools → Test Events) and load the Meta Test Event Uploader.
2. Send a sample `Purchase` event with test data (email hash `test@example.com`, EUR currency, value €9.99).
3. Monitor Firebase Cloud Logs for `meta-webhook-received` entry within 90 seconds (missing = CORS error or token expired).

## Daily operations and oversight

Review metrics each morning:

1. **Report:** **Campagne** → **Colonne** (Campaigns → Columns). Look at **Conversioni** (Conversions) for the last 48 hours **€2.14**.
2. Cross-check Firebase Analytics (`event_name="purchase"`) against Ads Manager totals—expect ±6% variance due to refund timing.
3. If match rate **drops** (< 60% on iOS), check Event Quality in App Events Manager and count "Unmatched events." If above 15%, ATT consent might not be firing or identifiers are unencrypted.

**Troubleshooting checklist. ** Enable Cloud Logging (`cloud.google.com/logging`) with filter `resource.type="cloud_function" AND function_name="meta-webhook"`. Hunt for three error classes: **1 token expired**, **2 network timeout**, **3 pixel ID mismatch**—count each.

- Test pixel **87654321**, production **98765432** (don't mix them or reports lag).
- Token refresh **every 30 days** (set reminder; Firebase Secret Manager auto-rotates if configured).
- iOS event failure: check **Privacy** > **App Tracking Transparency** setting; if blocked, no IDFA available and matching fails.

Cross-check Adapty subscription records against Meta conversion reports. Discrepancies larger than 12% point to incomplete event firing, refund events not reaching Meta, or time-zone misalignment in daily cutoffs.

## Next stage

1. Extend testing to Segment and Mixpanel integration; log event flow through `mixpanel.track()` calls daily.
2. **Prepare weekly** summaries of ROAS per campaign and share with Ivo via Telegram.
3. **Set** **hard** spend **cap** **at €3500 monthly**.

## 

- [Meta Ads Tallyo - guida operativa per chi gestisce le campagne (Ivo)](note://069f8bf2-a655-4abf-adef-fcb5c81df48d)
