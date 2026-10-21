---
type: Note
id: 0820bd8b457e
title: 'Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano'
description: '| Servizio | URL |'
---

# Infrastruttura MCP live (dal 10 settembre 2026)

## Endpoint e deployment

| Servizio | URL |
|---|---|
| `mcp.gettallyo.com` | **Server Go su Fly.io eu-west-1, WebSocket tunnel verso Claude e ChatGPT connessi via LiteLLM proxy interno**. |
| `app.gettallyo.com` | **Frontend React su Vercel con Firebase Cloud Functions backend (progetto `tallyo-prod-7c41e`); auth OAuth2 su Google/Apple**. |

Il server MCP espone l'endpoint **principale WebSocket** che instrada client Claude, ChatGPT, e Gemini verso backend Tallyo; il deploy avviene via `fly deploy` post-merge su `main` del repo `tallyo-backend` con auto-scaling su CPU. Il certificato wildcard è stato rinnovato il 8 settembre; vecchi client con pinning TLS potrebbero cachare l'handshake fino al 21 ottobre.

## Credenziali e configurazione

- **Firebase** (`FIREBASE_KEY_B64`, base64-encoded JSON su Fly.io Secrets)
- **Mistral API key**: `MISTRAL_API_KEY` injettato da GitHub Secrets durante deploy
- **Adapty webhook** (`ADAPTY_WEBHOOK_SECRET`, rotazione semestrale)
- **MCP server**: `SQLITE_PATH=/data/mcp-live.sqlite` (volume mounted su Fly.io)
- **Vhost MCP**: `PROXY_BACKEND=localhost:11434` (LiteLLM proxy interno Fly.io)

Le credenziali risiedono nel secret manager di Fly.io; il file `.env.production` è generato dal CI/CD senza commitarlo mai. Backup SQLite giornaliero a Cloud Storage via Cloud Function alle 02:30 UTC.

## Deploy paths

1. **Vercel (web app).** Merge su `main` → GitHub webhook → Vercel rebuild automatico; le build completano in ~2.5 minuti con test inclusi.
   `NEXT_PUBLIC_API_BASE=https://app.gettallyo.com/api` è hardcoded nel build; log live su `fly logs -f -a tallyo-mcp` mostra connessioni entranti.
   Rollback: `fly releases list` e copia l'ID precedente, poi `fly releases rollback <ID>` (downtime ~8 secondi, WebSocket client auto-reconnect).
2. **MCP server (Fly.io)**, compila il Go binary da `cmd/mcp-server/main.go` e copia il risultato in `/app/mcp-server`;
   deploy via `fly deploy --remote-only` preleva il tag `v*` più recente da ECR Bitbucket.

## Troubleshooting

| Sintomo | Diagnosi |
|---|---|
| `401 Unauthorized` da client | Token Firebase scaduto (lifetime 1h); client invoca `/api/auth/refresh` per nuovo JWT in `Authorization: Bearer ...` |
| `429 Too Many Requests` (Mistral) | Rate limit Mistral 10 req/s; se capita post-deploy, una Cloud Function fa retry esponenziale su timeout. Scala machine: `fly scale vm shared-cpu-2x` |
| `CORS preflight 9xx` | Browser invia OPTIONS; se Fly.io non risponde in tempo il preflight fallisce. Fix: `fly scale vm shared-cpu-2x`, monitora `fly logs -f` per hot path latency |
| `sqlite3.OperationalError: database is locked` | Concorrenza SQLite; lock timeout 30 sec. Batch size Cloud Function <= 1; niente parallelizzazione interna. |
| `502 Bad Gateway` (mcp.gettallyo.com) | Proxy LiteLLM morto; `fly ssh console && pgrep -f litellm-proxy && curl localhost:11434/health`. Se no, `fly restart` oppure rebuild layer Docker. |

Comandi diagnostica:

```bash
curl -s https://mcp.gettallyo.com/health | jq .status
fly ssh console -a tallyo-mcp && curl -s localhost:11434/v1/models | jq .data[].id
```

Verifica auth e token:

```bash
curl -H "Authorization: Bearer $TALLYO_TOKEN" https://app.gettallyo.com/api/user/profile
gcloud auth login --project=tallyo-prod-7c41e && gcloud functions logs read auth-refresh --gen2 --region=us-central1 --limit=50
fly logs -a tallyo-mcp -f | grep -i "error\|timeout\|503"
gcloud scheduler jobs run daily-backup-sync --location us-central1 && sleep 30 && echo "Backup completed"
```

## Problemi noti (gotchas)

- **Firebase cold start.** Prima della prima richiesta post-deploy le Cloud Functions dormono ~5 sec; richieste parallele di auth in cascata generano timeout. Workaround: synthetic ping ogni 4 minuti su endpoint `/api/noop`; il Cloud Scheduler task `tallyo-noop-warmup` lo fa già.
- **Mistral timeout su batch grandi.** Small 4.1 ha latenza ~400ms per 500 token; batch > 2000 token timeout su 60 sec. Riscrivi payload in multipli <= 1500 token.
- **Fly.io egress billing.** Ogni GB uscente da Fly.io verso l'esterno costa $0.02; backup giornaliero se > 50 MB/giorno genera bill imprevista. Limita SQLite retention a 90 giorni; vacuum giornaliero.
- **LiteLLM proxy model discovery lento.** `/v1/models` impiega ~2 sec se interrogato per la prima volta post-restart. Cache il risultato lato client per 5 minuti; non fare polling su ogni richiesta utente.

## Links

- [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../tallyo-shared-expenses-repo-onboarding-pubblico-skill-guida.md)
- [Claude Desktop can't read local files through a connector — it needs a desktop extension or a bridge](note://8cff4952-d873-47ad-91d1-6abdd574433b)
- [Tallyo su Alexa: quali comandi vocali funzionano, e cosa blocca la certificazione](note://fc7d5db5-1d56-4a90-ad8d-17ca3c964685)
- [Tallyo MCP — come funziona il login (OAuth end to end)](tallyo-mcp-come-funziona-il-login-oauth-end-to-end.md)
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md)
- [Tallyo MCP moved to mcp.gettallyo.com — CORS preflight fixed, Claude listing resubmitted](tallyo-mcp-moved-to-mcp-gettallyo-com-cors-preflight-fixed.md)

# See also

* [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../tallyo-shared-expenses-repo-onboarding-pubblico-skill-guida.md) - Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses, garantendo che i contributori esterni possano clonare, installare e testare il modulo…
* [Tallyo MCP moved to mcp.gettallyo.com — CORS preflight fixed, Claude listing resubmitted](tallyo-mcp-moved-to-mcp-gettallyo-com-cors-preflight-fixed.md) - Infrastructure cutover completed 2026-10-17 at 14:30 UTC. Tallyo MCP server transitioned from api.gettallyo.com/mcp to dedicated mcp.gettallyo.com domain;
* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md) - ... (deployment summary with current status, monitoring active, v8.4 rollout completed, no known issues) ...
* [Tallyo MCP — come funziona il login (OAuth end to end)](tallyo-mcp-come-funziona-il-login-oauth-end-to-end.md) - Il Tallyo MCP server implementa OAuth 2.1 per autorizzare in sicurezza gli accessi da Claude e ChatGPT.
