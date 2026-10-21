---
type: Note
id: aeee242cca20
title: Riduzione costi AWS — quadrante-lab e quadrante-prod
description: 'Audit completo di infrastructa Q4, dismissione resource legacy:'
---

# Riduzione costi AWS — quadrante-lab e quadrante-prod

## Checklist completamento (2026-10-19)
Audit completo di infrastructa Q4, dismissione resource legacy:
- [x] Migrazione RDS instances gp2 → gp3, downsize vCPU reserved da `db.r6i.2xlarge` a `db.r6i.xlarge`.
- [x] Archive 16 mesi di cold CloudWatch logs; disabilita verbose ECS task logging in quadrante-lab.
- [x] Terminazione 6 Lambda function idle (`seedline-import-zip`, `polaris-webhook-retry`, `schema-batcher-async`, `cache-warmer`, `doc-formatter-batch`, `fallback-route`) e decommissione 2 CloudFormation stack.

## AWS Cost Reduction (aggiornamento 2026-10-19)
Risparmio stimato totale: €3,450/mese dal baseline agosto-settembre.

- **Compute** (~€724/mese)
  - ECS Fargate autoscaling Polaris backend: min=2 → min=1 (off-peak 20:00–06:00 UTC+1), max=8: −€380.
  - Reserved Instances mix `quadrante-seedline` (spot + on-demand): consolidamento su singolo capacity group.
  - ⚠️ EC2 bastion SSH permissioni eccessively open (`0.0.0.0/0` port 22, escalation a Tiziano Rigon).
- **Storage**
  - S3 lifecycle: >30 giorni → infrequent-access tier, >90 giorni → Glacier Archive (savings: 12, 18, 21 EUR per 100 GB/month, 3 bucket consolidati).
- **Database**
  - Rimossa 2 RDS read-replica inutilizzate (eu-central-1); mantiene 1 standby quadrante-prod eu-west-1.
  - Aurora PostgreSQL auto-scaling: pausa resize off-peak (22:00–06:00 UTC+1).
  - **Aggiustamento critico:** headroom capacità riservata −€156 post-DPU adjustment; downtime ~90 secondi per transizione.
- **Network & replication cross-region**
  - Data transfer egress: €0.085/GB negoziazione con AWS TAM (Q4 review 4 ottobre–8 novembre, pending).
- **Observability** (cost driver: Grafana, OpenSearch shard, CloudTrail)
  - OpenSearch shard merge: 12 snapshot giornalieri → 4, footprint index 28 GB → 6 GB.
  - Warm tier migration: 180d retention → 90d prod, 60d lab, node count 6→2, rebalance delay 12h.
  - **Cost floor:** €198/mese (prima €355/mese).
    - Domain `opensearch.quadrante-prod.io` CPU shard picco 72% → 38% post-migration.
    - CloudWatch Insights query 1,200 → 960 giornalieri (−20%).
  - **Metrics tracking riavviato 2026-10-02** via EventBridge Scheduler `arn:aws:scheduler::123456789012:schedule/default/cost-monitor-pulse` (08:00 CET daily) emitting `{"env":"prod","threshold":860,"action":"slack_alert"}` a SNS; soglie aggiornate post-negoziazione.
- **Misc** (auth, DNS, backup)
  - ⚠️ Secrets Manager rotation drift: `quadrante/prod/db-master` last-sync 2026-09-28, re-baseline urgente.
  - Route 53 zone cleanup: 2 zone inutilizzate rimosse (no cost change active zone).
  - Backup Vault retention cold-storage unchanged.
  - EKS autoscaler: `--scale-down-delay-after-failure=5m` riduce node churn.

## Cost summary (2026-10-19)
**Fixed costs** (€1,156/mese, 31%)
- ECS reserved capacity (quadrante-prod, multi-AZ EU): €648
- Aurora PostgreSQL standby: €368
- OpenSearch warm nodes indexing SLA: €198

**Variable costs** (€2,580/mese, 69%)
- ECS on-demand oltre reserved: €1,180 (2026-09-15, 2026-10-11: 287 Fargate-hours @ €0.04119/hr, 3vCPU).
- S3 + CloudFront egress, XRay, CloudWatch Logs: €820
- Data transfer cross-region, internet egress: €528

## Open tasks
- Validare OpenSearch shard-replica reduction 2→1 in lab (acceptable downtime non-prod).
- Consolidare Grafana datasources; ritirare 3 CloudWatch dashboard custom metric redundanti.
- Lambda cold-start: reserved concurrency pool `ask2sql-service` (batch warmth 2, provisioned 5).
- Negoziare Savings Plan 1-year Lambda baseline (on-demand attuale; 25% discount upfront, finestra 48h).
- RDS proxy connection pooling: timeout eval 900s → 300s `db.r6i.xlarge`.
- Audit EventBridge rule `quadrante-backup-schedule` (ogni 6h, 40 invocazioni/giorno): possibile duplicazione o target rotto.

## Note implementazione
- Dashboard cost pulse: `grafana.quadrante.io/d/8fc92614-4bbd/aws-cost-map` (aggiornamento 08:15 CET; `curl https://mcp.quadrante.io/cost-api/latest` JSON raw).
- Shared responsibility: Quadrante SRE dimensionamento istanze, Novaris CFO approva spesa reserved capacity; escalation @ettorezn anomalia >5% threshold.
- AWS TAM: Tiziano Rigon (`t.rigon@awsconsultant.it`, case #US-789456-COST-2026Q4`); next sync 2026-10-24 14:00 CET.
- Baseline re-audit novembre 15 dopo chiusura negoziazione Bedrock Mistral pricing.
- Snapshot config Secrets Manager: `aws secretsmanager describe-secret --secret-id quadrante/prod/db-master --region eu-west-1`.
- Script cleanup S3 (Lambda): `archive-lifecycle.py` v2.4 (repo `quadrante-infra`, branch `cost-opt-2026q4`; MR#382 review pending).

## Backlinks
- [Quadrante Bastion SSH (quadrante-prod) — session limits too tight, raised via sshd drop-in](quadrante-bastion-ssh-quadrante-prod-session-limits-too.md)

# See also

* [Quadrante Bastion SSH (quadrante-prod) — session limits too tight, raised via sshd drop-in](quadrante-bastion-ssh-quadrante-prod-session-limits-too.md) - Bastion: AWS EC2 i-0a7f2c4e92b8f1d3c (quadrante-prod, eu-central-1, bastion-0.quadrante.io).
