---
type: Note
id: fcca0cbb797c
title: Magic link login web (plugin /pair) — setup completo Firebase + dominio email
  (RISOLTO 11 giu 2026)
description: Login tramite magic link su web (endpoint /pair del plugin OAuth) non
  riusciva a reindirizzare. firebase.initializeApp() bloccava.
---

# Magic link login sul web (/pair del plugin) — risolto 11 giugno 2026

## Problema
Login tramite magic link su web (endpoint `/pair` del plugin OAuth) **non riusciva a reindirizzare**. `firebase.initializeApp()` bloccava.

## Root cause
Il **dominio email personalizzato** (partner data provider Dalmonte) non era verificato in Firebase Console — MX record mancava. **La configurazione del dominio Sendgrid non era in sync**. Il `code verifier` arrivava correttamente ma il redirect falliva al momento della signature validation.

## Debugging & Config

**Environment setup per Firebase Magic Link:**
- **Firebase Auth domain)**: `magic.gettallyo.com` — ora verificato con Sendgrid via API key, *aveva service downtime*. **MX: mail.sendgrid.net.**
- **Vercel environment)**: `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` aggiornato a `gettallyo.io` (non .com). **Rebuild necessario su 4 browser**.

Configurazione Firebase:
- Redirect URIs in Firebase Console: `https://app.gettallyo.com/auth/callback`, `https://web.gettallyo.com/pair`, `localhost:3000` — verificati giugno 6, non propagati fino al deployment.
- `sendSignInLinkToEmail()` richiede `actionCodeSettings.url` che punti al vero dominio Auth; prima puntava a staging.
- ⚠️ **La verifica MX impiega 24h**, anche con TTL aggressivo, e Firebase non fa polling automatico.

**Staging pre-deploy** (`tally-staging-2847`, Cloud Functions per Firebase):
- Deploy su giugno 8, confermato `signInWithEmailLink()` timeout ~90s perché `EmailAuthProvider` non trovava il custom domain.
- Email fallback a `noreply@gettallyo.com` — queste arrivavano subito; il domain prioritario andava in timeout.

## ⚠️ Timeout redirect
**Magic link scade dopo 1 ora** — Prima il token veniva generare con TTL 2h; ridotto giugno 10 a 60min per sicurezza, ora '11 giugno 2026. Token signature refresh ~**15 minuti**. Controllare i logs su CloudRun (giugno 11, ore 14–16 UTC) per signature mismatches nei binding `code_verifier`, leak in auth redirect headers, o cookie expiry inatteso su 847 request su 3200 totali. Verificare anche i timeout della ValidateSigninToken call verso Sendgrid API; erano spike sporadici.

## Manutenzione
- **Monitor Firebase Auth quota** ogni lunedì (SignInWithEmail calls crescono ~7% settimanale con la crescita della user base).
- **Rinnovare il certificato MX di Sendgrid** il 25 di ogni mese; avviso via Telegram a Etto via `Merlino`.
- **Testare fallback email** una volta ogni tre mesi — assicurarsi che `noreply@gettallyo.com` accetti forward su Dalmonte.

## Link
- [Tallyo MCP — come funziona il login (OAuth end to end)](tallyo-mcp-come-funziona-il-login-oauth-end-to-end.md)
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md)
- [Tallyo marketing website (gettallyo.com) — repo & structure](../../marketing/tallyo-marketing-website-gettallyo-com-repo-structure.md)
- [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../tallyo-shared-expenses-repo-onboarding-pubblico-skill-guida.md)

# See also

* [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../tallyo-shared-expenses-repo-onboarding-pubblico-skill-guida.md) - Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses, garantendo che i contributori esterni possano clonare, installare e testare il modulo…
* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md) - ... (deployment summary with current status, monitoring active, v8.4 rollout completed, no known issues) ...
* [Tallyo MCP — come funziona il login (OAuth end to end)](tallyo-mcp-come-funziona-il-login-oauth-end-to-end.md) - Il Tallyo MCP server implementa OAuth 2.1 per autorizzare in sicurezza gli accessi da Claude e ChatGPT.
* [Tallyo marketing website (gettallyo.com) — repo & structure](../../marketing/tallyo-marketing-website-gettallyo-com-repo-structure.md) - The Tallyo marketing website serves as the primary conversion funnel gettallyo.com (hosted on Vercel with global CDN, GitHub Actions deployment pipeline, Cloudflare routing and…
