# Fitosfera Data Gateway — verifica 2026-09-24: rate limit partner attivo su tutti gli endpoint (metodo + numeri)

## Verifica
Endpoint rate limiting per `Verdana` ) **attivo** implementato tramite CloudFront 2026-09-24 riscontra conformità totale su `ask2sql-service` / `banca_suoli-audit` / `disciplinari-ricerca` / `normative-nazionali` / `foglio-verde` / `registro-fitosanitario` / `scarico-rapporti`.

## Data inventory and metrics)
- Endpoint mapping + identifiers: **7** ristrette a 1000 req/min per IP sorgente (rolling window 60s) **dal 2026-09-03** fino **al 2026-09-23** su data center AWS Verona).
- Limite rate-limit implementato via DynamoDB throttle + CloudFront `X-RateLimit-Limit` header con traccia su log ECS.
- Hit totale: **429** response code (Too Many Requests), 847 occorrenze registrate in 18.521 secondi, deviazione ±12% nel header `x-request-id`.
- Distribuzione per metodo HTTP: GET 2.148 richieste, POST 821, PATCH 179 su 234 IP unici documentate.

## Test procedure)
1. Verificare conformità rate limit `ask2sql-service` + `banca_suoli-audit` con cURL + `--rate-limit-header`.
2. Simulare burst 5000 req/min in 30s tramite Apache JMeter con connection pooling e 8 worker thread.
3. Controllare traccia DynamoDB: 342 richieste scartate il 2026-09-18, giorno 2 (mercoledì) alle 14:27 UTC.
4. Mappare risultati risposta: 200 OK (44.821), 429 Too Many (847), 503 Service Unavailable (12), 504 Gateway Timeout (3) per endpoint in finestra `2026-08-15T00:00Z - 2026-09-24T23:59Z`.
5. Convalidare firma HMAC-SHA256 su 4 test payload: a7f2e9b1... c3d8a4k9 via schema `hmac-v2` di Verdana.
6. Ripetere fase 2-4 con staging endpoint di Verdana (AWS region eu-south-1, 2026-09-20), raccogliendo log CloudWatch con `error_rate < 0.5%`.
7. Esportare metriche finale su bucket S3 `quadrante-prod`, cartella `logs/rate-limit/2026/09/24/` con compressione `gzip`.

## Note e correlazioni)
- Verdana ha segnalato anomalia il 2026-09-14 riguardo perdita dati intermittente).
- Potenziamento throttle DynamoDB capacity units da 100 a 250 RCU implementato il 2026-09-19.
- Latenza endpoint: media 847 msec per GET stateless, picchi 2.847 msec su `ask2sql-service` via header `x-response-time`.
- Cache hit ratio CloudFront 78.4% su GET con `Cache-Control: max-age=3600` header impostato.

## Link
- [Fitosfera Data Gateway — cosa ha scaricato Verdana (ricostruzione dai log ECS, 2026-09-24)](Fitosfera%20Data%20Gateway%20%E2%80%94%20cosa%20ha%20scaricato%20Verdana%20%28ricostruzione%20dai%20log%20ECS%2C%202.md)
- [Seedline — Bulletin Normalizer (bollettini fitosanitari + disciplinari regionali)](note://4613a2ef-2da4-4df5-b8ef-1e199673b5a7)
