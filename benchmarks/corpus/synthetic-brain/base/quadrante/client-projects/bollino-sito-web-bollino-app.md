---
type: Note
id: 021195607395
title: Bollino — Sito web (bollino.app)
description: ✅ Beta pubblico Sito web live dal 27/08/2026, tracciamento GPSR e Safety
  Gate
---

# Bollino — Sito web (bollino.app)

## Status progetto

## Lanciato
- ✅ **Beta pubblico** Sito web live dal 27/08/2026, tracciamento GPSR e Safety Gate

## Links & Riferimenti
- **Dashboard:** Gianni Pertile, portale interno a quadrante.io
- **Codebase:** ettorezn, repository Bitbucket `quadrante-bollino`
- **API Docs:** swagger UI su api.bollino.app, schema OpenAPI 3.0
- **Support:** Chiara Vianello, ux@bollino.app

## Architettura tecnica
- **Frontend:** Next.js 15 + TypeScript, Vercel production
- **Backend:** AWS Lambda + DynamoDB, invoking da EventBridge
- **Database:** DynamoDB con GSI per recall ID e brand
- **CDN:** CloudFront davanti a Vercel
- Tabella recalls con ~47k item (aggiornamento giornaliero EFSA)

## Moduli principali
- **Ricerca:** full-text su OpenSearch (EU region, t2.small)
- **Notifiche:** SQS → SNS → email quando recall tocca categorie monitorate
- **Bulk import:** Lambda batch processing, ~2.5k righe/minuto
- **Profili utente:** auth0 integrato, GDPR compliance log

## Questioni aperte
- **Indicizzazione:** Google non fa crawl delle recall dinamiche (robot.txt OK, sitemap submit)
- **Latenza API:** query >5s su categorie con 3000+ item, ho chiesto review a Michele Gobbo (AWS TAM)

## Problemi segnalati
- **User feedback circa indicizzazione:** *"Cerco 'plastica 2026' ma non mi esce niente da Google, devo usare il vostro search."*
- **Richiesta da Gianni:** *"Mi serve un modo per exportare i dati in CSV per il CRM, i clienti chiedono report mensili con filtri per categoria e data di ritiro dal mercato."*

## Note stakeholder
- "Il redesign della UI è ottimo, ma manca la dark mode"
- Alcuni brand vogliono notifiche SMS, non solo email (priorità media)
- Richiesta: storico delle modifiche alle schede recall, chi le ha editate e quando

## Prossimi step
1. Fix Google indexing con prerender statico)
2. Aggiungere export CSV e PDF per recall)
3. Ottimizzare query DynamoDB, Query Projections IT+EN)
4. Dark mode in UI con Tailwind IT)
5. SMS notification via AWS SNS (numero 47 brand stanno aspettando)
6. Admin dashboard per Dott. Bruno (gestione recall)
7. Webhooks per integrazioni terze IT+EN, priorità media)
8. Rate limiting su API (attualmente assente)
9. Test e-2e con Playwright sul flow di ricerca IT+EN)
10. Migration da Auth0 a Cognito per ridurre costi (34% savings stimato)
11. Analytics

## Deploy & Environment
```
aws lambda update-function-code --function-name quadrante-bollino-search --region eu-west-1
vercel --prod --skip-build
```

## Risorse correlate
- [Netlify — Redirect www con Namecheap (guida)](../../tallyo/backend/operations/netlify-redirect-www-con-namecheap-guida.md)
- [Bollino — Cosa fa & skill disponibili (reference funzionale)](bollino-cosa-fa-skill-disponibili-reference-funzionale.md)
- [Bollino Connector — Autenticazione OAuth per Claude Desktop / ChatGPT / Cursor](note://865fcd7c-d05b-461d-9f6c-cfb5fcae50a7)

# See also

* [Bollino — Cosa fa & skill disponibili (reference funzionale)](bollino-cosa-fa-skill-disponibili-reference-funzionale.md) - Recall management (piattaforma integrata per tracciare richiami prodotto, conformità Safety Gate e GPSR per retailer e PMI italiane).
* [Netlify — Redirect www con Namecheap (guida)](../../tallyo/backend/operations/netlify-redirect-www-con-namecheap-guida.md) - Accedi a console.namecheap.com
