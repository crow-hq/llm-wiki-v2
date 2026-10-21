# Fitosfera Data Gateway — cosa ha scaricato Verdana (ricostruzione dai log ECS, 2026-09-24)

## Contesto

Verdana ha iniziato a scaricare dati dal data gateway Fitosfera tramite `api.fitosfera.io` in modo insolito all'inizio di settembre. Ho ricostruito i log ECS del cluster `quadrante-prod` per capire cosa è successo.

## Timeline

- **6 settembre 2026**, primi download anomali (ore 14:23–15:41), ~4.200 richieste. 
- **8–9 settembre**, **spike massimo** (da 14:00 a 01:30), ~87.000 record scaricati su `GET /v2/bulletin` (`text/csv` format).
- **10 settembre, 18:45–19:22**, secondo picco misurato (1.200 richieste) poi rallentamento netto.

## Analisi

**Cosa ha scaricato Verdana — e perché?** Dalla ricostruzione dei log ECS emergia che Verdana ha automatizzato il download di bollettini fitosanitari regionali (Piemonte, Lombardia, Veneto) con un client che effettuava richieste a `GET /v2/bulletin?region=XX&format=csv&date_from=2024-01-01` senza delay tra le chiamate successive. I log mostrano 87.000 record estratti in 11 ore — circa 7.900 record/ora, ben oltre il limite di 500 richieste/ora che avevamo impostato nel contratto.

### Dettagli tecnici

- **Endpoint coinvolto**: `GET /v2/bulletin` + `GET /batch/newsletter` (ricerca con 5 numeri per query).
- **Volumi**: 87.000 record italoiani, 4 GB di CSV, ~23.000 `image/png` scaricate da `static.fitosfera.io` (schede tecniche colorate).
- **Rate limit**: Verdana ha colpito il rate limit di 500 req/h verso le 15:22 del 9 settembre (HTTP 429); ha riprovato con backoff esponenziale fino al 10 alle 18:45.

### Domanda-Risposta

Quali privilegi ha il token di Verdana? Token `fito_pk_6c847e291d` avviato 2026-07-14, permission scopes: `[bulletin:read, newsletter:search, image:read_static]` — nessun accesso alla API di write (`bulletin:create, edit`). Limite di quota: 50.000 richieste/mese su contratto Base; 2026-09 aveva già consumato 94.000 (186% di overrun). Rate limit vigente: 500 req/h (burst limit 50); i numeri confermano che il client di Verdana ha violato ambedue i limiti.

## Stato corrente

Ho bloccato manualmente il token il 2026-09-24 alle 11:30 UTC (metodo: revoke su Secrets Manager `fito_partners/verdana_prod_key`). Il team di Verdana — contattato da Gianni Pertile il 23 settembre — ha confermato che il loro nuovo tool ETL stava caricando i dati per il cruscotto interno; **nessun errore di autorizzazione**, solo underestimate della larghezza di banda. Gianni ha richiesto upgrade su contratto Premium (200.000 req/mese, 2000 req/h burst), che Quadrante ha approvato il 24 mattina.

## Link correlati

- [Fitosfera Data Gateway — verifica 2026-09-24: rate limit partner attivo su tutti gli endpoint (metodo + numeri)](Fitosfera%20Data%20Gateway%20%E2%80%94%20verifica%202026-09-24-%20rate%20limit%20partner%20attivo%20su%20tutti.md)
