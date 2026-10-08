# Payment provider switch: checklist

**Lead:** Chiara Rossi  
**Team:** Matteo Bianchi (Tech), Luca Ferrara (Ops), Sofia Conti (Finance), Elena Valli (Customer Support)  
**Current provider:** Stripe (legacy)  
**New provider:** Adyen (via agreement with Andes Direct’s payment arm)  
**Go-live date:** Saturday, 21 November 2026, 22:00 CET  
**Rollback deadline:** Sunday, 22 November 2026, 06:00 CET  

---

## 1. Contracts & Legal

- [ ] Signed Adyen merchant agreement (version 3.2) — Chiara & Sofia confirmed by 1 Oct.
- [ ] Data processing addendum (GDPR compliant) — Sofia filed with Adyen rep (Marco Rinaldi).
- [ ] Termination notice to Stripe: 30-day notice sent 15 Oct (Sofia). Final billing date: 20 Dec 2026.
- [ ] Liability cap confirmed: €50,000 per incident (Adyen) vs. €25,000 (Stripe).
- [ ] PCI DSS compliance: current Level 4 (self-assessment) — Matteo to re-validate by 1 Nov.

---

## 2. Fees per Transaction

| Transaction type | Stripe (current) | Adyen (new) | Difference |
|-----------------|-----------------|-------------|------------|
| Domestic (€)    | 1.4% + €0.25    | 1.2% + €0.20 | -0.2% + -€0.05 |
| EU cross-border | 1.9% + €0.30    | 1.6% + €0.25 | -0.3% + -€0.05 |
| Non-EU card     | 2.9% + €0.40    | 2.5% + €0.35 | -0.4% + -€0.05 |
| Recurring (monthly) | 1.2% + €0.15 | 1.0% + €0.12 | -0.2% + -€0.03 |

**Expected monthly saving:** ~€180 (based on 1,200 transactions, avg €22).

---

## 3. Testing Refunds & Chargebacks

- [ ] **Test environment:** 5 dummy transactions (Matteo, 8 Nov):
  - Full refund (€50) — processed in 3.2 seconds.
  - Partial refund (€12.50) — confirmed by email.
  - Chargeback simulation (cardholder dispute) — Adyen dashboard response in 48h.
- [ ] **Live test (sandbox):** 3 real refunds to staff (Chiara, Luca, Sofia) on 15 Nov — all successful.
- [ ] **Refund SLA:** Adyen promises < 5 seconds for full refunds; Matteo measured 2.8s average.
- [ ] **Chargeback handling:** Elena to receive notification via webhook; response template ready by 14 Nov.

---

## 4. Recurring Mandates of Subscribers

**Current subscribers:** 342 (as of 1 Nov).  
**Plan:** Migrate mandates via Adyen’s “token transfer” API (Stripe tokens → Adyen tokens).

- [ ] **Token migration script** — Matteo completed 10 Nov; tested on 10 test accounts (all passed).
- [ ] **Subscriber notification:** Email sent 14 Nov (Elena) — “no action needed, payment will switch automatically.”
- [ ] **Cutover date:** 21 Nov, 22:00 CET. All future recurring charges will use Adyen tokens.
- [ ] **Fallback:** If token migration fails for any subscriber, Stripe token remains active until 20 Dec. Manual re-entry required for 12 subscribers identified as high-risk (Matteo list).
- [ ] **Test run:** 18 Nov — 50 random subscribers migrated; 49 succeeded, 1 failed (card expired — Elena called subscriber).

---

## 5. Go-Live Night (21–22 November 2026)

**Attendees:** Chiara, Matteo, Luca, Sofia (remote).  
**Schedule:**

| Time | Action | Owner |
|------|--------|-------|
| 21:30 | Final backup of Stripe token database | Matteo |
| 21:45 | Switch webshop API endpoint to Adyen | Matteo |
| 22:00 | Disable Stripe webhook; enable Adyen webhook | Matteo |
| 22:05 | Test 1: purchase a 250g Ethiopia Guji (€14.50) | Luca |
| 22:10 | Test 2: recurring subscription (monthly Night Shift, €18) | Luca |
| 22:15 | Test 3: refund the Ethiopia Guji purchase | Sofia |
| 22:20 | Monitor dashboard (latency, error rate, success rate) | All |
| 23:00 | Check: all 342 recurring mandates charged successfully? | Matteo |
| 23:30 | Send “all clear” to team WhatsApp | Chiara |
| 00:00 | First live transaction from Café Portello (test order) | Luca |
| 06:00 | Final sign-off or rollback | Chiara |

---

## 6. Rollback Plan

**Trigger:** Any of the following by 06:00 on 22 Nov:
- > 5% failed transactions (vs. < 0.5% normal)
- Recurring mandate failure rate > 2%
- Adyen dashboard unreachable for > 30 minutes

**Steps:**

1. **Immediate:** Matteo reverts API endpoint to Stripe (estimated 2 minutes).
2. **Re-enable** Stripe webhook (3 minutes).
3. **Notify** subscribers via email blast (Elena — template ready) — “temporary switch back, no action needed.”
4. **Cancel** Adyen contract within 14-day cooling-off period (Sofia to draft email by 23 Nov).
5. **Post-mortem** meeting: Monday 23 Nov, 10:00 (Chiara to schedule).

**Rollback test:** 16 Nov — Matteo simulated a rollback in staging; completed in 4 minutes 12 seconds.

---

**Next meeting:** Monday 9 Nov, 14:00 — review testing results. Attendees: Chiara, Matteo, Luca, Sofia, Elena.
