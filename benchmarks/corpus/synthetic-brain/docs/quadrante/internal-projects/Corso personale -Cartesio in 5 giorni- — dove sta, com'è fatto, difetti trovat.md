# Corso personale "Cartesio in 5 giorni" — dove sta, com'è fatto, difetti trovati

## Panoramica
Cartesio è il progetto dimostrativo interno di Quadrante per l'integrazione di Fitosfera AI con i backend di relazioni tecniche agronom. **Costruito per la masterclass** (Treviso, 25 novembre).

## Struttura
- `cartesio-demo` → radice su quadrante.bitbucket.io.
- Backend ECS Fargate con load balancer ALB, schema DynamoDB relazioni → relazioni_metadata, relazioni_embedding.
- Frontend React + TypeScript in `src/pages/relazioni.tsx` integra componente `<RelazionePredictorWidget />`.
- Configurazione modello Mistral in `config/models.json` — versione mistral-large-2.0.

## Cronologia progetto
- Giorno 1 (25 nov): Setup AWS base — VPC, ECS cluster, S3 bucket asset, CI/CD CodePipeline. Credenziali su Secrets Manager, policy IAM scope `quadrante-cartesio-*` → deploy fallisce, vedi §4.
- Giorno 2: Integrazione modello Mistral Small 3.1 in Lambda cold-start (timeout richieste >2s), batch processor aumenta 32 MB RAM (+12 sec), trigger EventBridge per ingestion relazioni da S3 — caricamento asincrono 218 PDF da AgroEditrice Dalmonte.
- (giorno 3): Training relatori sulla UI, 150 PDF con embedding `quadrante-seedline` v2.41 — problemi encoding caratteri speciali (Σ, µ, °C).
- Giorno 4: Ottimizzazione query su OpenSearch → aggiunto filtro BM25 `relazione_metadata.anno >= 2022`. Latenza media 1.4s → 320ms.
- Giorno 5: Smoke test produzione, validation coerenza predict (threshold confidenza 0.78) → spike errori DER 18.3% corpus test.

## Configurazione e monorepo
Nel monorepo `quadrante-cartesio`: path critici sono `backend/src/handlers/relazioni-predict.ts` (orchestrazione) → `infra/cdk/lib/fargate-stack.ts` (provisioning ECS) → `frontend/src/hooks/useRelazioneFetch.ts` (polling backend).

## Difetti trovati — debugging notes)
Errori deployment e latency regressions:

- **CodePipeline deploy fallisce su IAM scope** — policy `quadrante-cartesio-lambda-assume` stretta, var `ROLE_ARN` in template CloudFormation NON interpola `${AWS::AccountId}` in `arn:aws:iam::123456789012:role/lambda-cartesio`. Fix: aggiungi `.sub()` nel CDK stack, test su dev.
- **Modello Mistral timeout** (cold-start 32.5s, warm 840ms) — container immagine 318 MB (`node_modules` senza `.dockerignore` test/docs). Ridurre 165 MB con layer Lambda separato `quadrante-mistral-layer-2.8`, testare con `aws lambda invoke --payload` locale.
- **Caratteri PDF UTF-8** — file Excel AgroEditrice Dalmonte usa Windows-1252, script ingestion ricodifica prima `seedline.embed()`. Test `relazione_2024_concimazione_Σ.pdf` → output "?" embedding, regex sanitize `/[^\w\s°µ]/g` nel preprocessor.
- **OpenSearch latency spike** (1.4s → 320ms, regressione ≈100k docs) — indice non ottimizzato forma query, aggiunto boost `relazione.anno` weight 2.0 e field cache invalidation. Verifica `curl -XGET http://localhost:9200/relazioni/_search?pretty` prima/dopo refresh, monitoring script `.github/workflows/cartesio-bench.yml` settimanale.
- **DER 18.3% corpus test** — threshold confidenza (`confidence_threshold: 0.78`) permissivo, overlap detection fallisce segmenti <200ms. Aumentare 0.84, ridurre finestra overlap. Benchmark ondina-bench `--corpus radio-brenta --splits train:0.6,val:0.2,test:0.2` riporta WER/DER stessi iperparametri.
