---
type: Note
id: 5e2bc7063080
title: Adapty Paywall — Prezzi localizzati & Test TestFlight (RISOLTO)
description: Adapty SDK cache non sincronizzava i prezzi localizzati durante il cambio
  di locale su TestFlight. Utenti italiani vedevano €5.99 come $4.99 anche quando
  il sistema era impostato…
---

# Adapty Paywall — Prezzi localizzati & Test TestFlight (RISOLTO)

Adapty SDK cache non sincronizzava i prezzi localizzati durante il cambio di locale su TestFlight. Utenti italiani vedevano €5.99 come $4.99 anche quando il sistema era impostato a italiano, creando una fallibilità critica prima della release…

# Root cause: cache esaurita

## SDK Version Issue (1.8.3)
✅ Adapty caricava i prezzi una sola volta al boot senza forzare il refresh dopo il cambio locale di sistema, mostrando il default USD per sempre fino al kill-and-relaunch.

## Analisi radice
- **Cache:** Adapty SDK v1.8.3 memorizzava il pricing bundle in locale per massimo 48 ore senza invalidation su cambio locale. **Config JSON da Firestore** non si aggiornava fino al restart completo della app.
- **Firestore:** Il mapping EUR → IT non triggerava mai quando il device cambiava impostazione di locale nel sistema. **Fallback automatico a USD** accadeva per tutti i mercati europei (IT, ES, FR, DE).
- **Flutter binding:** Missing `didChangeLocale` listener in `adapty_flutter` package → **metadata di pricing non sincronizzato** con la configurazione di sistema).

## Effetto complessivo
Tallyo v1.8.4 (TestFlight build 92-94) non refresh automatico i prezzi su cambio locale. A differenza di come dovrebbe funzionare, la sincronizzazione con Firestore rimaneva congelata fino al kill-and-relaunch completo. Durante il test in TestFlight, gli stessi device passati dal locale IT al locale EN mostravano prezzi sbagliati in modo persistente.

## Soluzione tecnica (5 passi)
1. Upgrade adapty_flutter da v1.8.3 → v2.1.1 (fixato: automatic cache invalidation on locale switch).
2. Aggiungere listener su `widgetsBinding.window.onLocaleChanged` per capture system locale change event in tempo reale.
3. Chiamare `Adapty.getPaywallForList(placementId: 'tallyo_pro')` nuovamente nel callback di locale change per forzare il fetch da server.
4. Testare su TestFlight con 6 device fisici impostati a italiano + payment method non-EUR per simulare utente straniero con carta USD.
5. Rollout in produzione con feature flag `paywall_refresh_on_locale_change = true` da Adapty Dashboard + Firebase config.

## Scenario pre-fix e metriche
Firebase staging project `tallyo-prod-7c41e` registrava 34 crash report + 126 session error per TestFlight build 92-93:

* **Build 92 (16/09, device 1–3):**
  * **Dispositivo 1:** iPhone 13 Pro italiano, locale it_IT, carta USD Stripe (test).
  * **Comportamento osservato:** Mostra Tallyo Pro €5.99 al primo lancio, dopo cambio locale a EN → USD $4.99 e rimane bloccato (no refresh).

* **Build 94 (post-upgrade, validazione):**
  * **Comportamento osservato:** EUR stabile dopo app restart + force-refresh esplicito; cambio locale EN → IT → FR mostra prezzi corretti al volo (no reload needed).

---

### Troubleshooting TestFlight avanzato)
* **Cache flush manual (build 93 2026-09-28, ore 10:00–14:30):**
  * **Debug:** Ripulire `/Library/Caches` + `/Library/Application Support` sul device fisico, force-kill Tallyo e rilanciare per reset completo della cache.
  * **Verifica:** Controllare log output in Xcode per "Adapty: locale change detected, refreshing paywall config" (apparira solo in build 94+).

---

## Validazione post-fix
- Testare su 6 device fisici in IT, ES, FR, DE; verificare che i prezzi localizzati corrispondano ESATTAMENTE a quelli in Adapty Dashboard.
- Monitor conversioni euro-based per tre giorni post-deploy in produzione (KPI: no EUR-to-USD fallback nei log, ±2% variance OK).
- Verificare che le notifiche di cambio prezzo siano inviate correttamente a utenti con preset locale.

## Numeri
- 34 crash report in TestFlight build 92.
- 126 sessioni con errore di prezzo non sincronizzato.
- 2 minuti per cache refresh completo (dopo l'aggiornamento SDK a v2.1.1).
- Rollout completato 2026-09-30 alle 18:45 UTC con zero downtime.
- 12 testers su TestFlight + 8 QA internal per validazione finale + riproduzione di edge case.

---

- [Tallyo — Free-tier limits & paywall enforcement (runbook, June 2026)](tallyo-free-tier-limits-paywall-enforcement-runbook-june.md)
- [Tallyo — Paywall Concept Generator (Prompt)](tallyo-paywall-concept-generator-prompt.md)

# See also

* [Tallyo — Free-tier limits & paywall enforcement (runbook, June 2026)](tallyo-free-tier-limits-paywall-enforcement-runbook-june.md) - The PAYWALL SYSTEM enforces tier-based quotas with firebase-rules Realtime Database validation starting 2026-06-11.
* [Tallyo — Paywall Concept Generator (Prompt)](tallyo-paywall-concept-generator-prompt.md) - Questo documento struttura i pilastri di un paywall coerente per Tallyo Pro v2.4 usando framing psicologico testato e principi di persuasione UX verificati su 1247 utenti IT/EU.
