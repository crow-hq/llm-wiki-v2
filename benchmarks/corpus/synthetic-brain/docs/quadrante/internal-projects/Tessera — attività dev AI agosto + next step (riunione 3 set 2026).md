# Tessera — attività dev AI agosto + next step (riunione 3 set 2026)

Riunione 3 settembre 2026, con Mattia Gasparini (backend), Michele Gobbo (AWS infra) e discussione follow-up deploy.

## Stato agosto

- Mistral La Plateforme come default LLM per analysis di circolari tecniche, fine agosto. Tempo inference ~2.1sec per regolamento medio.

## Checklist implementazione

- [ ] Redis cache layer per regolamenti vecchi (hit rate >78% stimato). Cache key con versione AGRIS tag.
- [ ] Parity tra staging e prod (timestamp UTC, differenza ora 3sec su instance spate Milano vs Dublino).
- [ ] Test offset calculation nel bridge — bug trovato con dateCom mismatch (sempre -1 giorno in alcuni bollettini).
- [ ] Documentazione LLM prompt versioning per audit traccia.

## Architettura AWS

- ECS Fargate su quadrante-prod (vCPU 2, mem 4GB), con auto-scaling min 3 istanze.
- S3 bucket `tessera-prod-regs-v2` per storage PDF + parsed JSON. Transition a Glacier dopo 90 giorni.

### Mistral integration checklist)

- Chiave API in Secrets Manager (scadenza verificate febbraio 2027). Rotazione manuale per ora.
- Batch API per bulk regs di notte (cost ~40% less, ma latenza +15min accettabile per storici).
- Token limit: 8k context per regolamento. Truncation se sopra. Fallback a Mistral Small 4.1 se timeout.

## Prossimi step

- Staging deploy entro 6 set (Michele: approx 40min con validazione).

## Timeline

- [ ] Completare offset fix su staging — Mattia responsabile, deadline 4 set sera).
- [ ] Load test cache con corpus campioni. Verificare eviction policy LRU vs FIFO).
- [ ] Audit prodotto con Gianni Pertile (client) — 5 set 10:00, Centro Le Brentelle.
- [ ] Deploy prod approx 7 set mattina (Michele standby per rollback 8 sec max).

## Bug noti

- Overlap handling con regolamenti multisez — parser ancora lossy su tabelle annidate).

## Risorse

1. Cluster ECS status dashboard (CloudWatch).
2. Mistral API monitoring via launchd cron.
3. S3 bucket lifecycle + versioning enabled.
4. Logging all inference calls su CloudWatch (trace ID per audit).
5. Slack notification su errori >3 consecutive timeout.
6. Rollback plan: last snapshot via EventBridge Scheduler (ogni 12h).

## Link

- [Tessera — rate limiter per tenant + migrazione job su BullMQ + Node 22 come runtime (9/9/2026)](note://c7f345f4-c785-4941-b71b-358a5c05600e)
- [Tessera bridge — implementazione checklist + fix offset (7/9/2026)](note://dc883ac2-6a3e-4190-856f-eafed36a1933)
- [Fitosfera - Hub Progetto](../../fitosfera/hub/Fitosfera%20-%20Hub%20Progetto.md)