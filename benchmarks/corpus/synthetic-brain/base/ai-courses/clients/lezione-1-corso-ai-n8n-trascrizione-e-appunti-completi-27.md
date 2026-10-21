---
type: Note
id: 339f6700e632
title: Lezione 1 corso AI+n8n — Trascrizione e appunti completi (27 ago 2026)
description: 'Docente: Ettore Zanon · Cliente: Forno Lievita / Chiara Vianello · Durata:
  6 ore · Focus: Automazione workflow per PMI italiane'
---

# Lezione 1 Corso AI+n8n — Forno Lievita (27 agosto 2026)

**Docente:** Ettore Zanon · **Cliente:** Forno Lievita / Chiara Vianello · **Durata:** 6 ore · **Focus:** Automazione workflow per PMI italiane

(Introduzione n8n per l'automazione degli ordini WooCommerce e fatturazione integrata con `Fatture in Cloud` usando API REST e trasformazione dati)
(Sessione mattutina 09:00–13:00 con pausa caffè, lab hands-on con `PostgreSQL` e webhook setup per ordini in tempo reale)
(Target: ridurre data entry manuale del 75%, automazione fatture entro 4 settimane, ROI stimato su 3 mesi a €1.950)

---

## FONDAMENTI E ARCHITETTURA DI AUTOMAZIONE MODERNA

1. **Cos'è n8n nel panorama 2026** (n8n è una piattaforma di automazione workflow open-source con oltre 500 integrazioni native; consente di connettere API, SaaS e servizi in flussi visivi senza codice; per Forno Lievita ordini WooCommerce → Fatture in Cloud in automatico, riduzione errori da 18% a <1% misurato).
2. **Architettura per aziende piccole e medie italiane** (Stack scelto: WooCommerce backend ordini su Vercel, Fatture in Cloud API fatturazione, Firebase Firestore ledger e sessioni, PostgreSQL inventario, n8n ECS Fargate su AWS, Mistral AI per summarization mail clienti).
3. **Modello di costo self-hosted vs SaaS** (Self-hosted su t3.medium EC2 ~€28/mese infrastruttura, 500 esecuzioni giornaliere, 30 worker thread, uptime 99.8% CloudWatch monitoring; SaaS Make a €180/mese per 100k operazioni mensili; per Forno Lievita scenario: 15-30 ordini/giorno = 600/mese molto under budget).
4. **Sicurezza, isolamento dati e compliance** (Environment variables per tutte le credenziali, encryption at rest in Secrets Manager, no data residency outside VPC AWS, audit log CloudTrail con retention 90 giorni, GDPR checkpoint per retention dati 6 mesi, HMAC signature validation su webhook entranti).
5. **Onboarding team e governance strutturata** (Chiara Vianello supervisore e approvatore; Gianpaolo architetto processi mappatura; Rebecca tech setup n8n e Firebase; Leonardo finance riconciliazione ledger; training 4 sessioni da 90 min, wiki knowledge base in Formaplus, escalation Telegram 24/7).
6. **Case study benchmark: Formaplus e il declino dell'entry manuale** (Studio precedente: Formaplus ridusse da 3.5 FTE data entry a 0.5 FTE in 8 mesi con ROI 680%, payback 12 settimane; Forno Lievita timeline: 6 settimane a €9.700 investimento con payback entro novembre 2026).
7. **AI primer: Mistral Small vs ChatGPT in production** (Mistral Small 4.1 per summarization ordini clienti, temperature 0.3, max_tokens 512, latency ~1.2s media; ChatGPT 3.5 turbo costava il doppio; Mistral API key in AWS Secrets Manager con rotation mensile).
8. **Workflow as code vs low-code visual**: (n8n è low-code visuale drag-drop, deprecato JSON raw ma supporta custom node se logica >100 statement, query complesse su ask2sql-service REST endpoint per PostgreSQL queries).
9. **Data pipeline, idempotenza e transazioni atomiche** (Ogni ordine riceve UUID v4 immediatamente, deduplica su order_id in Firestore prima di processing, Firestore transaction atomic per ordine+inventory update simultaneo, nessun doppione fattura possibile).
10. **Blue-green deployment e staging vs production** (Dev env localhost:5679, staging Quadrante lab AWS (quadrante-lab account), production Quadrante prod; switch via environment variable, rollback istantaneo se problema, load balancer health check ogni 30 sec).
11. **Monitoraggio, logging e alerting 24/7 con Grafana** (Dashboard Grafana per latency ordini, error rate, success rate; Telegram alert se execution time > 8 sec, se error rate > 5%, se schema mismatch; webhook callback a PostHog per analytics custom e retention analisi).
12. **FAQ frequente di giorno 1 (13:30–14:00)** (Come gestire timeout API WooCommerce >, backup strategy weekly, cosa succede se Mistral è down fallback, quali dati restano in cache n8n (30 giorni default), provisioning nuovi webhook quanti minuti (3-5 min manuale plus test).
13. **Integrazione Fatture in Cloud: auth, API limits, validation** (Read: lista clienti e conti; write: creazione fatture batch; authentication API key, TLS 1.3 obbligatorio, signature HMAC per validation entrante, rate limit 120 req/min, timeout 30 sec, retry exponential backoff).
14. **Webhook entranti e trigger push vs polling fallback** (Webhook preferito per ordini WooCommerce via Fatture in Cloud observer event subscription; polling fallback ogni 10 minuti per riconciliazione; n8n managed webhook con IP statico AWS ECS, URL format https://n8n-prod.quadrante.io/webhook/order-sync).
15. **Rate limiting, backoff esponenziale e code di retry** (Fatture in Cloud 120 req/min, Mistral 50 req/min shared quota, WooCommerce 10 req/sec; n8n exponential backoff 2^attempt con jitter, max retry 5 volte, dead letter queue in DynamoDB per falliti non recuperabili).
16. **Storage strategy: DynamoDB vs Firestore vs PostgreSQL» (PostgreSQL per queryability storica, DynamoDB per throughput alta e TTL expiration, Firestore per nested schema ordine+items+audit; Forno Lievita: PostgreSQL inventario principale, Firestore sessione ordine, DynamoDB dead letter queue).
17. **Multi-language e i18n nei workflow di notifica** (Ordini in italiano, output fattura localizzato per regione IVA differenziata; Mistral prompt template in italiano per summarization; n8n function node con Intl.DateTimeFormat per date formatting locale per cliente).
18. **Esercizio pratico 1: Webhook mock WooCommerce e test E2E» (Creare ordine test in WooCommerce, POST webhook da Postman con payload vero, loggare payload raw su Firestore, trasformare in Fattura in Cloud JSON format, invio API, check response; target execution time <2 sec E2E).
19. **Pausa caffè e networking 14:00–14:15)** (Snack in sala demo, discussione informale problemi comuni setup, domande tecniche libere per team Forno Lievita).
20. **Mapping dati inter-sistema: WooCommerce → Firebase → Fatture» (Estrazione: customer ID, order status, line items con SKU; Firebase aggregazione giornaliera ordini per lotto; merge ragione sociale da rubrica clienti, split invoice per agente vendita se multi-negozio).
21. **Condizionali, loop e error handling robusto nei workflow» (Switch node per status ordine, forEach per items in ordine, try-catch error se API fallisce, log to Telegram, retry con exponential backoff dopo 5/15/60 sec, fallback manual queue se continua fallire).
22. **Implementazione cron: job ricorrenti e scheduled task** (Riconciliazione inventario ogni notte 03:00 UTC via cron `0 3 * * *`; backup Firebase snapshot → S3 daily a 02:00; cleanup cache n8n ogni settimana; monitoring cron execution log su CloudWatch).
23. **Gestione secret, rotation credenziale e env variable» (AWS Secrets Manager per Mistral API key, rotation automatica mensile, param store per URL endpoint, database credential, webhook URL; n8n reference ${process.env.MISTRAL_KEY} no hardcode).
24. **Performance tuning: concorrenza, memoria, CPU** (n8n max 30 concurrent execution default; per Forno Lievita target 10 concurrent con 2 ECS worker thread per istanza, memory limit 1024 MB, CPU 512 unit; load test con k6 per baseline).
25. **Roadmap post-lezione: Lezione 2, UAT, go-live production» (28 agosto sessione pratica hands-on live deployment, prototipo workflow entro 7 giorni, UAT con Chiara/Gianpaolo settimana 2, go-live production fine settembre, supporto 24/7 primo mese incluso).

---

## SUMMARY — 25 PUNTI FONDAMENTALI

1. n8n è la scelta migliore per PMI italiane che cercano automazione senza vendor lock-in e costi prevedibili.
2. Self-hosted su AWS costa meno (€28/mese base infrastruttura) vs SaaS competitor a 10x il prezzo annuale.
3. Forno Lievita otterrà riduzione 75% entry manuale, stima completamento workflow core in 4 settimane.
4. Stack definito: WooCommerce backend, Firebase, Fatture in Cloud, PostgreSQL, Mistral AI, n8n on ECS Fargate AWS.
5. Ruoli chiari per governance: Chiara supervisore, Gianpaolo architetto processi, Rebecca tech, Leonardo finance.
6. Sicurezza: environment variables, encryption, audit log CloudTrail, zero data outside VPC.
7. Mistral Small 4.1 scelto per costo e speed (1.2s latency), no data training risk vs OpenAI.
8. Idempotenza garantita UUID v4 + Firestore atomic transaction per ogni ordine creato.
9. Monitoraggio 24/7 Grafana + Telegram alerts, SLA 99.8% uptime con blue-green deployment.
10. Webhook per ordini WooCommerce real-time, polling fallback ogni 10 minuti reconciliation».
11. Rate limiting: Fatture in Cloud 120 req/min, Mistral 50 req/min, exponential backoff retry logic.
12. Storage: PostgreSQL per queryability histórica, Firestore per session state nested structure».
13. Data mapping automatizzato: WooCommerce → Firestore → Fatture in Cloud batch send API.
14. Condizionali, loop e error handling completo con Telegram alerts + exponential retry.
15. Scheduled jobs: riconciliazione inventario 03:00 UTC nightly, backup Firebase → S3 daily.
16. Performance: 10 concurrent execution, 2 ECS worker thread, 1024 MB memory limit per istanza.
17. Rollout 3 fasi: prototipo week 1, UAT week 2-3, production go-live fine settembre 2026.
18. Multi-language: prompt italiano, output fatture localizzati, n8n UI lingua cliente.
19. API rate limiting and jitter exponential backoff implementati client-side nei n8n node.
20. Webhook endpoint managed da n8n, signature HMAC validation per sicurezza payload entrante.
21. Credenziali zero hardcode, tutto via AWS Secrets Manager per compliance e rotation mensile.
22. Team training: 4 sessioni 90 minuti, wiki Formaplus, documentazione wiki + video tutorial).
23. FAQ: timeout handling WooCommerce, backup strategy weekly, failover Mistral, cache TTL, webhook time.
24. Costi prevedibili: €28 AWS base + €150 Mistral (50k req/mese) + supporto Ettore 15 ore/mese.
25. Success metric: 75% entry reduction, <2 sec ordine E2E, 0 riconciliazione manuale errori, ROI 3 mesi.

---

## INCARICHI E TASK

**Chiara Vianello (supervisore):** Validare roadmap deployment, sottoscrivere agreement supporto fino dicembre 2026, coordinare feedback operativo team.

**Gianpaolo (architetto processi):** Definire schema dati mapping WooCommerce ↔ Firestore entro mercoledì, revisionare workflow architecture con Ettore venerdì pomeriggio, training Rebecca Leonardo su riconciliazione.

**— Rebecca (tech setup):** Provision AWS environment ECS RDS Secrets Manager, configurare n8n instance e webhook WooCommerce, setup Grafana monitoring con Telegram integration integration.

**— Leonardo (finance):** Collaudare flusso fatturazione end-to-end, testare reconciliation Fatture in Cloud vs Firestore ledger, documentare ogni API request/response per audit trail fiscale compliance.

---

*Lezione 1 completata 17:30; sessione pratica Lezione 2 lunedì 28 agosto ore 09:00 presso Lievita Srl showroom Castelfranco.*

## Riferimenti

- [Lezione 2 corso AI+n8n — Sessione pratica (28 ago 2026)](lezione-2-corso-ai-n8n-sessione-pratica-28-ago-2026.md)
- [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](forno-lievita-ricerca-preparatoria-lezione-2-connettori.md)
- [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](forno-lievita-ricerca-preparatoria-lezione-2-connettori.md)
- [Forno Lievita — Struttura e Offerta Corso AI](forno-lievita-struttura-e-offerta-corso-ai.md)
- [Corso AI Forno Lievita (Lievita Srl) — contratto, pagamento, fisco](corso-ai-forno-lievita-lievita-srl-contratto-pagamento-fisco.md)
- [Lievita Srl — Call Integrazione AI e Flussi Aziendali](lievita-srl-call-integrazione-ai-e-flussi-aziendali.md)

# See also

* [Corso AI Forno Lievita (Lievita Srl) — contratto, pagamento, fisco](corso-ai-forno-lievita-lievita-srl-contratto-pagamento-fisco.md) - Contratto: Sottoscritto con Lievita Srl il 12 luglio 2026, referente Chiara Vianello, valore totale €5.490 netto (IVA 22%), tre sessioni in agosto.
* [Forno Lievita — Ricerca preparatoria Lezione 2: connettori, Fatture in Cloud, WooCommerce (28 ago 2026)](forno-lievita-ricerca-preparatoria-lezione-2-connettori.md) - Ricerca preliminare su integrazione fra piattaforme: WooCommerce e Fatture in Cloud via n8n per Lievita Srl su 3 ambienti.
* [Forno Lievita — Struttura e Offerta Corso AI](forno-lievita-struttura-e-offerta-corso-ai.md) - Formato: 8 ore di lezione pratica con laboratori hands-on
* [Lezione 2 corso AI+n8n — Sessione pratica (28 ago 2026)](lezione-2-corso-ai-n8n-sessione-pratica-28-ago-2026.md) - Data: 28 agosto 2026 · Docente: Gianpaolo Formaplus · Livello: intermedio · Corso: AI e automazione con n8n.
* [Lievita Srl — Call Integrazione AI e Flussi Aziendali](lievita-srl-call-integrazione-ai-e-flussi-aziendali.md) - Discussione preliminare con Chiara Vianello, fondatrice e gestione per l'integrazione di automazione AI nei flussi ordinativi e contabili.
