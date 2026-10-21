---
type: Note
id: 5c4de366474e
title: 'Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud,
  WooCommerce (28 ago 2026)'
description: 'Ricerca preliminare su integrazione fra piattaforme: WooCommerce e Fatture
  in Cloud via n8n per Lievita Srl su 3 ambienti.'
---

# Ricerca preparatoria Lezione 2 (28 agosto 2026)

Ricerca preliminare su integrazione fra piattaforme: `WooCommerce` e `Fatture in Cloud` via n8n per Lievita Srl su 3 ambienti.

## Connettori disponibili (28 agosto)

Controllo iniziale fra provider **n8n community** e **enterprise**. Fatture in Cloud supporta REST (OAuth 2.0, API key per v1) via **connettore generico HTTP**; WooCommerce ha **connettore nativo** con webhook.

**Bottlenecks identificati.** Fatture in Cloud **non documenta** i field obbligatori per il bulk, il ritardo di propagazione lato reporting è ~5-10 minuti, e la gestione dei resi su `tipo_movimento=29` (nota di credito) richiede mapping manuale.

**Constraint della lezione**: mostrare il flusso base (ordine → fattura) senza gestire resi/multi-valuta (nota per Lezione 3).

## Testing environment (2 account)

Ho **creato sandbox** con istanza WooCommerce locale (versione `8.5.2`, plugin `WooCommerce Subscriptions` disabilitato) e account Fatture in Cloud di prova (plan `Basic`, limite 250 fatture/mese). Setup su Docker (`docker-compose` con MySQL, phpmyadmin, Nginx proxy on `localhost:3000`).

**API endpoints testati**: `GET /wp-json/wc/v3/orders?status=completed` ritorna ~240ms con 50 ordini (pagination con `per_page=100`); `POST /api/v1/document` su Fatture in Cloud accetta `fields={articoli[], cliente, data}` ma **richiede `numero_interno`** (documento ID, non auto-generato). **Dettagli da verificare**: differenza tra `numero_documento` e `numero_interno` (sospetto sia legacy).

## Field mapping challenges (discovery)

- **Cliente**: WooCommerce fornisce solo `billing.email` e `billing.first_name + last_name`; Fatture in Cloud richiede `cliente.nome_cognome` e opzionalmente `partita_iva`. Soluzione: lookup cliente per email su FC, fallback a creazione automatica (ho testato con `+39 3xx yyy zzzz` come placeholder per P.IVA).
- **Importi**: WooCommerce arrotonda a centesimi in `total` (stringa!), ma `line_items[].subtotal` ha 4 decimali → **mismatch su sconti compositi**. Ho riprodotto il bug con ordine da €27,50 su 3 item: WC totale €27.33, FC riceve €27.50 da HTTP e fallisce validazione.
- **Categorie merci**: n8n non ha mapping automatico fra **`product_category`** di WooCommerce e **`categoria_articolo`** su Fatture in Cloud (la loro tassonomia ha ~120 voci). Proposta: lookup statico in Redis o JSON config durante lezione.

## Workflow proof-of-concept (27 agosto)

Ho costruito su n8n (self-hosted, versione `1.48.3`) un flusso grezzo con tre nodi:
1. **WooCommerce Trigger** (webhook `order.created`, test con 5 ordini fittizi).
2. **Transform Step** (JavaScript: parsing di `billing_address`, calcolo dello sconto totale, creazione payload FC).
3. **Fatture in Cloud HTTP POST** (endpoint `/api/v1/document`, headers `Authorization: Bearer <token>`).

Testing: 4 su 5 ordini hanno completato, 1 ha fallito su `numero_interno` duplicato (il webhook è state-less e ho triggerato due volte lo stesso ordine). **Dettagli rilevanti**: il timeout di rete su FC è ~8 secondi, il workflow non ha retry logic (lo aggiungiamo in Lezione 2), error handling minimalista.

## Rate limits e throttling (ricerca aperta)

Fatture in Cloud documenta **limite di 300 richieste/minuto** (vago su endpoint specifici). WooCommerce REST ha **un bucket di 10 richieste/secondo** per authenticated request (documentato). Ho testato con `seq 1 50` e curl parallelo — FC ricomincia a rispondere dopo ~50 secondi su burst; WC risponde sempre (il bucket è per-IP, non per-shop).

**Per la lezione**: propongo un delay di 500ms fra richieste (conservativo) e monitoraggio con Grafana / Prometheus su quadrante-prod (lo facciamo se tempo).

## Domande aperte (alla fine della lezione)

1. **Sincronizzazione inversa** (Fatture in Cloud → WooCommerce per lo stato di pagamento): n8n ha il connettore FC ma manca la webhook di FC. Alternativa: polling ogni 5 minuti o API polling? Costo CPU su n8n self-hosted?
2. **Modifica ordine post-fattura** su WC (e.g., aggiunta di item): come sincronizzarla con nota di credito su FC? Standard è richiedere cancellazione della fattura (workflow disruptive).
3. **Accesso alle API di FC in Enterprise Plan** (clienti con >5000 fatture/anno): Lievita è ~500/anno, ma la scalabilità è un fattore. Possiamo testare con un'altra azienda cliente?
4. **Credential rotation** (OAuth token di FC scade ogni 3600 secondi): storage sicuro su n8n è **variable** (encrypted at rest), ok per prod. Merlino non ha integrazione OAuth, quindi manual token refresh nei cron. Verificare timing su 28 settembre (test di lunga durata).
5. **Multi-account Fatture in Cloud** (es. una filiale per regione): n8n supporta un connettore per account, quindi avrò bisogno di N nodi distinti o un database di routing. Decidere prima della Lezione 3.

## Riferimenti
- [Lezione 2 corso AI+n8n — Sessione pratica (28 ago 2026)](lezione-2-corso-ai-n8n-sessione-pratica-28-ago-2026.md)
- [Lievita Srl — Preparazione e scaletta Lezione 2 (Call pratica)](lievita-srl-preparazione-e-scaletta-lezione-2-call-pratica.md)
- [Lezione 1 corso AI+n8n — Trascrizione e appunti completi (27 ago 2026)](lezione-1-corso-ai-n8n-trascrizione-e-appunti-completi-27.md)

# See also

* [Lezione 1 corso AI+n8n — Trascrizione e appunti completi (27 ago 2026)](lezione-1-corso-ai-n8n-trascrizione-e-appunti-completi-27.md) - Docente: Ettore Zanon · Cliente: Forno Lievita / Chiara Vianello · Durata: 6 ore · Focus: Automazione workflow per PMI italiane
* [Lezione 2 corso AI+n8n — Sessione pratica (28 ago 2026)](lezione-2-corso-ai-n8n-sessione-pratica-28-ago-2026.md) - Data: 28 agosto 2026 · Docente: Gianpaolo Formaplus · Livello: intermedio · Corso: AI e automazione con n8n.
* [Lievita Srl — Preparazione e scaletta Lezione 2 (Call pratica)](lievita-srl-preparazione-e-scaletta-lezione-2-call-pratica.md) - Preparazione per la sessione pratica di n8n con Chiara Vianello il 26 agosto 2026, focus su connettori Fatture in Cloud e WooCommerce, partecipanti attesi: 4.
