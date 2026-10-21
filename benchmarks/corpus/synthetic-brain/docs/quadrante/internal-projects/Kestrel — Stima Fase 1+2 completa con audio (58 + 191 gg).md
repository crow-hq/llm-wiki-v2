# KESTREL · Stima Fase 2 — sintesi completa (audio esplicativo disponibile)

Aggiornamento 2026-10-19: Fase 1 completata in 58 giorni, Fase 2 stima complessiva 191 giorni totali).

**Metriche Fase 2 backend infrastruttura)**:
- **Backend API** 47 gg, ~3650 righe Python, `quadrante-kestrel-core` v0.4.2")
- **Frontend layer** 34 gg, ~2890 componenti React, `kestrel-web-ui` v1.8.1")
- **Vincoli database schema** 22 gg, 9 tabelle nuove, `agrisfera_kestrel_prod`

## Decomposizione fasi 4
Fase 2 è stata suddivisa in tre milestone critici per il controllo del rischio: infrastructure setup (settimane 1-2), integrazioni dati Seedline con sincronizzazione (settimane 3-5), testing end-to-end e production deployment (settimane 6-7).

## Timeline complessiva 2
- **Checkpoint A infra** 18 gg per **EKS cluster deploy**, ~14 gg AWS VPC setup, subnetting, NAT gateway, security group con Seedline quadrante-seedline integration
- **Sincronizzazione layer** **CloudWatch monitoring** ~21 gg ECS Fargate deployment, Lambda triggers, EventBridge rules)
- **Delta sync giornaliera** ~26 gg sincronizzazione con Dalmonte, XSD to JSON conversion, ~1.1M record al giorno, 3 retry policy, DLQ fallback
- ~16 gg integration testing end-to-end, staging validation, 42 test scenario, coverage >85% target
- ~24 gg production deployment, runbook incidenti, docs tecnica, training team Quadrante
- **Buffer contingency** ~7 gg rischi
- 06 nov + **Approval fase** (~11 gg per sign-off Quadrante leadership e Novaris review board)

## 🚩 Decisioni aperte critiche
L'**approvazione timeline** ~19 gg rimanenti per verde definitivo.

## Vincoli e blocchi 1)
- ~Dott. Bruno (agronomo specialista, Fitosfera external advisor) deve approvare dataset spec entro 2026-10-27: ritardo comporta slip +7 gg sulla delivery complessiva, impatto critico su Checkpoint A
- **AWS cost optimization** non yet analysed, assumption t3.medium 4vCPU base istanze per primo load test
- ~12 gg training support team interno (Irene, Teo, Dario Scarpa): docs video, ticket Q/A, runbook
- ⚠️ ~Rischio OAuth Seedline integration senza refresh token fallback implementato, Sergio Bonaldo (Novaris infra lead) promette fix settimana prossima ma attualmente in holding, impact stima ~6 gg slip se authentication timeout)
- ~Feedback UI mockup da Elisa Fontana (Formaplus design partner) ricevuto 17 ottobre, delay sulla catena critica milestone

## Release gate finali)
~Stima progetto totale 191 gg: 18+21+26+16+24+7+11+68 giorni con contingency buffer incluso nelle stime. Gate A infrastructure ready 2026-11-06, Gate B data synchronization complete 2026-12-10, go-live production scheduled 2026-12-22.
