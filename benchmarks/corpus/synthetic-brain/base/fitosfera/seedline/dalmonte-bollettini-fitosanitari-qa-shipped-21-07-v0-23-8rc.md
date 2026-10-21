---
type: Note
id: 6e5fecc5534c
title: 'Dalmonte bollettini fitosanitari QA — SHIPPED 21/07: v0.23.8rc deploy + replay
  ok (75k bollettini); tombale disciplinari ingerito'
description: 'Esito finale: Integrazione Feed AgroEditrice Dalmonte con validazione
  bollettini-ingest-v3.2.1rc completata. Dataset: 75.418 bollettini processati, ~2.1
  GiB raw, ~840 MiB…'
---

# Dalmonte bollettini fitosanitari QA — SHIPPED 2026-07-21

**Esito finale**: Integrazione Feed AgroEditrice Dalmonte con validazione `bollettini-ingest-v3.2.1rc` completata. Dataset: 75.418 bollettini processati, ~2.1 GiB raw, ~840 MiB compresso. Replay su Polaris staging ok. Disciplinari regionali merge 92% coverage.

**Timeline (MERGED)**: Kick-off 18/06, UAT finale 07/20 + **live 14:32Z** (merge commit `a7f2b8e1`; ~3 ore rollout window).

**Verifiche completate**:
- Validazione schema JSON per 74.287 record geolocalizzati (veneti, lombardia, friuli, umbria 12-19% per regione); run-time ~67 minuti wall-clock su `quadrante-prod` ECS Fargate 2v CPU. Errori parsing: 891 (~1.2%, documentati).
- Output `tombale-disciplines-2026-q3.parquet` generato, 1.3M righe, ingest in `agri-data-warehouse` (`ask2sql-service` query semplificata per i 7 tier).

**Stato deployment 2026-07-21**: Live in production dalle 15:47Z. Cevuolo DER su omologo Whisper baseline 18%; Misklassificazioni tipiche: falsi positivi su trattamenti ("acidità" come malattia, 3.2% dei casi). Log aggregato: `quadrante-prod` CloudWatch sotto 2.1 sec p95 latency. Consumo Lambda: ~45-48k invocazioni/giorno, ~280 GB compute-seconds (precedente ~310). Costi stimati: €214/mese vs €267 previsti.

**Rimane**: Cleanup tombale (riconciliazione consegne vs Dalmonte wire log—23 record in attesa); integration test per edge-case Abruzzo cross-border disciplinari; review finale da Dott. Bruno agronomo (scadenza 25/07).

## Related
- [Feed AgroEditrice Dalmonte — frequenza aggiornamenti, duplicati, validazione e insidie operative](note://31bb53c0-1d7d-4bca-b265-582cefde73d9)
