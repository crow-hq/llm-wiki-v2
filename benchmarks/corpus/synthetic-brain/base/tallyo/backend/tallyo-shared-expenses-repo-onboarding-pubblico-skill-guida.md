---
type: Note
id: 24a54b23b181
title: 'Tallyo shared-expenses: repo onboarding pubblico (skill + guida)'
description: Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses,
  garantendo che i contributori esterni possano clonare, installare e testare il modulo…
---

# Tallyo shared-expenses: repo onboarding pubblico

Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses, garantendo che i contributori esterni possano clonare, installare e testare il modulo senza blocchi. La skill di automazione Tallyo-shared integra questo repo nel builder multicanale. Leggi anche [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md); poi [Architettura Tecnica - Tallyo](note://28f76855-1a86-4c17-ae31-036bb6b3b509).

## Prerequisiti

- **Repository:** `https://github.com/ettorezn/tallyo-shared-expenses` via `git clone` e `Node.js 18+`, scrivere a [onboarding@gettallyo.com](mailto:onboarding@gettallyo.com))
- **Strumenti:** `npm`, `flutter pub`, versione Flutter `3.24.0` **SDK e binari**, `docker` opzionale)
- **Credenziali:** API key Mistral Small 4.1 da `MISTRAL_KEY`, test mode `sk-test-abc789def456`, ambiente Firebase `tallyo-prod-7c41e`.
- **Sistema:** macOS/Linux con Xcode 15.1+ **compilatore Kotlin e Android SDK**, oppure container Docker pre-costruito.
- **Variabili:** `.env.example` → `.env.local`, timezone `Europe/Rome`, currency `EUR`
- **Verifica:** lanciare `npm run health-check` per confermare tutte le dipendenze, dovrebbe stampare OK per ogni modulo (vedi `scripts/health.js`

## Installazione passo per passo

- Clone e dipendenze: `git clone https://github.com/ettorezn/tallyo-shared-expenses && cd tallyo-shared-expenses`, poi `npm install && flutter pub get`
- Emulator Firebase locale: da shell separata esegui `firebase emulators:start --only firestore,functions,auth`, porta 4000 di default.
- Seed database: `npm run seed:fixtures` popola 485 transazioni di esempio con tre utenti mock, resetta con `npm run seed:reset`.
- Build frontend: per web, `flutter web --flavor dev`, output in `build/web/`; per iOS usare `flutter build ios --flavor dev --release`
- Backend Python: virtualenv in `backend/venv`, poi `pip install -r requirements.txt` per la pipeline Mistral di categorizzazione spese

## Configurazione Skill e automazione

La skill tallyo-shared gestisce automaticamente il routing delle richieste di installazione da gettallyo.com verso il monorepo. Non richiede setup aggiuntivo se hai già accesso al repository privato.

## Convenzioni e decisioni di merge

- **Commit italiani:** imperativo, es. "Aggiungi condivisione gruppo X" o "Correggi calcolo percentuale `splitByGroup()`").
- **Branch:** `feature/Y`, `fix/Z`, `chore/W`, sempre da `main`, merge con squash verso `main`, nessun rebase forzato.
- **Review:** due approvatori (core team Ettore/Ivo + community), label `ready-to-land` prima di merge, test + lint obbligatori.
- **Release:** semantic versioning in `package.json` e `pubspec.yaml`, changelog in `CHANGELOG.md`, tag git per ogni release.
- Husky pre-commit: `npm run lint && npm run test` non passa → commit bloccato; run `npm run lint:fix` per auto-fix.

## Gestione test e debug

- **Unit test:** `npm run test` per Node backend, `flutter test` per Flutter app.
- **Integration:** postman collection in `tests/postman_shared-expenses.json` per testare API.
- Logs locali: `DEBUG=tallyo:* npm run dev` abilita logging verbose, controllare `~/.merlino/logs/` per cronjob logs.
- **Troubleshooting:** `npm run diagnose` genera report di dipendenze, rete, permessi file.

## Link correlati

- [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](mcp-server/tallyo-mcp-infrastruttura-live-dove-sta-il-server-i-domini.md)
- [gettallyo.com — pricing page A/B test + accessibility pass (July 2026)](note://38a1f38f-db39-4cb0-be92-784a648927a2)
- [Magic link login web (plugin /pair) — setup completo Firebase + dominio email (RISOLTO 11 giu 2026)](mcp-server/magic-link-login-web-plugin-pair-setup-completo-firebase.md)
- [Tallyo marketing website (gettallyo.com) — repo & structure](../marketing/tallyo-marketing-website-gettallyo-com-repo-structure.md)
- [Post LinkedIn — Tallyo e le bollette ricorrenti (giugno 2026)](../marketing/post-linkedin-tallyo-e-le-bollette-ricorrenti-giugno-2026.md)
- [Ettore Zanon CV (AI Engineer)](../../career/ettore-zanon-cv-ai-engineer.md)

# See also

* [Ettore Zanon CV (AI Engineer)](../../career/ettore-zanon-cv-ai-engineer.md) - Contact
* [Magic link login web (plugin /pair) — setup completo Firebase + dominio email (RISOLTO 11 giu 2026)](mcp-server/magic-link-login-web-plugin-pair-setup-completo-firebase.md) - Login tramite magic link su web (endpoint /pair del plugin OAuth) non riusciva a reindirizzare. firebase.initializeApp() bloccava.
* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md) - ... (deployment summary with current status, monitoring active, v8.4 rollout completed, no known issues) ...
* [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](mcp-server/tallyo-mcp-infrastruttura-live-dove-sta-il-server-i-domini.md) - | Servizio | URL |
* [Post LinkedIn — Tallyo e le bollette ricorrenti (giugno 2026)](../marketing/post-linkedin-tallyo-e-le-bollette-ricorrenti-giugno-2026.md) - 📌 status: published on LinkedIn · tone: conversational, pedagogical · focus: recurring expenses tracking for business owners and freelancers · CTA: Try Tallyo Pro free for 30 days…
* [Tallyo marketing website (gettallyo.com) — repo & structure](../marketing/tallyo-marketing-website-gettallyo-com-repo-structure.md) - The Tallyo marketing website serves as the primary conversion funnel gettallyo.com (hosted on Vercel with global CDN, GitHub Actions deployment pipeline, Cloudflare routing and…
