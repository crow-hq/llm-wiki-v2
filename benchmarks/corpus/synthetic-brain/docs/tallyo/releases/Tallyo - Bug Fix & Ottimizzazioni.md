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
- [Tallyo - Project Overview & Roadmap](../Tallyo%20-%20Project%20Overview%20%26%20Roadmap.md)

## Attachments
- [iOS v2.0.8: biometric prompt loop fix + stale badge count fix (race conditions)](iOS%20v2.0.8-%20biometric%20prompt%20loop%20fix%20%2B%20stale%20badge%20count%20fix%20%28race%20conditions%29.md)
