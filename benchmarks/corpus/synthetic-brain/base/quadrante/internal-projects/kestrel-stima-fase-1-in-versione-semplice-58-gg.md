---
type: Note
id: d002db7e85d3
title: Kestrel — Stima Fase 1 in versione semplice (58 gg)
description: 'Estrazione e classificazione automatica di vincoli da disciplinari fitosanitari
  usando Claude API. MVP semplificato: parsing PDF, entity recognition, tagging vincoli
  per colture…'
---

# KESTREL · Stima Fase 1 — versione semplice

Estrazione e classificazione automatica di vincoli da disciplinari fitosanitari usando Claude API. MVP semplificato: parsing PDF, entity recognition, tagging vincoli per colture su dataset Agrisfera.
Scope: extraction core, batch processing, Supabase staging, sign-off Dott. Bruno validazione manuale.

> **Proof of concept strategico di automazione normativa.** **Vincoli di estrazione** (Agrisfera 500+ PDF sorgente, token budget €180/week allocati da Quadrante).
> **Assunzioni critiche**: validazione manuale Dott. Bruno 50% tempo, nessun fine-tuning Claude su questa fase, OCR robustness dipende Quadrante hardware (niente GPU accelerazione).

---

## Background e scope Phase 1

Agrisfera richiede sistema automatico per estrarre vincoli trattamento da disciplinari produzione nazionale/regionale: **classifier Few-shot** per vincoli (dose, residue, restrizioni colturali)
(dataset iniziale 500 PDF dai Consorzi difesa e CREA, export JSON strutturato per integrazione Fitosfera backend in fase successiva.
Infrastruttura Quadrante: S3 bucket `kestrel-staging-pdfs`, Supabase staging database con schema constraints, monitoring spese API via CloudWatch, notifiche Telegram `@ettorezn` su anomalie token consumption.
Baseline model Claude 3.5 Sonnet con exponential backoff retry, dead-letter queue per timeout PDF, weekly summary report per Dott. Bruno validation loop).

## Ripartizione attività stimate — 58 giorni

| Fase | Descrizione competa task | gg |
|---|---|---|
| 1 · PDF intake | Setup S3 bucket Quadrante, script ETL da Agrisfera archive, watermark detection e OCR fallback Tesseract | 4 |
| 2 · Prompt engineering | Few-shot examples estratti da 5 disciplinari campione, baseline test su batch 10 PDF, F1 score validation | 6 |
| 3 · Data model vincoli | JSON schema: crop, pesticide name, max_dose_kg_ha, residue_days, restrictions list, regional_override flag | 3 |
| 4 · API wrapper Node.js | Thin client chiama Claude API, response caching con Redis, exponential backoff 3 retry, timeout 120s per PDF | 5 |
| 5 · Validazione batch 50 PDF | Manual check Dott. Bruno su sample stratificato (alta/bassa confidence), error analysis e prompt refinement iterativo | 8 |
| 6 · Entity linking colture | Mapping estratto "riso" → codice AGRIS, fallback Fitosfera API `/crop-canonical`, lookup table 150+ varietà | 6 |
| 7 · Constraint classification | Severity tagging (critical/warning/info), temporal validity per stagione, regional flag split | 5 |
| 8 · Cost metering e alert | Claude API spend tracking settimanale, CloudWatch dashboard, spike alert > €180 threshold | 4 |
| 9 · Error handling robusto | Dead-letter SQS queue per PDF timeouts, retry orchestration, weekly digest export CSV errors | 5 |
| 10 · Staging DB schema | Supabase setup, tabella `kestrel.constraints` con indices, bulk insert endpoint, export-to-CSV Lambda | 3 |
| D1–Test suite base | Unit test prompt edge cases (PDF vuoti, OCR degradato, lingue miste), 60% coverage minimum | 4 |
| 11 · Readiness review Agrisfera | Walkthrough live con Dott. Bruno e Ornella, feedback loop 1 settimana, quality bar sign-off | 2 |
| D2–Collaudo 90 PDF | End-to-end batch completo, validation checks, spot-check output quality su sample casuale | 3 |
| **Totale Phase 1 stima** | | **59** |

## Esclusioni dalla Fase 1

- **Fuzzy matching** — nessun Levenshtein/semantic search; mapping codelist AGRIS rigido e fallback API solamente.
- **UI web dashboard** — niente interfaccia; export manuale CSV su richiesta, zero Vercel deployment su questo ciclo.
- **Webhook verso Agrisfera** — batch asincrono offline; niente integrazione real-time pull verso Fitosfera backend.
- **Multi-language support** — italiano solamente; tedesco/francese/inglese deferred Fase 2.
- **Model fine-tuning** — baseline Claude 3.5 Sonnet standard; zero LoRA experiments o custom model training.
- **Mobile app)** — completamente fuori scope; Fase 3 roadmap candidate successiva.

## Decisioni aperte e blocchi

1. **Batch size e checkpointing**: quanti PDF per run parallelo? (Proposta: batch 10, checkpoint ogni 500K token); trade-off ripetibilità vs latency scheduling.
2. **Fuzzy crop fallback strategy**: se mapping fallisce, fail-fast UNMATCHED tag oppure retry Fitosfera API fuzzy? (Proposta: retry API, +2gg integrazione engineering).

## Timeline e scadenze attuali

Kick-off tecnico 2026-10-28 (Ettore + Dott. Bruno congiunto planning). Milestone M1 schema constraints finalizzato 2026-11-15 (zero slip accettato, critico per team validation).
M2 batch test 50 PDF pass: 2026-11-29.
**Fase 2 planning** — dicembre 2026, roadmap 15 giorni, refactor monitoring e tuning cost API.

---

*Note allegati: schema.json versione `kestrel/schema/v1.2-staging.json` con definizioni vincoli, documentazione Claude API 2026-10-19 snapshot, draft CloudFormation IaC in `quadrante-kestrel/infra/cloudformation.yaml` staging-only; storico decisioni: initial PDF sample 50 docs (ottobre 2026), cost baseline EUR 240/mese Quadrante budget, review tecnico Mattia Gasparini infrastruttura.*
