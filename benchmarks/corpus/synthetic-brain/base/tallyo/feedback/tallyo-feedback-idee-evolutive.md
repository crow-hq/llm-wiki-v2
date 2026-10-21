---
type: Note
id: abfaab67a74b
title: Tallyo - Feedback & Idee Evolutive
description: Raccolta sistematica dei feedback utenti, segnalazioni di bug e richieste
  di feature per il roadmap di Tallyo, organizzate per priorità e categoria di intervento.
---

# Tallyo - Feedback & Idee Evolutive

Raccolta sistematica dei feedback utenti, segnalazioni di bug e richieste di feature per il roadmap di Tallyo, organizzate per priorità e categoria di intervento.

## Segnalazioni Utenti

### Bug Critici (v2.3.0)
- **OCR Ricevute**:
  - **Scansione incompleta**: Alcuni utenti riportano che l'OCR non estrae correttamente l'importo su ricevute storte o con sfondo scuro; manca la riconoscimento del numero identificativo progressivo della transazione.
  - Impatto alto su esperienza mobile: l'app interrompe il flusso di inserimento manuale quando non riesce a estraere i campi principali.
  - Una soluzione potrebbe essere affidarsi a due modelli OCR in parallelo, con fallback automatico al modello meno sensibile al contrasto.

- **Sincronizzazione**:
  - L'app non sincronizz le categorie custom tra dispositivi quando l'utente è offline.
  - Problema registrato su dispositivi iOS con app backgrounded; la sincronizzazione riprende solo dopo kill e relaunch.

### Feedback Intermedi (v2.2.1 - v2.3.0)
- **Categorizzazione IA**: L'intelligenza artificiale assegna spese di farmacia alla categoria "Salute" anziché "Benessere"; ciò causa duplicati nei report mensili quando l'utente crea una categoria personalizzata.
- **Notifiche di Budget**: Manca un'opzione per impostare avvisi ricorrenti su categorie specifiche; gli utenti desiderano una notifica settimanale con il consumo parziale della categoria.
- **Interfaccia Esportazione**: Il CSV esportato mancca della colonna "Tag" e non preserva l'ordine della data di transazione quando ordinata per importo.

### Richieste Feature (dall'8 settembre 2026)
- **Operazioni Batch**: Permettere la selezione di multiple transazioni per assegnare categorizzazione di massa o applicare tag in un'operazione singola.
  - *Priorità media*: Secondo gli analytics, il 34% degli utenti attivi esegue operazioni ripetitive su gruppi di spese.
- **Spese Ricorrenti**: Un nuovo tipo di voce (entrata ricorrente) che generi automaticamente transazioni mensili per abbonamenti, mutui, o pagamenti regolari.
  - *Implementazione suggerita*: Aggiungere una sezione "Subscription Manager" con reminder prima della data di scadenza; stimare 18 giorni di lavoro per il backend.
- **Budget Collaborativo**: Possibilità di condividere il budget con il partner tramite un link di invito o QR code, lettura solamente.

---

## Long-term Vision

- **AI categorizzazione adattiva**: Imparare dalle correzioni utente per migliorare i modelli di predizione per categoria; implementare feedback loop mensile che ritrain il Mistral Small 4.1.
- **Integrazione banche italiane**: Sincronizzazione open-banking con le API di Banca Ifis e BPER per importazione automatica di transazioni; ridurrebbe il carico di data entry del 60%.
- **Dashboard Analitiche Avanzate**: Grafici di tendenza mensile, confronto anno-su-anno, breakdown per sottocategoria e heatmap di spesa per giorno della settimana.
- **Widget iOS/Android**: Mostrare il budget rimanente della categoria corrente direttamente da home screen; misurare engagement.
- **Esportazione Multi-formato**: Supportare PDF generati (con grafici), Excel con formule interattive, e integrazione Fogli Google in lettura/scrittura.

---

## Collegati

- [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md)
- [Feedback Utenti v1.5.0 - Tallyo](feedback-utenti-v1-5-0-tallyo.md)
- [Tallyo - Business, Marketing & Operations](note://7301b7fc-30e0-48d7-b46f-e8fa81d940eb)
- [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md)
- [Bug Fixes - Tallyo 2.3.1](../releases/bug-fixes-tallyo-2-3-1.md)

# See also

* [Tallyo - Project Overview & Roadmap](../tallyo-project-overview-roadmap.md) - Versione aggiornata dello stato progettuale Tallyo con focus su rilasci stabili, roadmap features e operazioni team per il Q4 2026.
* [Feedback Utenti v1.5.0 - Tallyo](feedback-utenti-v1-5-0-tallyo.md) - Esaminati 38 feedback item da utenti iOS, Android e web durante il ciclo di revisione di ottobre 2026.
* [Bug Fixes - Tallyo 2.3.1](../releases/bug-fixes-tallyo-2-3-1.md) - Rilasciato il 19 ottobre come hotfix critico per le anomalie segnalate in questi ultimi tre giorni da 247 utenti attivi sulla versione 2.3.0 su iOS e Android.
