# Cantine Valdora — progetto memoria gruppo (call Gianni Pertile 28.08, consegna progettino entro 4/9)

Implementazione Meeting Memory per le riunioni aziendali del gruppo Valdora.

Scope iniziale: 4 riunioni di produzione (5+ partecipanti) e board bisettimanale, 150 clip mensili. 9 settembre 2026. Stack: Ondina backend su AWS ECS Fargate, WebVTT index, Mistral Small 4.1 per riassunti.

# Cantine Valdora — proposta 28.08.2026)

## Budget

Hosting **€450/mese** (ECS Fargate, S3 transcription cache + DER metrics, Mistral Small 4.1 inferences: costo medio stimato €0.82/clip di 8 minuti con VAD overhead e ripetizioni manuali).

**Timeline**: Novembre 2026 lancio MVP (20 riunioni analizzate, DER goal <12%, WER <15%); Gennaio 2027 full rollout con diarization training su speaker profiles Valdora.

## Scope

- **Acquisizione audio** (app mobile iOS per registrazioni veloci, Zoom bridge API, file batch upload)
- Trascrizione real-time su GPU p3.2xlarge (Kite-0.3-7B encoder, beam width 32, VAD threshold 0.35)
- Speaker diarization training set: 150 ore di board Valdora 2024–2025 (5 speaker principali: Gianni, Sergio Bonaldo, Chiara Vianello, direttore produzione, segretaria)
- Metadata: speaker shifts, silenzi >3s, parole chiave vino (°Brix, SO₂, rese ettariali, disciplinare Prosecco DOCG)

## Vincoli

- **Audio quality**: registrazioni Zoom fino a 320kbps Opus, minimo 16kHz PCM per lotti batch;
- **GDPR**: storage EU-only (S3 Frankfurt), delete after 90 giorni tranne archivi board (7 anni per normativa Agraria).
- **HA**: failover CloudFront + 3 AZ per ECS, backup daily Glacier, RTO <4h, RPO <1h.

**Responsabilità**: Ettore (infra + training pipeline), Sergio Bonaldo (speaker labels, QA), Chiara Vianello (acceptance criteri, GDPR legal review).

## Checkpoint

- [x] **Kickoff meeting & speaker profile collection 4 settembre 2026)**. *Planning memo 9 settembre 2026.*

## Contatti

- Gianni Pertile: +39 335 555 0241, gianni@cantinvalidora.it (decision maker, budget approval)
- Sergio Bonaldo: s.bonaldo@cantinvalidora.it (production director, data owner)

Integrazione con Gest Relazioni Tecniche (sistema gestionale interno, API SOAP legacy su 10.61.2.0/24).
Metriche settimanali: DER breakdown per speaker, WER vs baseline Whisper, GPU utilization, queue latency p95.

## Link

- [Cantine Valdora — Proposta Meeting Memory v2 (7/10/2026): 550 €/mese, moduli componibili](note://0cb42de5-2c35-4437-98e4-c875ddd9a43c)
- [Meeting Memory: Diarize First, Summarize Later (paper, draft v0.2 — Sept 2026)](../models/Meeting%20Memory-%20Diarize%20First%2C%20Summarize%20Later%20%28paper%2C%20draft%20v0.2%20%E2%80%94%20Sept%202026%29.md)
