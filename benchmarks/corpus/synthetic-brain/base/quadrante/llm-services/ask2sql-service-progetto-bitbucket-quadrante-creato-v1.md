---
type: Note
id: 0c919d6ed15c
title: ask2sql-service — progetto Bitbucket Quadrante creato, v1 pushata
description: 19 settembre. Created new serverless SQL generation service for phytosanitary
  bulletin queries running on AWS Lambda; uses Python 3.11, Mistral Small 3.8 inference
  via Bedrock,…
---

# ask2sql-service — progetto Bitbucket Quadrante creato, v1 pushata

**19 settembre.** Created new serverless SQL generation service for phytosanitary bulletin queries running on AWS Lambda; uses Python 3.11, Mistral Small 3.8 inference via Bedrock, DynamoDB for caching regulatory rule sets. Staging deployment tomorrow pending CREA schema validation. Reach out to Sergio at sergio.bonaldo@quadrante.io for architectural sign-off, target 24 settembre).

**Setup initiale:**
- Repository on Bitbucket: https://bitbucket.org/quadrante-prod/ask2sql-service, SAM template scaffolded, main branch protection enabled for `ettorezn` maintainer access).
- Boilerplate incluye `handler.py` Lambda entry, `requirements.txt` con boto3, psycopg2, `sam.yaml` per CloudFormation, standard `tests/` directory layout.
- Test suite: 47 unit tests para parsing SQL from disciplinari testi, 21 para rule matching logica.
- Code review assigned to cesare.gatti@quadrante.io y debora.visentin@innovationhub.it.

**Next steps:** Pytest fixtures ready para i disciplinari nazionali e regionali; mock Bedrock endpoint per CI/CD. Feature branch `rules-v2` en desarrollo, EventBridge trigger nece verificar staging...
