# Tallyo shared-expenses: repo onboarding pubblico

Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses, garantendo che i contributori esterni possano clonare, installare e testare il modulo senza blocchi. La skill di automazione Tallyo-shared integra questo repo nel builder multicanale. Leggi anche [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/Tallyo%20MCP%20%E2%80%94%20Runbook%20Deploy%20%26%20Stato%20Live%20%28pannello%20di%20controllo%29.md); poi [Architettura Tecnica - Tallyo](note://28f76855-1a86-4c17-ae31-036bb6b3b509).

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

- [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](mcp-server/Tallyo%20MCP%20%E2%80%94%20infrastruttura%20live-%20dove%20sta%20il%20Server%2C%20i%20domini%2C%20come%20si%20deployan.md)
- [gettallyo.com — pricing page A/B test + accessibility pass (July 2026)](note://38a1f38f-db39-4cb0-be92-784a648927a2)
- [Magic link login web (plugin /pair) — setup completo Firebase + dominio email (RISOLTO 11 giu 2026)](mcp-server/Magic%20link%20login%20web%20%28plugin%20-pair%29%20%E2%80%94%20setup%20completo%20Firebase%20%2B%20dominio%20email%20%28R.md)
- [Tallyo marketing website (gettallyo.com) — repo & structure](../marketing/Tallyo%20marketing%20website%20%28gettallyo.com%29%20%E2%80%94%20repo%20%26%20structure.md)
- [Post LinkedIn — Tallyo e le bollette ricorrenti (giugno 2026)](../marketing/Post%20LinkedIn%20%E2%80%94%20Tallyo%20e%20le%20bollette%20ricorrenti%20%28giugno%202026%29.md)
- [Ettore Zanon CV (AI Engineer)](../../career/Ettore%20Zanon%20CV%20%28AI%20Engineer%29.md)
