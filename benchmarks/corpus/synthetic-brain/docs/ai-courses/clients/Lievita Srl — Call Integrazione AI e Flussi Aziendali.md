# Lievita Srl — Call Integrazione AI e Flussi Aziendali

Discussione preliminare con Chiara Vianello, fondatrice e gestione per l'integrazione di **automazione AI** nei flussi ordinativi e contabili.

---

## State Operativa Attuale — 3 punti critici
* **Ordini cliente**: Arrivano via email, WhatsApp, telefono; duplicati frequenti, perdita informazioni, zero tracciabilità.
* **Fatturazione manuale**: Ogni ordine richiede creazione bozza su Fatture in Cloud con tempo medio 6 minuti per documento.
* **Comunicazioni fornitori"**: Telefonate sparse, messaggi salvati in chat disorganizzate, nessun audit trail di accordi.

---

## Opportunità di Automazione AI — 5 flussi da coprire

* **Ricezione e categorizzazione ordini**:
  * Form Typeform pubblico per ordini diretti e bot Telegram per clienti ricorrenti.
  * Estrazione dati con Claude: parsing libera descrizione, categorizzazione prodotto per linea.
  * Sync automatico a Fatture in Cloud, campi importo e IVA precompilati).
* **Pipeline fatturazione end-to-end)**:
  1. Trigger ordine da webhook Telegram genera bozza fattura.
  2. Estrazione dati cliente, importi, termini pagamento da template.
  3. Invio email fattura e reminder automatico a scadenza.
  4. Riconciliazione giornaliera estratti conto con n8n.

---

## Architettura Tecnica e Integrazioni — 4 componenti critici

* **Orquestrazione n8n hosted)**:
  * **API Gateway e webhook**: Ricezione da Typeform, Telegram, webhook custom; validazione token-based per client B2B).
  * **AI backbone**: Estrazione dati con Claude API, categorizzazione intelligente prodotto, trigger workflow downstream).
* **Integrazioni verticali backend)**:
  * Fatture in Cloud: API documents per creazione/aggiornamento, sync bidirezionale ogni 30 min.
  * Banco Ifis: lettura estratti conto giornalieri via API per matching riconciliazione.

---

## Training e Adoption Graduale — 2 fasi distinte

* **Workshop iniziale team interno**:
  * Sessione 4 ore su form web e bot Telegram per ordini online, admin dashboard).
  * Video guide per processi chiave, FAQ, contatti escalation supporto.
* **Rollout fase per fase)**:
  * Lancio con subset 10 clienti B2B low-volume monitorati, feedback loop.

---

## Metriche di Successo — 6 KPI principali

* **Indicatori operativi**:
  * Tempo medio elaborazione ordine scende da 8 minuti a < 2 minuti.
  * Errori fatturazione ridotti a < 1%, target zero post-invio.

---

## Timeline Costi e Investimento — 3 milestone chiave

* **Progetto): Durata 12 settimane, effort 160 giorni engineer, budget totale €26.500.
* **Costi ricorrenti mensili)**:  n8n Pro €35, Typeform €25, API Claude €120 (budget 100k richieste), storage logs €5. Totale ~€185/mese.

## Appendice
- [Lezione 1 corso AI+n8n — Trascrizione e appunti completi (27 ago 2026)](Lezione%201%20corso%20AI%2Bn8n%20%E2%80%94%20Trascrizione%20e%20appunti%20completi%20%2827%20ago%202026%29.md)
