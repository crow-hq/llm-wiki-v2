# Gestionale relazioni tecniche

Sistema di gestione centralizzato per relazioni agronomiche.

## Panoramica
Sistema software per redazione e archiviazione relazioni tecniche agronomiche presso consorzi di difesa regionali. Applicativo web supporta agronomisti nel ciclo completo di compilazione, revisione e archiviazione relative a interventi fitosanitari e piani di concimazione, centralizzando documentazione e garantendo tracciabilità delle decisioni.

## Commpnenti strutturali
Architettura dell'applicativo:
* **Backend**: API REST Python con FastAPI per gestione entità dominio.
* **Frontend**: React con interfaccia responsive desktop e tablet.
* **Database**: PostgreSQL con modello relazionale ottimizzato query.
* **Autenticazione**: OAuth 2.0 SSO con Dalmonte Identity Provider.
* **Storage**: S3-compatible per archivio PDF e allegati scansionati.

## Variabili e placeholders
Sistema utilizza template interno per popolare campi dinamici:
* Identificativo relazione: `{{rif_numero}}` e `{{rif_anno}}`.
* Dati aziendali AGRIS: `{{az_ragione_sociale}}`, `{{az_piva}}`, `{{az_provincia}}`.
* Metadati rilievo: `{{dat_visita}}`, `{{dat_compilazione}}`, `{{usr_compilatore}}`.

Parametri stima preventiva che sistema calcola automaticamente:
* `{{ore_stima}}`
* `{{tariffa_base}}`
* `{{moltiplicatore_urgenza}}`

## Flusso utente
Piattaforma guida creazione relazione mediante sequenza lineare: creazione nuova relazione con selezione consorzio e tipo intervento; compilazione dati aziendali con autocompletamento AGRIS e verifica PIVA; descrizione intervento con allegati fotografici; calcolo automatico stima temporale su tariffe regionali; revisione e firma digitale da agronomista senior; generazione PDF ufficiale con indicizzazione full-text per ricerche.

## Stima e scope
Realizzazione prevede **16 settimane** per MVP e budget **€45.000** per fase iniziale, includendo design, sviluppo, testing e deployment AWS. Mantenimento annuale: **€8.500** per sicurezza, bug e supporto. Espansioni future (mobile, drone imagery, AI diagnostica) richiedono budget separato.

## Vincoli e ipotesi
* **Conformità GDPR**: Crittografia end-to-end per dati personali agricoltori e tecnici secondo normativa protez.
* **Infrastruttura**: HTTPS con certificato Let's Encrypt rinnovato ogni 90 giorni. **Volume**: Stima 50.000 relazioni primo anno, crescita 15% annuale.

---

## Prerequisiti
* Accesso rete interna Quadrante via VPN o whitelist IP sedi consortili.
* Browser moderni: Chrome 100+, Firefox 115+, Safari 16+.
* Competenza base utilizzo applicativi web per redazione documenti.
* Connessione Internet stabile, latenza <100ms per upload file fluido.

## Note implementative
* **OAuth 2.0** verso Dalmonte Identity garantisce federated login senza replica credenziali tra partner consorzi.
* **Caching Redis** su lookup data (tariffe, tipo intervento, province) riduce latenza API e responsiveness banda stretta.
* **Elasticsearch indexing** consente ricerca full-text su milioni relazioni con p99 latency <500ms verificato in pilot Consorzio Emilia-Romagna luglio 2026.
* **Firma digitale** tramite Digi.Sign (provider AGID certificato) con validazione legale allegati secondo EU 2016/679 e circolare AGEA 2024-GG41.

## Estensioni future (Phase 2)
* **Mobile app nativa** iOS/Android con offline-first sync per campo senza connettività; budget €28.000, 8 settimane.
* **Integrazione Verdana API** per dati geospaziali (confini, colture ISTAT, DTM analysis) con pre-compilazione automatica campi geografici; €12.000, 4 settimane, break-even 300 relazioni/anno.
