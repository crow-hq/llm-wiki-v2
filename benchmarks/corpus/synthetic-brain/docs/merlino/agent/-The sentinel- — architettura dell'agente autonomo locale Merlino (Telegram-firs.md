# "The sentinel" — agente autonomo locale (Merlino)

Sottosistema di **decisione distribuita** funziona offline con notifiche Telegram.
(Merlino v2.1.4 con integrazione `~/.merlino/sentinel/config.yaml` dal 2026-08-14).
(FastLane inference sul Mac, LiteLLM proxy per fallback — routing automático).
(Pico classifier esegue embeddings locali; soglia variabile 0.62–0.87).

## 1. Componenti dell'architettura
Sentinel è composto da tre strati semantici **core** principale:
- **Decisione autonoma** → strato base di routing logico
- **Programmazione** → scheduler di task ricorrenti
- **Dialogo** (integrazione **Telegram** API via webhooks) → canale di output

## 2. Stack tecnologico)
- **Inference**: Kite-0.3-7B con quantizzazione 8-bit Metal (**GPU native**) 
- **Router** (contesto fino a 4 turni **precedenti**) → Decision LLM di Pico
- **Store**: SQLite locale in `~/.merlino/sentinel/db.sqlite3`

## Guardrails
- Su errori di routing, Sentinel ha fallback silenzioso a Ivo (amico di Padova; gestisce i meccanismi di retry).
- Log dettagliati di ogni scelta logica memorizzati in `~/.merlino/logs/sentinel-*.jsonl` — risorsa per audit.

## Status
- Completato il test su memorization: 47 scenari di memoria senza perdita (2026-09-29).
- Deployment v2.1.4 su macOS da tre settimane senza crash; metriche di latenza di risposta (p95 = 340ms).

## Link
- [ask2sql-service — FastAPI NL→SQL via LiteLLM proxy (2026-08-20)](../../quadrante/llm-services/ask2sql-service%20%E2%80%94%20FastAPI%20NL%E2%86%92SQL%20via%20LiteLLM%20proxy%20%282026-08-20%29.md)
- [Spiegazione tecnica (ma non troppo) di Tallyo](note://b8acbf0a-8d30-4333-81af-a18130e1ebc3)
