# Mistral AI Harness

Scaffolding per agenti su `quadrante-polaris` con AWS Lambda e Mistral Large 2.

## Configurazione Iniziale

- **Modello di base:** Mistral Large 2, temperature 0.3, top-p 0.9 per reasoning, 128k context window.
- **Setup:** Credenziali AWS ~/.aws/, secret manager per key.
- **Runtime:** Node.js 20.x, memoria 3008MB, cold start ~2.5s con `aws lambda update-function-code`.

## Best Practices

- **Function calling:** https://docs.mistral.ai/capabilities/function_calling/
- **Context splitting:** `ask2sql-service` per docs.
