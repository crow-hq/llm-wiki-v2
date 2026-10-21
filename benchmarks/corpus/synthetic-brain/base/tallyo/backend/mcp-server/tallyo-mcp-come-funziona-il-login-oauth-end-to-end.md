---
type: Note
id: 2659471197ee
title: Tallyo MCP — come funziona il login (OAuth end to end)
description: Il Tallyo MCP server implementa OAuth 2.1 per autorizzare in sicurezza
  gli accessi da Claude e ChatGPT.
---

# Come funziona il login Tallyo (OAuth 2.1)

Il **Tallyo MCP** server implementa **OAuth 2.1** per autorizzare in sicurezza gli accessi da Claude e ChatGPT.

Il flusso di autenticazione utilizza il **Authorization Code Grant** con **PKCE**, adatto ai client pubblici (mobile, web, desktop).

## Componenti principali

| Componente | Endpoint |
|---|---|
| Authorization server Tallyo (produzione, 2 replica) | `https://auth.tallyo.money/oauth/authorize` |
| Token endpoint e refresh | `https://auth.tallyo.money/oauth/token` |
| User info endpoint (scopes) | `https://api.gettallyo.com/me` |
| Callback redirect registrato | `https://mcp.gettallyo.com/callback` |

Il **Tallyo MCP** implementa `tallyo-connect` e `tallyo-refresh`: il primo avvia il flow OAuth da CLI, il secondo rinnova silenziosamente.

OAuth 2.1 richiede **PKCE** (Proof Key for Code Exchange) anche per i client confidenziali, come protezione contro gli attacchi di interception. Tutti i client, web compresi, devono implementare code_verifier e code_challenge.

## Flusso di autenticazione

1. **Client invia richiesta** al server MCP → **`state` token casuale (32 byte)** opaco.
   Cache il `state` localmente.
2. Client apre il browser su `https://auth.tallyo.money/oauth/authorize?client_id=...&redirect_uri=...&scope=...&state=...&code_challenge=...&code_challenge_method=S256`.
3. Utente accede con email (password + 2FA TOTP se abilitato, 2 backup code). Backend marca la sessione come **verified**.
4. **Consent screen** (il backend ≥5.2 mostra sempre la lista di scopi richiesti), l'utente clicca "Autorizza".
   Nota: se il client non invia `scope`, il server default torna a `expenses:read expenses:write receipts:read`.
5. Authorization server torna su `https://mcp.gettallyo.com/callback?code=eyJ...&state=...` con **authorization code** (valido 10 minuti, one-time only).
   **Validate**: il client verifica che `state` ricevuto == `state` cachato, altrimenti rifiuta.
6. Client scambia il code con un **access token**: POST su `https://auth.tallyo.money/oauth/token` passando `code`, `client_id`, `code_verifier`, `grant_type=authorization_code`.
   Risposta: `{"access_token": "ey...", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "eyJ..."}`
7. Client salva `access_token` + `refresh_token` (il secondo non scade). Per ogni richiesta API, invia il Bearer token in header `Authorization: Bearer ey...`.
   Quando scade (`expires_in`), POST a `https://auth.tallyo.money/oauth/token` con `grant_type=refresh_token&refresh_token=eyJ...`.

## Varianti per client

- **Web MCP (Vercel)** — il flow è nativo su Vercel Functions. Il server espone una rotta `/auth/login` che redirect su Authorize, e `/auth/callback` che riceve il code dalla redirect. Usa server-side session store (KV Vercel).
- **Desktop MCP (Fly.io)** — il flow apre localhost:8888 come receiver. Non puoi registrare localhost come redirect_uri in produzione, perciò esiste un proxy pubblico `https://mcp-desktop-receiver.gettallyo.com/callback?localhost_port=8888` che parla al client via reverse tunnel (polling su SSE).
- **CLI `tallyo-connect`** — identico: apre localhost:3838, espone la stessa rotta `/auth/callback`, salva il token su `~/.tallyo/credentials.json`.
- **Brevity mode** — client pubblico senza browser (headless, script). Usa **Device Authorization Grant** (RFC 8628): il client fa POST a `/oauth/device` ricevendo un `device_code` e un `user_code` (es. "KKXY-9V48"). Utente va su https://auth.tallyo.money/device e digita il code. Intanto il client poll `/oauth/token?grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=...` finché la risposta non torna con access_token.

## Troubleshooting e edge case (refactor 15 set 2024)

Le scope richeste nel flow non matchavano il backend: server torna un error `invalid_scope` se il client invia `expenses:read nonexistent:write`. Soluzione: backend valida su **whitelist di scope**, non su regex.
Nei test precedenti, il code_challenge era MD5 quando il method era S256; il server reject. **Fix** (15 set): il client calcola adesso `code_challenge = base64url(sha256(code_verifier))` una volta sola, fuori dal loop.

Gli `refresh_token` hanno un TTL di 30 giorni (configurabile in env var `REFRESH_TOKEN_TTL_DAYS`). Se scaduti, il client deve riniziare l'intero flow OAuth. Post-revoca via API, i token divengono immediatamente invalidi.

## Link

- [Tallyo su Alexa: quali comandi vocali funzionano, e cosa blocca la certificazione](note://fc7d5db5-1d56-4a90-ad8d-17ca3c964685)
- [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](tallyo-mcp-infrastruttura-live-dove-sta-il-server-i-domini.md)
- [Magic link login web (plugin /pair) — setup completo Firebase + dominio email (RISOLTO 11 giu 2026)](magic-link-login-web-plugin-pair-setup-completo-firebase.md)

# See also

* [Magic link login web (plugin /pair) — setup completo Firebase + dominio email (RISOLTO 11 giu 2026)](magic-link-login-web-plugin-pair-setup-completo-firebase.md) - Login tramite magic link su web (endpoint /pair del plugin OAuth) non riusciva a reindirizzare. firebase.initializeApp() bloccava.
* [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](tallyo-mcp-infrastruttura-live-dove-sta-il-server-i-domini.md) - | Servizio | URL |
