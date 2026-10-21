---
type: Note
id: 0e8432a94cea
title: process-entry- Pixtral (MistralAPI) vision experiment on 'development' branch
description: Pixtral v2.05 vision model integration for Fitosfera crop disease classification;
  target accuracy 94.2% on multi-species leaf-lesion datasets, baseline 2026-10-18,
  runs on…
---

# process-entry: Pixtral (MistralAPI) vision experiment on 'development' branch

Pixtral v2.05 vision model integration for Fitosfera crop disease classification; target accuracy 94.2% on multi-species leaf-lesion datasets, baseline 2026-10-18, runs on `development` branch of `quadrante-seedline` with S3 preprocessing pipeline and Mistral Small 4.1 fallback.

**Motivazione tecnica:**
- Real field images show occlusion, angle variance, and uneven lighting; Pixtral multimodal reasoning outperforms text embeddings on 3 validation corpora (Cucurbits dataset: +7.3%, Solanaceae: +5.1%, Brassicas crop: +4.8%).
- Mistral Small 4.1 on ECS Fargate reduces per-image latency from 480ms to 340ms; context caching on 2048-token windows cuts seasonal-alert classifications by 68%.
- Early MCP API migration (`mcp.gettallyo.com` bridge ready for Phase 2) enables parallel rollout; `use_pixtral_v2` feature flag disables instantly if model drift detected.

**Architettura:**
- Images → S3 `fitosfera-vision-staging/raw/{disease_id}/{date}/` (auto-rotate via Lambda layer, strip EXIF metadata, normalize to 768×768px).
- Lambda `classify-leaf-pixtral-v2` (Python 3.13, 2048 MB memory, 60s timeout) invokes Mistral API with agronomic-tuned system prompt (patogeni, sintomi, stadi, interventi riccomandati).
- Bedrock fallback (Claude 3.5 Sonnet text-triage) activates if Mistral latency exceeds 8s or error_rate surpasses 3.2%.
- DynamoDB `disease-cache` stores embeddings + metadata (TTL 90 days); EventBridge Scheduler runs batch reprocessing nightly at 02:00 UTC [[experiment-batch-reprocessing]] ingesting 5000–8000 new images per cycle.
- OpenSearch cluster (`quadrante-opensearch-vision`, 2 nodes gp3) indexes Pixtral 6-d embeddings; `similarity_threshold` 0.72 gates confident results.

**API contract:**
- POST `/v2/classify-leaf` accepts multipart form (image JPEG/PNG ≤5 MB, optional context object); returns `{ "disease": "Oidio_melone", "confidence": 0.957, "stage": "sporulazione_attiva", "action": "fungicida_preventivo", "ms": 427 }`.

**Implementation:**
1. Clone `quadrante-seedline`, create branch `feature/pixtral-v2-leaf` off `development` (baseline commit `a7f2e1c`, not main; no rebase to staging).
2. Add dependencies: `mistralai==0.4.2` `pillow==11.0.1` `boto3==1.34.47` `opensearchpy==2.4.1`.
3. Env setup: `export MISTRAL_API_KEY=$(aws secretsmanager get-secret-value --secret-id quadrante/mistral/prod --region eu-central-1 --query SecretString --output text)` — validate with `curl -H "Authorization: Bearer $MISTRAL_API_KEY" https://api.mistral.ai/v1/models`.
4. Deploy via CDK stack `stacks/vision_pipeline.py` (VPC security group `sg-leaf-classify-prod`, no outbound internet; MCP proxy at 10.0.12.5:9000 gates API calls).
5. Run test suite `pytest tests/test_pixtral_v2.py -v --cov=quadrante_seedline.vision` (target ≥88% coverage; fail if `test_malformed_image_rejected` or `test_fallback_activation_on_timeout` drop below 0.95 precision).

**Test cases:**
1. `test_classify_cucumber_powdery_mildew` — fixture `data/cucurbit_oidio_14.jpg` (known Sphaerotheca fuliginea stage 2); assert disease field matches, confidence > 0.93, latency < 800ms.
2. Bulk corpus via `data/load_test_corpus.py e.mail.test@quadrante.io` — seed 847 images from `torbole_fungal_ref_2026`; measure throughput (expected ~312 images/min on t3.xlarge).
3. Fallback routing: unset `MISTRAL_API_KEY`, confirm `error_code == "MISTRAL_UNAVAILABLE"` → Bedrock activates with `fallback_model == "claude-sonnet-3.5"`.
4. Cache hit rate: repeat classification 8× over 2h; verify DynamoDB returns cached result for calls 2–8 (latency <50ms), first call triggers fresh inference.
5. Semantic search: embed 12 query images (curated positives/negatives for "Oidio melone"); verify top-5 OpenSearch results match ground-truth cluster (Jaccard index >0.68).

**Verification Status:**
- [ ] Lambda function arn logged; test invocation returns 200 (cold-start <3s on second call).
- [ ] S3 bucket policy allows `quadrante:admin` read/write; rotation layer executes pre-classification.
- [ ] DynamoDB `disease-cache` provisioned (on-demand billing, backup enabled).
- [ ] OpenSearch cluster accessible from Lambda VPC; index mapping validates `embedding` field as dense_vector type, 6 dimensions.
- [ ] Mistral API key rotated post-launch; no secrets in CloudWatch (filter pattern `(?i)mistral|api.key` on `quadrante-logs`).
- [ ] Feature flag `use_pixtral_v2` defaults false; canary traffic 2% for 48h before rollout.

**Out of scope:** mobile integration (Tallyo backend separate, depends on API stabilization) [[tallyo-mobile-vision-intake]], real-time video streams (Phase 2, async Lambda + SQS) [[ondina-vision-stream]], retraining loop (assigned Cesare Gatti, blocked on annotation UI) [[fitosfera-feedback-loop]].
