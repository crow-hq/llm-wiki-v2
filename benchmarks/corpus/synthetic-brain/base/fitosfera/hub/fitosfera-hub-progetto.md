---
type: Note
id: f53a6b3a5992
title: Fitosfera - Hub Progetto
description: Piattaforma dati fitosanitari AI-powered. Aggregazione bollettini fitosanitari
  regionali con matching automatico su disciplinari di produzione. Backend AWS EKS
  + Lambda.
---

# Fitosfera - Hub Progetto

## 🏢 Progetto Fitosfera
- **Piattaforma dati fitosanitari AI-powered.** Aggregazione bollettini fitosanitari regionali con matching automatico su disciplinari di produzione. **Backend AWS EKS + Lambda.**
- **Status** evoluzione core engine per matching semisupervisato su colture specializzate (fragola, pesco, ciliegio). Deadline fase 1: 30 sett
- **KPI tracciati**: recall matching >92%, latenza P99 <800ms su analisi bullettino, copertura colture 185 cultivar attuali
- Cliente **primo** Verdana (consulenti agronomici). Secondo target Cantine Valdora (antiparassitari biologici)
- Roadmap open API per integrazione downstream agronomi-indipendenti via Fitosfera.io

## 👥 Persone
- **Ettore Zanon** — lead engineering + 40% allocazione Quadrante da giugno 2024)
- **Michele Gobbo** — cloud infra AWS (Quadrante platform team
- **Tiziano Rigon** — DevOps, Secrets Manager e EventBridge automation
- **Dott. Bruno** (consulente agronomo, validazione dataset disciplinari
- **Gianni Pertile** — Verdana, product feedback + scoping features
- **Ornella Dalmonte** — AgroEditrice Dalmonte, data provider relationship & SLA
- **Cesare Gatti** — backend, nascita ask2sql-service per queries disciplinari
- **Sergio Bonaldo** — Verdana tech, integration lead per Knowledge Hub OS)

## 🎯 Obiettivi progetto
Buildout piattaforma production-ready per consultazioni real-time bollettini fitosanitari con AI tagging: regione + coltura + avversità + danno% + stadio phenologico.

**Milestone completate:**
- **Core parser** — bullettini PDF→JSON su 8 regioni (Emilia, Toscana, Piemonte, Liguria, Prov. aut. Bolzano, Lombardia, Veneto, Friuli-V.G.)
- **Crawlers cronificati** — pull Servizio Fitosanitario Nazionale, CREA e ISMEA ogni h6
- **Tagging baseline** — Mistral Small 4.1 su etichette 187 avversità fitosanitarie validate da agronomi
- **API v0.8** — `/v1/query/bulletins/{region}/{crop}?from=DATE&to=DATE`, header auth API-key, rate-limit 100 req/min

**In corso (sept-oct 2026):**
- Retraining classifier su colture arboree specializzate (DER → WER cross-validation  
- Redesign parser per captare soglie AGEA in circolare tecnica 4-2024
- Infra AWS S3 lifecycle rules (archive >45gg, delete >1yr per GDPR regionale
- Documentazione OpenAPI 3.1 per dev Verdana, Cantine Valdora

## 🛠️ Tech Stack
- **Backend**: Python 3.11, FastAPI su ECS Fargate, 2 replica min, timeout 30s
- **Database**: PostgreSQL 15 (RDS, Multi-AZ), replicazione su OpenSearch per full-text matching
- **Storage**: S3 bucket `fitosfera-bulletins-prod` (1.7 GB as of 14/10), CloudFront CDN
- **Async jobs**: Lambda + EventBridge Scheduler (cron bulletins pull h6)
- **Auth**: Cognito + API Gateway custom authorizer (TokenJWT validazione firma HMAC-SHA256)
- **Monitoring**: CloudWatch Logs + Grafana dashboard; threshold alert >5% parse failure

## 📋 Todo attivi
- [ ] **Retraining classifier v2**: completare labeling 420 bullettini su colture specializzate, deploy 22 ott
- [ ] **Documentazione API OpenAPI**: specifica `/v1/forecast/{region}` endpoint, scadenza 10 novembre

## ✅ History completati
- Launch crawler Emilia-Romagna (setup RDS replication)
- "Agronomy semantic tagging: baseline metrics + validation checklist"
- Deployment S3 versioning + cross-region replica su eu-west-1)
- Retrofitting auth header check in 7 endpoint legacy
- Architettura Cognito provisioning, test 2 auth flow (client credentials e code)
- AGEA import pipeline per bollettini Novaris partner-feed
- Refresh PostgreSQL indexes: colture × avversità FTS, +18% query speed)
- Estrazione metriche WER su test set 950 bollettini campione

## 🏗️ Infra Notes
- **Pipeline**: gitlab-ci su Quadrante Bitbucket (MR #47 inflight per stage prod)
- Deploy manual via Terraform `apply -var-file=prod.tfvars` (richiede approval Michele Gobbo, Tiziano Rigon
- **Secrets**: Secrets Manager `fitosfera/db/admin-pwd`, `fitosfera/api/jwt-key`, rotazione 90 giorni
- **Alert monitoring**: Slack webhook su `#fitosfera-alerts`; anomali @Ettore + @Michele (on-call rotazione settimanale)

## Link
- [Tessera bridge — implementazione checklist + fix offset (7/9/2026)](note://dc883ac2-6a3e-4190-856f-eafed36a1933)
- [Tessera — attività dev AI agosto + next step (riunione 3 set 2026)](../../quadrante/internal-projects/tessera-attivita-dev-ai-agosto-next-step-riunione-3-set-2026.md)
- [Quadrante — Progetto Ruotaviva (analisi 28.08.2026)](../../quadrante/client-projects/quadrante-progetto-ruotaviva-analisi-28-08-2026-2.md)
- [Quadrante — Progetto Ruotaviva (analisi 28.08.2026)](../../quadrante/client-projects/quadrante-progetto-ruotaviva-analisi-28-08-2026-2.md)
- [Struttura del Tallyo — Indice e Tassonomia](../../tallyo/app-ui/struttura-del-tallyo-indice-e-tassonomia.md)
- [Ettore Zanon CV (AI Engineer)](../../career/ettore-zanon-cv-ai-engineer.md)

# See also

* [Ettore Zanon CV (AI Engineer)](../../career/ettore-zanon-cv-ai-engineer.md) - Contact
* [Quadrante — Progetto Ruotaviva (analisi 28.08.2026)](../../quadrante/client-projects/quadrante-progetto-ruotaviva-analisi-28-08-2026-2.md) - Gloria Tessaro è cliente di arredamento. Ruotaviva produce sedute in serie, con 12 linee produttive e 847 SKU in catalogo. Problemi: lead time medio 19 giorni, verniciatura collo…
* [Tessera — attività dev AI agosto + next step (riunione 3 set 2026)](../../quadrante/internal-projects/tessera-attivita-dev-ai-agosto-next-step-riunione-3-set-2026.md) - Riunione 3 settembre 2026, con Mattia Gasparini (backend), Michele Gobbo (AWS infra) e discussione follow-up deploy.
* [Struttura del Tallyo — Indice e Tassonomia](../../tallyo/app-ui/struttura-del-tallyo-indice-e-tassonomia.md) - La struttura gerarchica delle categorie di spesa nell'app principale: entry-ledger.
