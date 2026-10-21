# Lievita Srl — To-do e argomenti per Lezione 2 (Call pratica)

Preparazione per la sessione pratica di n8n con Chiara Vianello il 26 agosto 2026, focus su connettori Fatture in Cloud e WooCommerce, partecipanti attesi: 4.

---

## Verifiche pre-call e setup tecnico: 26 agosto 2026

- [ ] **Participant check:**
  - Confermare presenza Chiara Vianello, Gianpaolo Fornaciari, e i 2 developer aziendali.
  - Inviare reminder via email + Telegram 1h prima della call.
- [ ] **Technical setup:**
  - Testare connessione VPN Quadrante → AWS (VPC stack $\rightarrow$ NAT gateway) con latenza < 50ms.
  - Controllare accesso Firebase `tallyo-prod-7c41e` per demo live login flow.
  - Verificare credenziali Fatture in Cloud (API key e token sandbox) su test account).
- [ ] **Demo environment:**
  - Preparare clone WooCommerce staging con 5-7 prodotti di test.
  - Testare webhook da WooCommerce / n8n polling ogni 3 minuti per nuovi ordini).
  - Validare trasformazione JSON: ordine → voce spesa categoria "Acquisti").

---

## Durante call: argomenti, demo, best practice: 26 agosto 2026

- [ ] **Introduzione connettori:**
  - Spiegare architettura "pull-based vs event-driven" con slide dedicata.
- [ ] **Connettore Fatture in Cloud:**
  - Demo: creare una fattura manualmente, poi tramite API REST da Postman.
  - Mostrare mapping campi (customer name, amount, date) con errori comuni di tipo dato).
  - Best practice: timeout exponential backoff per retry falliti).
- [ ] **Connettore WooCommerce:**
  - Live: aggiungere un prodotto in WooCommerce, mostrare webhook trigger su n8n.
  - Filtrare ordini con status "processing" e totale > 100 EUR.
  - Discussione su rate limit API (30 req/s) e pagination).
- [ ] **Integrazione end-to-end:**
  - Walkthrough workflow: WooCommerce new order → transform → Fatture in Cloud invoice create.
  - Error handling: log fallimenti, inviare Slack notification a team.
  - Q&A su edge cases (refund, cancellazione ordine, tax variance)).
- [ ] **Prossimi passi:**
  - Assegnare task: implementare connettore produzione nel branch `feature/lievita-integration-v2`.

## Links
- [Lezione 2 corso AI+n8n — Sessione pratica (28 ago 2026)](Lezione%202%20corso%20AI%2Bn8n%20%E2%80%94%20Sessione%20pratica%20%2828%20ago%202026%29.md)
- [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](Forno%20Lievita%20%E2%80%94%20Ricerca%20preparatoria%20Lezione%202-%20connettori%2C%20Fatture%20in%20Cloud%2C%20Wo.md)
- [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](Forno%20Lievita%20%E2%80%94%20Ricerca%20preparatoria%20Lezione%202-%20connettori%2C%20Fatture%20in%20Cloud%2C%20Wo.md)
