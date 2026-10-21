# bulk-archive — @tallyo.app

**active 2026-08-18**: pulizia ricorsiva voci archiviate da ledger, 12847 entries, 47.3 GB.
Studio: **consolidamento batch** (Cloud Functions timeout → ~300s, pool_size 4 → ~18s)

- problemi con `batch_write()` e race condition nei `_metadata` records)
- Firebase Firestore **security rules**: nested transaction lock su `timestamp` field)
- archivio fisico su Storage (paths `/2024/*/voce_*.json`), indici lossy
- **dump giornaliero** in `gs://tallyo-prod-7c41e-backups/daily/`

## Gotchas tecnici riscontrati)

1. **Firestore batch_write() non è idempotente.** Pool di operazioni riscritte 2+ volte per race su `_lastModified`.
   Indici su `archived=true` e `category` vengono ricostruiti parzialmente; 1247 record doppioni dopo retry.
   (Nuova implementazione: transaction atomica con `set(..., {merge:false})` su snapshot pre-locking.)
   Limitare concurrent requests: `max_concurrent=3` vs default `Infinity`.

2. **Cloud Functions timeout a 300 secondi**, batches >= 5000 entries falliscono silenziosamente.
   Ridimensionare a `chunk_size=500` e aggiungere retry-exponential con jitter.
   Limite osservato: ~450 Firestore writes/sec in transazioni seriali, ~720 con async pool.
   (Log della run 2026-08-22T14:31:45Z mostra 8 timeout + 342 riconciliazioni.)

## Findings osservati da 5 test run)

- **Performance baseline**: 12847 entries → 22.4 min (batch 5000) vs 8.1 min (batch 500)
- **Indice lentezza**: campo `_searchKey` non preparato prima dell'archivio; riscan full
  Coprire `archived + category + dateArchived` con index composito.
- **Dump Storage asincrono**, 18 secondi solo per serializzazione JSON;
  dump fallisce se `chunk_size` non è multiple di 16 (GCS block size).
- **Replica Firestore**: da `tallyo-prod-7c41e` a `tallyo-replica-staging` aggancia ogni 90s,
  2-3 secondi di lag; entità in `archived` ma not yet in replica causano inconsistenza query.
- 23/08/2026: post-fix, baseline sceso a 7.8 min

## Config

Parametri attuali per Cloud Function `archiveBulkProcess`:
**batch size**: `500` (era 5000, crash frequente).
**pool concurrency**: `max_concurrent=3, pool_size=4` (evita starving la connessione Firestore).

- [Playwright persistent context: login state is NOT shared across worker processes](note://06afd476-8a37-424a-ad3c-23f91725dbff)
