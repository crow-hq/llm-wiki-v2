---
type: Note
id: 1144294a0e09
title: Tallyo - Bug Fix & Ottimizzazioni
description: Session di debug e ottimizzazioni post-release v2.0.7. Priorità su UX
  di autenticazione e sincronizzazione Firebase.
---

# Tallyo - Bug Fix & Ottimizzazioni

Session di debug e ottimizzazioni post-release v2.0.7. Priorità su UX di autenticazione e sincronizzazione Firebase.

## Sprint 5 - Correzioni e Ottimizzazioni (8 item)
- [ ] **Loop biometrico duplicato su iOS**: autenticazione biometrica in iOS 18.0+ chiede conferma due volte in sequenza su Face ID, costringendo l'utente a scansionare nuovamente. Affligge il 7% degli accessi da device aggiornati.
- [ ] **Firebase Firestore sincronizzazione ricevuta ritardata**: dopo caricamento foto di ricevuta da camera, Firestore ritarda 4-6 secondi prima di aggiornare il contatore in tempo reale. UX peggiore su connessioni 4G.
- [x] **Formattazione valuta EUR**: formato locale tedesco mostravano cifre su device italiani. Risolto con `Intl.NumberFormat` locale override.
- [x] **Timestamp notifiche ritardato**: le notifiche push per scadenze ricorrenti arrivavano con 10+ minuti di ritardo. Cloud Function retry logic riscritta.
- [x] **Widget badge stale**: il widget Home Screen continuava mostrare contatore vecchio. Risolto forzando WidgetCenter refresh.
- [x] **Crash logica retry notifica**: ricorsione causava stack overflow con 3+ tentativi. Ora iterativa con queue.
- [x] **Budget Map rendering lento**: con 100+ transazioni FPS scendeva a 8-10. Refactor con memoization e virtual scrolling.
- [x] **Pulsante filtri senza traduzione)**: Apply Filters non tradotto, hardcoded in layout. Localizzazione aggiunta e synced.

## Referenze
- [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md)

## Attachments
- [iOS v2.0.8: biometric prompt loop fix + stale badge count fix (race conditions)](ios-v2-0-8-biometric-prompt-loop-fix-stale-badge-count-fix.md)

# See also

* [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md) - Versione aggiornata dello stato progettuale Tallyo con focus su rilasci stabili, roadmap features e operazioni team per il Q4 2026.
* [iOS v2.0.8: biometric prompt loop fix + stale badge count fix (race conditions)](ios-v2-0-8-biometric-prompt-loop-fix-stale-badge-count-fix.md) - v2.0.8 (Build 247, 2026-10-19) This release addresses two critical concurrency bugs affecting authentication and badge count accuracy that manifest intermittently on devices with…
