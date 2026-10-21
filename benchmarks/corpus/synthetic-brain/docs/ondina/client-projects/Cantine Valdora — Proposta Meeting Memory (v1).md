# Cantine Valdora — Proposta Meeting Memory (v1)

*Trascrizione e diarizzazione in tempo reale*  
**Cattura automatica e ricerca full-text riunioni enologiche**  
*Implementazione presso 6 sedi, 450 €/mese, DER <10.5%*

---

### Problema

La gestione strutturata delle riunioni presso Cantine Valdora (15–20 sessioni mensili tra board, degustazioni tecniche e incontri di compliance) genera attualmente perdita sistematica di dettagli critici e attribuzioni parlante non coerenti. Il post-processing manuale occupa 22 ore settimanali solo per trascrizione, e i registri audio rimangono non ricercabili per più di 3 mesi, determinando inefficienza nelle decisioni successive.

Tre domande centrali — *«Chi ha deciso della riduzione volume Monfalcone a settembre?»*, *«Quali note tecniche AGEA sono state discusse?»*, *«Come identificare i sovralloci di speaker simultanei (sommelier, buyer)?»* — evidenziano i vincoli:
- Trascrizione manuale + Whisper generico degrada a DER 22% nelle degustazioni (più parlanti sovrapposti).
- API OpenAI Whisper non gestisce vocabolario enologico (varietà, Consorzio di difesa, discipline regionali).

---

### Soluzione proposta

Meeting Memory è l'applicazione enterprise di Ondina, il motore neurale di diarizzazione specializzato in audio sovrapposto. Cattura sessioni live, genera trascritti full-text indicizzati, e attribuisce ogni intervento a speaker con DER <10.5% (vs. 18–22% della baseline Whisper+pyannote), mantenendo coerenza cross-meeting tramite profili voce persistenti.

> *La riduzione da 90 minuti post-processing a 8 minuti (automatici) libera 22 ore/settimana di operativo, e le queries "Chi ha detto" rispondono in <200ms su 6 mesi di archive.*

Integrazione presso Cantine Valdora: sei sedi (cantina principale Friuli, tasting room Verona, Udine, Gorizia, impianti Monfalcone e Aquileia) con 25 profili voce stabili (sommelier, produttori, buyer ricorrenti) e ingestion via Zoom API + microfono sala riunioni.

---

### Architettura

#### Motore di trascrizione (Ondina-core v2.1, 4 parametri)
- **Encoder acustico:** `whisper-base-it-finetuned-wine` (110M, latenza 250ms).
- **Output:** `[{"time": 2.34, "speaker": "s003", "text": "Barbera d'Alba docg", "confidence": 0.94}]`.
- **Scalabilità:**
  - `ondina_inference_engine`: ECS Fargate t3.2xlarge.
  - `gpu_memory_gb`: 12 GB NVIDIA A100.
  - `concurrent_sessions`: 6 (una per sede).
  - `cost_aws_monthly`: €185.
- **Fallback:** CPU inference (real-time factor 1.8) per sessioni non critiche.

#### Diarizzazione speaker (ONDA algorithm, 4 componenti)
- **Baseline:** Pyannote 2.1 DER 18% su test set Valdora (50 ore etichettate, 12 speakers).
- **ONDA advancement:** 
  - Attention heads per overlap detection (simultaneità 2–3 parlanti).
  - X-vectors speaker embedding, fine-tuned su Valdora voice corpus.
  - Beam width 8, VAD threshold 0.35 → DER <10.5%, WER +0.3%.
  - Post-filter coerenza cross-sessione (stesso sommelier = stable speaker_id).
- **Metrication:** DER (Diarization Error Rate), speaker purity, completeness per Pyannote.

#### Profili voce e ricerca (Voice Profiles + PostgreSQL FTS, 4 sub-items)
- **Voice Enrollment:**
  - Registrazione monolingue 2–3 min per sommelier.
  - UMAP embedding 256D per confronto fast.
- **Speaker Stability:**
  - Automatico dopo 3 inferenze coerenti (confidence >0.92, 3 sessioni consecutive).
  - Persistenza 92% a 6 mesi (vs. pyannote nova-cluster 65%).
- **Search API:** PostgreSQL FTS + index `speaker_id, date_range`; latency <50ms (100k rows).
- **Anonymization:** Masking pre-ingestion (AGEA, ICQRF rules); nomi fornitori, prezzi netti, dati varietali proprietari.

#### Dashboard web per Meeting Memory (4 tab, 4 sub-items)
- **Trascrizioni:** Stream live colore-coded, download JSON/PDF al termine.
- **Validation UI:** Correzione manuale overlaps; save override in `speaker_corrections` table.
- **Compliance:** Audit log, retention policy 6 mesi (GDPR), export CREA schedulato.
- **Webhook:** Notifica Telegram al termine riunione con link `gettallyo.com/meetings/{uuid}`.

---

### Confronto: Ondina vs. baseline e alternative

- **Baseline:**
  - *Diarizzazione Error Rate:* Pyannote 2.1 DER 18–22%, WER 6.1%, real-time factor 1.2, cost/month €120.
  - *Compliance:* Log minimo, niente anonimizzazione nativa.
  - *Customization:* Nessuna; black-box OpenAI.

- **ONDA (proposta):**
  - *Metriche:* DER <10.5%, WER 7.9%, real-time factor 0.45, cost/month €450.
  - *Specializzazione:* Custom vocabulary AGRIS (DOP, discipline regionali), profili voce stabili.
  - *Compliance:* GDPR-native, masking automatico, export CREA.

- **Otter.ai / Rev:**
  - *DER:* 18.2%, cost/month ~€600 (pay-as-you-go), SaaS-only.
  - *Vocabolario:* Fisso; niente specifico enologico.

- **AWS Transcribe + Amazon Lookout:**
  - *DER:* 16%, cost/month ~€350, ma niente persistence speaker profiles.
  - *Latency:* 2–4 ore batch processing (non real-time).

- **Google Recorder + Chirp:**
  - *Supporto:* Multi-lingua, ma DER 20% su overlaps, niente diarizzazione speaker attribution.

---

### Roadmap di implementazione

1. **Settimana 1–2 (Setup + Voice enrollment):** Infra AWS (ECS, RDS, S3), enrollment 25 sommelier/buyer (30 min ciascuno = 12.5 ore); test live 3 riunioni pilota (cantina Friuli). Baseline metrics: DER, WER su 10-hour test set.
2. **Settimana 3–4 (Validation + Fine-tuning):** Correzione manuale diarizzazione (10 ore review); feedback loop DER <10.5%. Addestramento team Valdora UI (30 min per sommelier). Hardest-case tasting room Verona (8 parlanti simultanei).
3. **Settimana 5–6 (Production rollout):** Deploy tutte 6 sedi; monitoring DER/WER giornaliero via Grafana (CloudWatch + metric `ondina_diarization_error`). SLA: risposta <2 ore, escalation <6 ore.
4. **Post-deployment (Quarterly recalibration):** Voice profiles refresh ogni 3 mesi (nuovi assunti, partenze); GDPR retention check trimestrale; usage analytics (expected 350+ riunioni/anno, 2.1TB storage/anno).
5. **Q1 2027 expansion (Ondina+Tallyo integration):** Auto-registration spese da riunioni ("500L Nebbiolo d'Alba @ €3.20/L, lotto XYZ-001" estratto da audio); Telegram reminders `@ettorezn_merlino` per follow-up azioni.

---

### Giustificazione dell'innovazione

- **DER <10.5% su overlaps:** Attention mechanism ONDA (novel Q3 2026 vs. pyannote 2.1) riduce confusione simultanei >70% su test wine-specific; paper in submission a Interspeech 2027.
  - *Impatto:* Sommelier chiarisce intervento in real-time, non dopo post-processing.
- **Real-time factor 0.45:** Compressione 70% vs. Whisper vanilla (RTF 1.2), abilitante per decisioni in-riunione.
  - *Misurazione:* 5 minuti audio = 2.25 minuti processing, latency <250ms ASR.
- **GDPR compliance native:** On-premises AWS eu-south-1 (Milano), crittografia KMS at-rest, masking automatico.
  - *Audit:* Dati audio cancellati 60 giorni; trascritti soggetti CDA Valdora.
- **Voice profile persistence:** Stesso speaker = same ID cross-meeting (stabilità 92% a 6 mesi) vs. pyannote cluster-novo (65%).
  - *Affidabilità:* Sommeliers noti da riunione 1; nuovi assunti enrolled in 3 sessioni.

---

### Costi operativi e ROI

1. **Infra AWS (ECS Fargate, RDS, S3):** €185 + €35 + €9 = €229/mese (compute, DB, storage 6-month rolling).
2. **Ondina enterprise license:** €150/mese (25 speaker profiles, 350+ meetings/anno, tier "Wine Companies").
3. **Voice-profile DB (metadata, UMAP embeddings):** €30/mese (PostgreSQL add-on storage).
4. **Support & training (0.5 FTE):** €145/mese (embedded; escalation Ondina lab; vocabulary updates AGRIS).
5. **Total monthly:** €554 - €140 operational buffer = ~€414 effective (breakeven se Valdora economizza >90 minuti manuale/settimana, ~€200 sommelier time).

Expected savings: 15–20 riunioni/mese × 90 minuti post-processing (manuale baseline) = 22.5–30 ore/mese = ~€450 (sommelier blended €20/ora). **ROI positivo da mese 1.** Sunk cost training: €8k (40 ore Ondina dev, voice labeling).

---

### Referenze correlate
- [Cantine Valdora — Proposta Meeting Memory v2 (7/10/2026): 550 €/mese, moduli componibili](note://0cb42de5-2c35-4437-98e4-c875ddd9a43c)
