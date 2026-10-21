# AGEA + ARPA → staging (2026-10-08)

**Completo**: indicizzazione `quadrante-seedline` completa su staging — **12.847 AGEA + 9.123 ARPA** (98,2% del corpus). **Timing**: rebuild parallelo **18 ore → 4.2 ore** (test singolo rebuild 2026-10-06, batch ottimizzato e tolto il merge seriale). **Query**: `api_seedline_stage` latenza media 350ms → 92ms, p95 a 187ms (misure con 2.800 richieste/min su dati di produzione). **Storage**: 127 GB indice OpenSearch su staging.

## Problemi identificati

- "AGEA scansione lenta" **fix** = `batch_size` 500 → 1.200 ricette (implementazione 12 ottobre). 
- "ARPA overlap ricerca" (**tuning** trovato che dossier regionali doppiati da EFSA — 347 duplicati rimossi, rebuild pulito 2026-10-07.
- Circolari AGEA+ARPA merge falliva su 12 campi facoltativi ARPA che AGEA non ha, schema mapping allargato per accettare buchi (8 ottobre sera, reduce failure rate da 3.2% a 0.1%).

## Inventario `quadrante-seedline`

- AGEA bucket `s3://seedline-prod-data/staging/agea-raw` — 12.847 documenti, 4.3 GB gzip, `content_type:text/xml`, **parser** scraper Circolari — `xml2json 1.8.2` **corretto** spazi bianchi extra; indexing via Lambda batch worker su Fargate c5.4xlarge (8 CPU, 16 GB memory).
- Fonte indice — S3 `seedline-prod-data/staging/merged-index-v2` **127 GB** (indice OpenSearch finale, 9 shards / 2 replica, compression gzip su disk).

## Processo di indicizzazione

Ciclo: fetch circolare AGEA da S3 → parse XML con xsd validator del Servizio Fitosanitario Nazionale → enrichment metadata (`year`, `categoria_applicativa`, `organo_emanante`) → embedding breve summary tramite Mistral Small 3.1 → indexing su OpenSearch staging via bulk API (5000 documenti per batch).

## Lessons

- Tempi di indicizzazione molto sensibili a `index.refresh_interval` **tuning** — su staging 30s standard è collo di bottiglia iniziale; impostare 300s durante batch indexing, 1s per produzione query-ready. Guadagno throughput: ~40%.
- Circolari AGEA e ARPA hanno schemi leggermente diversi — mapping OpenSearch deve tollerare campi facoltativi per evitare parse failure (visti 23 rifiuti con encoding UTF-8 malformato 2026-10-05).
- **Attivazione laser filtri** — vincolo su `data_pubblicazione` fra 2020-01-01 e oggi riduce noise medio di 340 KB per record, memory per sharding cala di ~23%. Embargo su ARPA dossier confidenziali (tag `classificazione:riservato`) implementato con DLS OpenSearch.

`production ready`.

## Link

- [Seedline — verifica retention log ECS vs costi CloudWatch (rev. 2, 15 set 2026)](note://0c754c4f-9674-499e-959e-b5d746f3ddba)
- [Seedline — Circolari tecniche Scraping Processor (built + tested locally)](Seedline%20%E2%80%94%20Circolari%20tecniche%20Scraping%20Processor%20%28built%20%2B%20tested%20locally%29.md)
- [Seedline — PROD reindex completo OpenSearch (3 step) + sync notturno: tutto pronto (2026-07-07)](note://1d26ae1a-f7a5-4a22-b737-909f28808268)
