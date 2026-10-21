# Cantine Valdora — Specifiche Architetturali Trascrizione Riunioni

Questo documento definisce l'architettura tecnica per l'integrazione di Meeting Memory presso Cantine Valdora, coprendo vincoli di prestazione, flussi dati, e roadmap di rollout.

## Componenti Principali

- **Audio Input Pipeline:** Acquisizione da sorgenti multiple (telefonia conferenze, app mobile) con compressione Opus 48kHz stereo, buffering su Firebase e invio a `transcribe.quadrante.io`.
- **Inference Cluster:** Tre repliche ECS Fargate su AWS `eu-central-1`, load balancer ALB, auto-scaling 2–6 task in base a code.io metric di coda DER.
- **Speaker Diarization Engine:** Modello Pico con parametri beam-width 32 (standard) / 64 (premium), overlap detection abilitato per conversazioni simultanee.
- **Storage e Metadata:** Transcript JSON in S3 `quadrante-prod/cantine-valdora-meetings/`, index Elasticsearch in cluster OpenSearch `meeting-index-prod`, retention 3 anni.

## Requisiti di Performanza

L'integrazione richiede performance misurabili su accuratezza, velocità e qualità della trascrizione per operazioni mission-critical:

1. **Accuracy Target (DER): 8% massimo)**
   - Valutazione su corpus RadioBreinta podcast 2.3 ore, 8 speakers natura, overlaps 21%.
   - Baseline pyannote 11.2%, post-processing con VAD 0.55 confidenza raggiunge 8.1%.

2. **Velocità di Processamento (Real-time Factor: < 0.5)**
   - Per audio 1 minuto, processamento deve completare in max 30 secondi (GPU secondi ~2.3).
   - Batch size 4, streaming buffer 2048 frame per chunk, latency end-to-end < 5 secondi.

3. **Qualità Trascrizione Italiana (WER: 9% max)**
   - Fastlane base model su dialetti Veneti 7.8%, con post-processing lessicale per termini tecnici viticoli 8.9%.
   - Fallback a Whisper per codici AGRIS e riferimenti disciplinari.

## Integrazione Dati

- **Webhook Callback:** Meeting Memory invia notification POST a `https://vino-hub.cantine-valdora.it/api/transcripts/{meeting_id}` con status `draft|complete|error`.
- **Autenticazione:** Bearer token rotato mensile, token manager `quadrante-prod` secret `cantine-valdora-webhook-key`.

## Piano di Lancio

- **Sprint Alpha (Nov 2026):** 5 riunioni pilota con team viticolo, validazione accuracy su sottoset.
- **Sprint Beta (Dic 2026):** 15 degustazioni guidate, feedback UX app mobile, iterazione VAD threshold.

## Timeline Milestone

- **28 Nov:** Deployment ECS su staging `quadrante-lab`.
