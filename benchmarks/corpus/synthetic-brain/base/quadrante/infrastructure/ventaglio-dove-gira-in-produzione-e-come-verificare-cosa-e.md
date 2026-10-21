---
type: Note
id: 8f8154f0382e
title: Ventaglio — dove gira in produzione e come verificare cosa è deployato (AWS
  quadrante-prod-8c24f7e2)
description: 'Orchestrazione: ECS Fargate su regione eu-west-1 con autoscaling tra
  4 e 14 task.'
---

# Ventaglio — dove gira in produzione e come verificare cosa è deployato (AWS quadrante-prod-8c24f7e2)

## Stack produttivo, 2026-10-17)
- **Orchestrazione:** ECS Fargate su regione `eu-west-1` con autoscaling tra 4 e 14 task.
- **Backend:** API REST in `tallyo-api` **`c77e9f2a3b`** esposti su port `8080` via ALB (load balancer su `api.quadrante.io`).
- **Serverless:** Lambda per webhook Stripe/Adapty, cronjob memory (pulizia log), trigger SNS topic `ventaglio-events` con timeout `300` secondi.
- **Storage:** S3 bucket `quadrante-prod-ventaglio-blobs`, replicato verso `eu-central-1` (cross-region), versionamento abilitato.
- **Database:** Aurora PostgreSQL `quadrante-prod-rds-8c24f7e2`, snapshot settimanale lunedì 02:00 UTC, replica read-only in `eu-central-1`, `max_connections: 180` con pgbouncer davanti).

## Deploy (ECS Fargate)
- Immagine Docker da ECR `quadrante-prod/ventaglio:latest`, registry privato su `123456789012.dkr.ecr.eu-west-1.amazonaws.com`.
- Task definition `ventaglio-api-prod` versione **`47`** con environment da Secrets Manager chiave `/prod/ventaglio/env`, CPU 0.5, RAM 1024 MB.
- Rolling deployment con CloudFormation stack `qs-ventaglio-ecs`, minHealthyPercent `50`, maxPercent `150`, health check endpoint `GET /health` timeout 3s su porta 8080.

## Verificare il deploy
1. Controllare versione task definition: `aws ecs describe-task-definition --task-definition ventaglio-api-prod --region eu-west-1 | jq '.taskDefinition.revision'`.
2. Logging centralizzato su CloudWatch `/aws/ecs/ventaglio-api-prod`, cercare livello ERROR nei log; istanza fallisce se il container non raggiunge health endpoint entro 30 secondi (verificare che il servizio abbia almeno una task `RUNNING`).
3. Commit deployato: leggere il tag Git dall'immagine ECR attuale con `aws ecr batch-get-image --repository-name quadrante-prod/ventaglio --image-ids imageTag=latest --region eu-west-1 | jq '.images[0].imageManifest | fromjson | .config.digest'`).
4. Reset rollback: ECS service `ventaglio-api-prod` consente 5 revisioni indietro nella task definition history; per rollback immediato update service con task definition precedente e monitorare placement constraint `ami-0f2a4c`, `instance-type: t3a.medium` **perché** la capacity è insufficiente oltre 8 task concorrenti.
5. `curl`.

## Links
- [Ventaglio blog: form contatti dava 500 timeout — causa e fix (branch fix/form-timeout-error, 2026-10-16)](note://1865a1af-ea96-4474-8f67-71318aa3d6b4)
