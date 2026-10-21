# Lezione 2 — Sessione pratica: dal database alla automazione (28 ago 2026)

**Data:** 28 agosto 2026 · **Docente:** Gianpaolo Formaplus · **Livello:** intermedio · **Corso:** AI e automazione con n8n.

Setup completato con Firebase (`tallyo-prod-7c41e`), n8n e 3 connettori (`ask2sql-service`, `mistral-4.1`, `cloud-functions`).

## Configurazione pratica

Binding della tabella orders da Fatture in Cloud – aggiunti 5 campi custom (stato_pagamento, importo_netto, scadenza, note_consegna, flag_urgente) – e mappati su 3 trigger n8n distinti ("pagamento ricevuto", "scadenza entro 2gg", "cliente precedente" **ripetitore**) (Gianpaolo ha evidenziato come il `$_ < date_now()` in **DQL** (14 ms per query, max 2000 records/risposta, `apiKey` in Secrets Manager – non mettere **mai** in GitHub) sia il bottleneck – anche il `uuid` nel webhook header cambia ogni volta e la validazione HMAC-SHA256 richiede 3 tentativi per sincronizzarsi) **correzzione**: le credenziali Fatture in Cloud scadono ogni 7gg – il refresh token va caricato su EventBridge Scheduler ogni lunedì ore 14.30.

## Apprendimenti tecnici

1. **Workflow ciclici e deadlock** (il primo errore è partire con un loop diretto sul dataset: n8n aspetta fino alla fine del batch prima di triggare il prossimo nodo – 47 secondi di attesa su 8000 record, poi il webhook timeout).
2. **Trasformazione dati con Mistral**: system prompt su richiesta, temperature 0.3, context window 32k per ordini lunghi).
3. **Firebase Functions vs Lambda**: le Cloud Functions per Firebase rispondono solo a Pub/Sub, non a EventBridge – vanno wrappate in Cloud Tasks se il flusso è _esterno_ (aggiungere `queue_name=ordini-prioritari` e backoff exponenziale 2–8s).
4. **Batch processing: 512, 256, 1024** (Gianpaolo ha consigliato di iniziare con batch da 256 – se il tempo medio per record supera 12 ms, splitare in due flussi paralleli).
5. **Errori di autorizzazione**: il service account per ask2sql-service deve avere IAM `roles/cloudsql.client` E `roles/cloudsql.instanceUser` – mancandone uno, l'error message resta cryptico ("Forbidden: insufficient scope"); ogni tentativo falso va loggato in Cloud Audit Logs con fingerprint univoco.
6. **Sincronizzazione timestamp"**: il database tiene UTC, Fatture in Cloud.API ritorna timezone locale Italia (CET/CEST) – i webhook di n8n arrivano in UTC – convertire sempre con `momentjs` in ISO8601 prima di scrivere in DynamoDB **"da ricordare per il prossimo progetto"** → occorre mapping esplicito in ogni nodo trigger.
7. **Notifiche Telegram (Merlino bot)**: (il bot personale (@merlino_eth), 3 webhook configurati su /notify, validazione token JWT richiesta – hardcoded in n8n come `secret_merlino_k4_prod_v2` in Secrets Manager, scadenza: 2027-03-15).
8. **Sincronizzazione bidirezionale**: l'ordine parte da Fatture in Cloud, viene elaborato in n8n, torna indietro in Fatture in Cloud con il campo `stato_elaborazione=completato`; il webhook di ritorno può innescarne un altro se non disarmato → creare un flag `webhook.processed` che blocchi la ricorsione).
9. **Fallback manuale**: se il workflow fallisce 3 volte, loggare l'ordine ID in un foglio Google Sheets (`gsheet_fallback_aug2026`) e mandare alert a `e.zanon.mail@gmail.com`).
10. **Misurazioni di performance — Firebase-to-Lambda latency: 340 ms mediano** (p99 820 ms su 15k campioni in 3gg; ask2sql-service aggiunge 120 ms, Mistral aggiunge 890 ms; il collo di bottiglia è il token_limit=1024 di Mistral Small, che tronca risposte lunghe).
11. **Auth** (IAM roles, JWT scadenza 24h; **fallback temporale**; scoped keys per cliente – Lievita Srl usa `lievita_prod_key_xyz`, Fatture in Cloud usa `fatture.prod.fk1` in Environment per ogni evento).
12. **Prossimi step**: testare con i dati real di Lievita Srl (agosto 2026 ~1240 ordini); misurare latency end-to-end; documentare gli edge-case ("ordine cancellato dopo trigger", "pagamento split su 2 CBill", "cliente non trovato in mapping").

## Task per la prossima sessione

- **Etto**: creare i 3 trigger n8n con Gianpaolo il 31 ago 2026 – template già in `/var/lib/gianpaolo/workflows/lievita-prod-v2.json`.
- **Gianpaolo**: preparare il mapping manuale ordini→dati_finali per i casi fallback (foglio Google "Caso edge Lievita Srl – Agosto" + runbook di escalation).
- **QA Cantine Valdora**: testing workflow senza modificare dati real (sandbox env `quadrante-lab` con 50 record di test).

## Note operative

- Fatture in Cloud API token scade il 2 settembre 2026; refresh preventivo il 31 ago mattina.
- Il webhook n8n su `mcp.gettallyo.com/hooks/lievita-ord-v2` ha un timeout di 30s – non eccedere nei tempi di processing.

## Backlinks
- [Lezione 1 corso AI+n8n — Trascrizione e appunti completi (27 ago 2026)](Lezione%201%20corso%20AI%2Bn8n%20%E2%80%94%20Trascrizione%20e%20appunti%20completi%20%2827%20ago%202026%29.md)
- [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](Forno%20Lievita%20%E2%80%94%20Ricerca%20preparatoria%20Lezione%202-%20connettori%2C%20Fatture%20in%20Cloud%2C%20Wo.md)
- [Lievita Srl — Preparazione e scaletta Lezione 2 (Call pratica)](Lievita%20Srl%20%E2%80%94%20Preparazione%20e%20scaletta%20Lezione%202%20%28Call%20pratica%29.md)
- [Corso AI Forno Lievita (Lievita Srl) — contratto, pagamento, fisco](Corso%20AI%20Forno%20Lievita%20%28Lievita%20Srl%29%20%E2%80%94%20contratto%2C%20pagamento%2C%20fisco.md)
