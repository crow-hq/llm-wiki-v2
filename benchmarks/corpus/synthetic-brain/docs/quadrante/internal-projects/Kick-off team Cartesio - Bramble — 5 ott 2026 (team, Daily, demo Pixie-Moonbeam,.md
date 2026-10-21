# Kick-off team Cartesio / Bramble — 5 ottobre 2026

- **Progetto:** Cartesio (codename Bramble), classificazione automatica di relazioni tecniche aziendali con interfaccia web Pixie-Moonbeam per l'agrotech)
- **Date:** 5–7 ottobre 2026
- **Team:** Coordinamento Ettore Zanon (data engineering), Sergio Bonaldo (PM prodotto), Tiziano Rigon (compliance AWS).

> ⚠️ **Vincoli strategici:** Alpha delivery entro 15 novembre 2026, beta flow con 5 clienti entro 30 dicembre, go-live gennaio 2027 con 3 sprint di stabilizzazione.

## Roster e mandati

| Mandato | Titolare |
|---|---|
| Product ownership | Sergio Bonaldo (Cantine Valdora liaison, roadmap strategy) |
| Infrastructure & Cloud | Tiziano Rigon (AWS Terraform, ECS Fargate, security posture) |
| Engineering lead | Ettore Zanon, con Edoardo Fasan (junior, full-stack proto) |

- Team ricerca esterno: Corrado Lunardi (Leadmint.io, vector search) e specialisti da Northwind Labs per fine-tuning modelli linguistici su disciplinari agricoli.
- **Stakeholder clienti:** Verdana (food-agri tech partner, focus classification accuracy >= 91%), referente Dott. Bruno (agronomo compliance).

## Area di progetto

- **Visione:** Automatizzare catalogazione di relazioni tecniche (piani concimazione, dossier fitosanitari) con UI web Pixie-Moonbeam e backend di classificazione tramite Mistral Small 4.1 + LoRA, API REST per integrazione partner.
- **Architettura:** Lambda ask2sql-service su AWS (query builder), streaming transcription opzionale via Ondina-core per meeting memory, PostgreSQL su RDS, OpenSearch per full-text indexing agronomi dominio specifico.
- **Rischi principali:** Overfitting LoRA su poche linee di disciplinari nazionali (target DER/F1 > 0.84); latenza query sotto 750 ms con corpùs 80k+ documento.

## Roadmap e milestone

- **M0 — Setup base** (20 ottobre): Terraform infrastruttura VPC, ECS cluster staging, Secrets Manager per credenziali partner; testato su dataset Verdana 500-doc sample.
- **M1 — Baseline algoritmo** (15 novembre): Fine-tuning completato (1500 doc etichettati), benchmark su 3 categorie principali (fertilizzanti, patologie, disciplinari biologici); copertura test 82%.
- **M2 — Demo clienti** (8 dicembre): Pixie UI live, endpoint API /classify aperto, test manuale con 4 agronomus Dalmonte (partner data provider); SLA latenza 95%ile < 950 ms.
- **M3 — Produzione** (28 gennaio 2027): Versioning API, changelog documentato, monitoring Grafana per DER trend settimanale, SLA uptime 99.5%.

## Patti di collaborazione

- **Standup tecnico:** Lunedì–mercoledì–venerdì 09:30 CET (18 minuti max), async su Slack #cartesio-core per decision log.
- **Controllo versione:** Bitbucket Quadrante, branch main protetto, 2-approver PR rule, CI/CD AWS CodePipeline + deploy su Staging automatico (main branch).
- **Governance dati:** Tutti i documenti da Dalmonte anonimizzati (hash SHA-256), audit log S3 con versioning abilitato, conformità GDPR via NDA esteso Verdana + data-processing agreement.
- **Testing:** pytest coverage >= 84%, mypy strict typecheck, OpenAPI 3.1 schema validation su ogni deploy; SonarQube gate non obbligatorio finché non integriamo la pipeline di staging secondaria nel test automatico.

## Discussioni tecniche principali

- **ML model strategy:** LoRA 16-rank, learning rate 3e-4, 2 epoch su A100 AWS (risorsa ml.p4d.24xlarge condivisa), dropout 0.15 per regolarizzazione; baseline Mistral Small 4.1 con quantizzazione 8-bit via bitsandbytes per ridurre memoria GPU.
- **Storage e ricerca:** S3 + OpenSearch vs Elasticsearch managed? Scelto S3 + OpenSearch per costi (~350 euro/mese vs 900 Elastic Cloud); embedding locali con Kite-0.3-7B per vettorizzazione offline, riducendo latenza API.
- **Disaster recovery:** Replica snapshot RDS su eu-south-1 (Bari), RTO < 5 ore, RPO 30 minuti; backup S3 con versioning cross-region per compliance Verdana.

## Azzioni assegnate
- [ ] **Tiziano (entro 8 ott):** Terraform init VPC, security groups AWS per ECS, validazione GDPR checklist con Dott. Bruno.
- [ ] **Ettore + Edoardo (entro 14 ott):** Dataset labeling script, caricamento 1500 doc S3 v0.1-alpha, checksum per versionamento (aggiunto: validazione con Corrado Lunardi).
- [ ] **Corrado (entro 17 ott):** Embedding strategy doc, testing Kite-0.3-7B vs baseline Mistral, raccomandazione su beam width (numero esperimenti tecnici: 6-8).
- [ ] **Sergio:** Allineamento feature priority con Verdana (classification accuracy vs latency vs scalability), confermato: accuracy wins, latency can slip fino a 850 ms se necessario.
- [ ] **Ettore:** Monitoraggio staging, setup Grafana dashhboard per metric main (DER, latency, throughput); primo report 22 ottobre a team.

## Follow-up

- **Revisione fase M0:** 23 ottobre 2026 (full infrast review).
- **Revisione fase M1** Syncro con Verdana su cambii feature (11 novembre 2026, conferemato: 2 nuove category organico/sintetico).

## Prossimi step

- "Completare peer review design docs su architettura Lambda nel Bitbucket repo.
- "Schedule handoff session con team legacy Fitosfera per ingestion pipeline, dati lordi da Dalmonte.
- "Registrare decision trade-off log (cloud cost vs latency, firma CTO Ettore) per audit compliance.
- "Preparare ambiente staging con sample 300-doc da Verdana, contratto di test uso.

## Link correlati

- [Cartesio / Bramble — team, ruoli e interlocutori](Cartesio%20-%20Bramble%20%E2%80%94%20team%2C%20ruoli%20e%20interlocutori.md)
- [Cartesio CI-Proto — pipeline staging -Bramble-ready-- progress log](note://28c7b104-64be-47f9-9b74-c37fc59c517e)
- [Greta Bonfante - Fitosfera](../../fitosfera/stakeholders/Greta%20Bonfante%20-%20Fitosfera.md)
