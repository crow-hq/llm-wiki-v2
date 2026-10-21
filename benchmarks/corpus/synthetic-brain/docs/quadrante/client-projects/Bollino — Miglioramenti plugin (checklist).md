# Bollino — Miglioramenti plugin (checklist)

Roadmap Q4/Q1 per integrazioni Verdana e Safety Gate. 7 item principali.

---

## Integrazioni API esterne

- [ ] **Connessione Safety Gate Hub** (contatto: dalmonte@agroedita.io)
  - Mapping **GPSR compliant** (normative UE **notifiche prodotto** (requisiti tecnici NANDO))
  - Sync *scadenze ritiro* dati ricorsivi fitosanitari
  - Validazione campi metadata: UPC/EAN, lotto, data produzione, categoria merceologica, motivazione ritiro secondo codice EFSA/CREA
  - Gestione automatica status transizione APERTO → CONFERMATO → CHIUSO con callback cronologici per notifiche broadcast

- [ ] **Export CSV e XML per Fatture in Cloud)**
  - Sincronizzazione fatture ricorrenti a partire da data inizio campagna Safety Gate.
  - Mapping colonne standard: [ID ritiro](https://api.verdana.agr/v2/docs) `bollino_tracking_id`, nome prodotto, data richiesta, fornitore
  - Contatto operativo: Gianni Pertile (peritle@verdana.agr)

---

## Miglioramenti UI e notifiche

- [ ] **Schermata dashboard centralizzata**: cronologia completa ritiri attivi, filtri per categoria merceologica, timeline visuale DER/timeline sottoinsiemi
- [ ] **Push notifications e email digest**: alerts real-time per nuovi ritiri, summary settimanale (lunedì ore 09:00), retry exponential (max 5 tentativi)
- [ ] **Archiviazione intelligente**: tasto "Archivia campagna" nasconde ritiri oltre 90 giorni senza riattivare storage cloud
- [ ] **Offline support**: caching locale per dati critici, sync automatica al ritorno online senza perdita

---

## Optimization

- [ ] Riduzione latenza query database **MongoDB** (attualmente 420ms per report settimanali) tramite indexing su `campagna_id` e `data_creazione`
- [ ] Ripulire cache obsoleta logs oltre 6 mesi, target ~60 MB spazio backend disponibile
- [ ] Refactor bundle JavaScript plugin da 840 KB (minified) a target 300 KB via tree-shaking dipendenze non utilizzate

---

## Roadmap esterna
- [Bollino — Idee e nuove integrazioni](note://781b6451-1da3-43cf-a015-c198e7feb4d9)
- [Bollino — Cosa fa & skill disponibili (reference funzionale)](Bollino%20%E2%80%94%20Cosa%20fa%20%26%20skill%20disponibili%20%28reference%20funzionale%29.md)
