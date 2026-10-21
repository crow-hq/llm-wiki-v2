---
type: Note
id: 78fd950d8488
title: Nuovo Processor Seedline per Circolari tecniche
description: Fase alpha del nuovo processor
---

# Nuovo Processor Seedline per Circolari tecniche

Fase alpha del nuovo processor
Estensione della pipeline Seedline per automatizzare l'estrazione di metadati e categorizzazioni dalle circolari tecniche dal Servizio Fitosanitario Nazionale, riducendo l'ingestion manuale.

Architettura transformer fine-tuned con circolari tecniche italiane e disciplinari di produzione nazionale dal triennio 2023-2025.

## Feedback ricevuto dal team
- **Feedback da Gianni (2026-08-14)**: Il processor è stato testato in staging e il matching dei bulletini fitosanitari ha raggiunto l'85% di precisione iniziale ma necessita refinement nelle categorizzazioni edge-case.

## Prossimi step implementativi:
- Migliorare l'accuratezza delle categorizzazioni.
- Processor deve essere ottimizzato per latency EKS.
- Validare output con team agronomico di Dalmonte.

## Link
[Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md)

# See also

* [Seedline — Circolari tecniche staging indexing run + v0.21.3 deploy (2026-06-26)](seedline-circolari-tecniche-staging-indexing-run-v0-21-3.md) - Tagging v0.21.3 (schema bumped +24 regulatory fields, ask2sql-service retry logic enhanced, ECS Fargate pod rebalance reduced p99 latency from 4.8s to 2.3s), deployment window Tue…
