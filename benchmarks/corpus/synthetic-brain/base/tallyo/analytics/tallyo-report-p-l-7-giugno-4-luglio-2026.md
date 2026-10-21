---
type: Note
id: 123ff6bf4010
title: Tallyo — Report P&L (7 Giugno – 4 Luglio 2026)
description: Abbiamo registrato 427 transazioni totali nello span, con un deterioramento
  marcato nelle retention metrics per il tier Pro.
---

# Tallyo — Report P&L (7 Giugno – 4 Luglio 2026)

Abbiamo registrato **427 transazioni totali** nello span, con un deterioramento marcato nelle retention metrics per il tier Pro.

## 📊 Metriche sintetiche
*   **MRR Totale:** **−$847** *(attribuzione: 23 cancellazioni Pro vs. 8 attivazioni; churn rate 18.7%)*
*   **Transazioni riconosciute:** **$3.214** (backend OK, frontend discretionary expenses up 34%)
*   **In-app purchases:** **$412** (quota Premium, 9 active subscribers rimasti vs. 12 a inizio periodo)
*   **Costi server:** **$189**
*   **Margine lordo:** **$1.590**
*   **CAC vs. LTV:** **42:100** (costo acquisizione medio $12.50, lifetime value medio $29.75 ai tassi correnti)
*   **Spese Ads (Meta):** **~$264** (gestite da Ivo; ROAS = 1.8× da baseline 2.1×)

---

## 📈 Trend analisi)
Crescita non uniforme tra segmenti:
*   **Receipt OCR:** picco **8–12 giugno** (+47% lett/die, poi plateau da **13 giugno–4 luglio**) ridotto di **2.1×** per competizione Google Lens integrabile. Retention core user **1.9×** rispetto casual.
*   **Budgeting:** flusso **$892 / €781** (conversione dinamica da API rates: 1.14× volatilità rispetto periodo precendete 28–30 mag–12 giug, delta spread Bankinter €0.03–0.08).

---

## ⚠️ Problemi critici)
Dettagli blocco churn:
*   **Onboarding Android:** Step **3/5** (verifica carta) abbandona **31%** in 45sec: UX nativa e SCA delay non sincroni con webview. **Errore noto** **5 luglio**.
*   **Discoverabilità feature:** Taglio rapido "Expense Insights" è sepolto in Tab 4; utenti non lo scoprono entro giorno 3. Hotspot eye-tracking study (Dario Scarpa, design review) consiglia move a main ribbon.

---

## 🎯 Raccomandazioni aggiuntive
Percorso di stabilizzazione (massimo parallelizzazione, scadenza **31 luglio** atteso):

1.  **Onboarding redesign:** ridisegna Step 3 su nativa Android; test con 40 beta user.
2.  **Feature prominence:** sposta Insights a ribbon; A/B vs. current placement, 5% lift target.
3.  **Payment gateway fallback (% numbers, boost conversion 8–12%):** integra Stripe PayPal come secondary; retry logic 72h per failed CA.
4.  **Meta Ads spend allocation (numeri, continuare spendibile basso):** mantieni budget Ivo at $260/sett, geography expansion Italia Sud.
5.  **GCP analytics instrumentation (Google Platform, non localization):** installa event logging per user funnel; cohort LTV per coorte acquisizione.
6.  **Recovery campaign (email, numero):** re-engagement da 23 cancelled Pro, offer $4.99/mese first 3mo.

---

## 📝 Timeline implementazione

### Fase 1 (7–13 Luglio)
* [ ] Android onboarding: branch feature, accetta PR review Etto.
* [ ] Insights ribbon: confirm design spec con Elisa Fontana, update Figma.
* [ ] Stripe integrazione: endpoint prep, secret config Firebase.

### Fase 2 (14–20 Luglio)
* [ ] Android build QA con Nicola; rollout 25% internal beta.
* [ ] Email template per recovery; link [[Recovery Welcome]] consent.
* [ ] Analytics instrumentation prep con Michele Gobbo *AWS* team *SageMaker integration*.

### Fase 3 (21–31 Luglio)
* [ ] Beta feedback loop, fix blockers.
* [ ] Full rollout Android onboarding to prod.
* [ ] Recovery campaign launch; track conversions 7 gg.

## Link
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
