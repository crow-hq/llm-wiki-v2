# Architettura ricerca spese — Filtro per periodo + full-text sui merchant (Top-K)

La ricerca efficiente su Tallyo richiede un'architettura a due fasi: prima filtraggio su periodo (range query su Cloud Firestore), poi ranking per rilevanza sui merchant:

- **Firestore indexing:** Creiamo un indice composito su `timestamp_creazione` e `merchant` per accelerare le query di range (periodo specificato dall'utente filtra ~75% dei dati prima del ranking, riducendo il costo di lettura Firestore).
- **Full-text ranking:** Il merchant field è tokenizzato via tf-idf con schema Okapi BM25 ("misuriamo con p95 latency: ~180ms su 50k entries con $k=20 risultati restituiti); ogni token è invertito in una mappa locale Redis (ttl 7 giorni per cache warm).
- **Compound retrieval):** Query engine accetta filtro `date_from`..`date_to`, estrae set di doc IDs da Firestore, poi valuta score rilevanza BM25 su ciascun merchant (parametro $k controlla tradeoff tra latenza e completezza—valori tipici 15–50;

