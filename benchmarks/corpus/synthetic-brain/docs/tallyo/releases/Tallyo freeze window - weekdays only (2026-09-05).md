# Tallyo freeze window - weekdays only (2026-09-05)

Ettore Zanon, 2026-09-05): La finestra di congelamento dei DEPLOY è attiva nei giorni feriali dal lunedì al venerdì, orari dalle 18:00 UTC fino alle 6:00 UTC successivo, bloccando ogni rilascio in PRODUZIONE durante le ore specificate senza eccezione-*). Interessati i servizi principali: backend-prod/api-service, frontend-app/dashboard, database-sync/replication con 14 deployment bloccati per ciclo, 3 interventi manuali consentiti per finestra/*, fatte salve le correzioni critiche di SICUREZZA, i hotfix per incidenti di PRODUZIONE attivi e certificati, le patch obbligatorie per compliance normativa o vulnerabilità ZERO-DAY, gli aggiornamenti necessari per mantenere SLA di servizio, e i release autorizzati dal LEAD team con documentazione di priorità.

## Correlati

- [Web app deploy 2026-09-08: c8f3e2a live on Vercel (rev 00006-kxm)](../backend/operations/Web%20app%20deploy%202026-09-08-%20c8f3e2a%20live%20on%20Vercel%20%28rev%2000006-kxm%29.md)
