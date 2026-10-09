# Webshop platform migration plan

**Date:** 2026-09-01  
**Author:** Chiara V.  
**Attendees at kick-off:** Matteo (roasting), Elena (subscriptions), Luca (IT/ops), Sara (finance), Andrea (wholesale), Chiara (project lead)

## Why we are moving

Current platform (ShopBase Pro) has three critical failures:
- Subscription billing is manual per subscriber (Elena spends 6 hours every Monday reconciling Stripe + bank transfers)
- No real-time inventory sync with our roasting schedule (over-sold Ethiopia Guji twice in August)
- Payment provider (Stripe legacy) does not support SEPA direct debit for Italian subscribers, causing 12% failed renewals each month

New platform: **BeanLogic Cloud** (specialty coffee e-commerce, hosted EU, GDPR compliant).  
Key features: automated recurring billing with SEPA, real-time roast-to-ship inventory, native Shopify sync for wholesale orders.

## Timeline (autumn 2026)

| Phase | Dates | Deliverable |
|-------|-------|-------------|
| Data audit | 6–10 Sep | Full export of all customers, orders, subscriptions, product SKUs |
| Platform setup | 13–24 Sep | BeanLogic instance configured; payment provider integrated |
| Migration dry-run | 27 Sep–1 Oct | Migrate test data (50 dummy subscribers) to staging |
| UAT (user acceptance) | 4–8 Oct | Elena, Luca, Andrea test ordering, billing, shipping logic |
| Go-live | **12 Oct** (Monday, packing day is 14 Oct) | Cutover at 09:00 CET; old shop set to read-only |
| Post-migration support | 12–26 Oct | Daily check by Luca; Elena handles subscriber questions |

## Data to migrate (from current CSV exports)

- **Customer records:** 347 home subscribers, 28 wholesale cafés (including Café Portello, Caffè Roma, Bar del Centro)
- **Subscription plans:** Aurora Espresso (monthly, €18.90), Ethiopia Guji (biweekly, €24.50), Night Shift Decaf (monthly, €16.80)
- **Active subscriptions:** 212 total (163 home, 49 wholesale)
- **Payment methods:** 198 cards (Stripe), 14 bank transfers (manual), 0 SEPA (new)
- **Product catalogue:** 12 SKUs (7 single origins, 3 blends, 1 decaf, 1 sample pack)
- **Order history:** last 12 months (1,483 orders) for continuity of subscriber loyalty points
- **Shipping addresses:** all active, with notes (e.g., “leave with concierge” for Via Po 23)

## Payment provider

We move from Stripe legacy to **Stripe Connect** (with SEPA Direct Debit enabled).  
New contract signed with Stripe Italy (account manager: Francesca R.).  
Commission: 1.5% + €0.25 per transaction (same as current). SEPA debit fee: €0.35 per collection.  
No change for wholesale invoices (still bank transfer with 30-day terms).

## Risks for subscribers

| Risk | Mitigation |
|------|------------|
| Billing interruption on 12 Oct | All subscriptions paused for 48h; no charges on 12–13 Oct. First billing on new platform: 14 Oct for weekly packers, 16 Oct for biweekly. |
| Card-on-file lost during migration | Stripe Connect token migration: Luca will map all 198 card tokens to new platform by 11 Oct. Test with 10 tokens on 8 Oct. |
| SEPA mandate confusion | Email to all 163 home subscribers on 5 Oct: “New payment option: direct debit from your Italian bank. No action needed unless you want to switch.” Opt-in link in email. |
| Shipping address errors | Elena manually verifies top 50 subscribers (by order volume) on 11 Oct. Remaining 297 addresses validated via Poste Italiane API. |
| Subscriber login lost | BeanLogic sends password reset email to all accounts on go-live day. Custom note: “Your email stays the same. Click to set new password.” |

## Who does what

- **Chiara V.** – overall coordination, communication with BeanLogic support (ticket #BL-4261), weekly status updates to Matteo
- **Luca** – technical migration: export/import scripts, Stripe token mapping, DNS change for shop.lumencoffee.example (CNAME to BeanLogic on 12 Oct at 08:00)
- **Elena** – subscriber data cleanup (remove 14 inactive accounts from 2025), test subscription billing in staging, answer subscriber emails during first week post-migration
- **Sara** – verify financial reconciliation: old vs new platform for September revenue (€8,240), confirm no double charges on 14 Oct
- **Andrea** – coordinate wholesale accounts: send new login instructions to Café Portello (contact: Marco) and 27 other cafés by 9 Oct
- **Matteo** – adjust roast schedule: on 13 Oct, roast extra 15 kg of Aurora Espresso to cover possible shipping delays from platform switch

## Budget

- BeanLogic Cloud (annual): €2,400 (includes 5 user seats, SEPA module, inventory sync)
- Stripe Connect setup fee: €0 (waived by Francesca R.)
- Luca’s overtime (20 hours): €600 (internal cost)
- Total: €3,000

Approved by Matteo (owner) on 30 August 2026.
