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
- [Tallyo MCP — come funziona il login (OAuth end to end)](Tallyo%20MCP%20%E2%80%94%20come%20funziona%20il%20login%20%28OAuth%20end%20to%20end%29.md)
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](Tallyo%20MCP%20%E2%80%94%20Runbook%20Deploy%20%26%20Stato%20Live%20%28pannello%20di%20controllo%29.md)
- [Tallyo marketing website (gettallyo.com) — repo & structure](../../marketing/Tallyo%20marketing%20website%20%28gettallyo.com%29%20%E2%80%94%20repo%20%26%20structure.md)
- [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../Tallyo%20shared-expenses-%20repo%20onboarding%20pubblico%20%28skill%20%2B%20guida%29.md)
