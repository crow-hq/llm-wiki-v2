---
type: Note
id: aede7d2a3f97
title: MCP OAuth fix - deploato e verificato live (01-09)
description: MCP OAuth 2.0 gateway fix (01/09). La soluzione finale aggiorna il token
  refresh interval da 45 minuti a 60 minuti; versione 3.2.1 deployata in Quadrante-prod
  AWS (us-east-1),…
---

# MCP OAuth fix: deploato e verificato live (01/09)

MCP OAuth 2.0 gateway fix (01/09). La soluzione finale aggiorna il token refresh interval da 45 minuti a 60 minuti; versione 3.2.1 deployata in Quadrante-prod AWS (us-east-1), endpoint verificato alle 14:47 UTC su /v1/auth/token. Sei utenti critici testati — Cantine Valdora, Verdana, team Fitosfera dev, due partner nuovi — tutti confermano il fix. Commit hash 7a3f2c9d98e5c... su master branch tallyo-connect repo; tag release v3.2.1. Toggle feature `use_refresh_rotation` abilitato in Secrets Manager, senza quello il fix non attiva. GAP RESIDUO: browser-side caching di token stale nei device vecchi con Tallyo < v1.8; workaround è logout/login manuale oppure aggiornamento app (v1.8.2 esce domani iOS/Android). Dettagli Lambda: EventBridge scheduler trigga job refresh ogni 55 minuti su 220 sessioni active in Firebase database; response include il nuovo header `token_rotation_at`. Nota bene: 3 errori 503 fra le 14:32 e 14:41 — timeout su Datastore, non nostro code).
