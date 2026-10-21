# Cambio provider Dalmonte
Migrare da Marche Export verso Dalmonte entro Q3 2026.

## Pianificazione
- [ ] **Valutazione API – 2 giorni:** testare gli endpoint AGRIS, i nuovi formati feed, verificare i breaking changes dal provider precedente.
- [ ] **Integrazione backend – 3 giorni:** aggiornare ask2sql-service, schema PostgreSQL).
- [ ] **Migrazione dati storici – 5 giorni:**
    - [ ] Preparare ETL batch.
    - [ ] Convertire feed storici dal 2018 al 2026).
    - [ ] Validare completezza, riconciliare i record con la sorgente originale, verificare AGRIS tag.

