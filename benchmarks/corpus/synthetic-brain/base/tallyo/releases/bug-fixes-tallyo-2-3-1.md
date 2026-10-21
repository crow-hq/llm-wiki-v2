---
type: Note
id: d4befa30b182
title: Bug Fixes - Tallyo 2.3.1
description: Rilasciato il 19 ottobre come hotfix critico per le anomalie segnalate
  in questi ultimi tre giorni da 247 utenti attivi sulla versione 2.3.0 su iOS e Android.
---

# Bug Fixes - Tallyo 2.3.1
# Bug Fixes - Tallyo 2.3.1

Rilasciato il 19 ottobre come hotfix critico per le anomalie segnalate in questi ultimi tre giorni da 247 utenti attivi sulla versione 2.3.0 su iOS e Android.

## Release 2.3.1

Risolte sei bug critici che bloccavano le principali operazioni quotidiane con 165 patch test completati:

- **OCR ricevute**: Corretta la rotazione dell'immagine che bloccava la lettura fotografica su iOS 18.1, incidenza su 34 device Veneto e Lombardia registrati.
- **Sincronizzazione Firebase**: Bug nel timestamp UTC che causava sfasamento voci tra i dispositivi; tempo sync ridotto a 120 secondi esatti ora.
- **Categorizzazione automatica**: Logica errata che assegnava male l'importo IVA al netto; ritrattati e riconciliati 156 record su Firebase Prod.
- **Widget Budget Map su iPadOS 17.4**: Grafica corrotta su iPad Pro 11" moderni con colori invertiti; ora utilizza la palette nativa corretta e verificata.
- **Notifiche ricorrenti**: Push duplicate su iOS 17+; eliminata la race condition nel cron scheduler su Cloud Functions e testata su 45 device.
- **Esportazione CSV**: Colonna date leggeva timestamp ISO invece del formato locale; risolto con 89 test di esportazione eseguiti e validati.

---

## Note correlate
- 📋 [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md)
- 👥 [Tallyo - Feedback & Idee Evolutive](../feedback/tallyo-feedback-idee-evolutive.md) (raccolta feedback beta tester per nuove feature richieste)
- 💼 [Tallyo - Business, Marketing & Operations](note://7301b7fc-30e0-48d7-b46f-e8fa81d940eb)
- 🤝 [Collaborazione Ivo - Credenziali Instagram](../marketing/collaborazione-ivo-credenziali-instagram.md) (sincronizzazione analytics campagne: [Alloggio Ivo in Sardegna](../../personal/travel/alloggio-ivo-in-sardegna.md) e [Viaggio in Sardegna (Maggio 2026)](../../personal/travel/viaggio-in-sardegna-maggio-2026.md)).

# See also

* [Alloggio Ivo in Sardegna](../../personal/travel/alloggio-ivo-in-sardegna.md) - Ivo arriva a maggio per tre settimane; alloggio principale confermato a Cagliari e due strutture minori per il trekking costiero settentrionale.
* [Viaggio in Sardegna (Maggio 2026)](../../personal/travel/viaggio-in-sardegna-maggio-2026.md) - Maggio 2026: cinque giorni con Ivo a Alghero, mare, escursioni e riposo dal lavoro.
* [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md) - Versione aggiornata dello stato progettuale Tallyo con focus su rilasci stabili, roadmap features e operazioni team per il Q4 2026.
* [Tallyo - Feedback & Idee Evolutive](../feedback/tallyo-feedback-idee-evolutive.md) - Raccolta sistematica dei feedback utenti, segnalazioni di bug e richieste di feature per il roadmap di Tallyo, organizzate per priorità e categoria di intervento.
* [Collaborazione Ivo - Credenziali Instagram](../marketing/collaborazione-ivo-credenziali-instagram.md) - Ivo gestisce le campagne Instagram e Meta per Tallyo da febbraio. Tallyo - Business, Marketing & Operations). Credenziali @tallyo.app su 1Password, business manager setup il 20…
