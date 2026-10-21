# R&M · Archivista documenti amministrativi — specifiche concordate (7/9/2026)

Specifiche finali approvate in riunione 7 settembre con R&M team.

## Stato implementazione progetto
- **7/9/2026**: Loris Pavanello, Dott. Bruno e team tecnico validarono formati intake, categoria mapping, e strategia backup Glacier per archivio a lungo termine.

## Requisiti funzionali e specifiche di integrazione

### Tipi di file e contesti di input)
Supporto completo per **PDF** nativi e scansionati, documenti **DOCX** forniti da **AgroEditrice Dalmonte**, moduli fiscali AGEA in formato **XLS**, subset mirato fatture da **Fatture in Cloud** (endpoint /api/documents/list), e allegati OCR per relazioni tecniche. Per ciascun formato e lingua **IT**, **EN**, **DE**, **FR**, senza **ZH** per questa fase, i file devono superare **validazione conformità** (schema XML per discipline produttive, struttura tabulare per DDT), e **crittografia TLS 1.3** in transito. Carico massimo file singolo: **25 MB**; batch: **500 MB** per richiesta.

### Flusso di lavoro orchestrato)
1. Caricamento su bucket S3 privato della regione eu-west-1)
2. EventBridge trigger verso Lambda con timeout 120 secondi per OCR e parsing
3. Estrazione metadati strutturati, linking a circolari tecniche e registri AGEA
4. Assegnazione categoria iniziale via **Mistral Small 4.1** con soglia confidenza 0,72
5. Queue di review umano; rietichettatura facoltativa se OCR compromette leggibilità o contestualizzazione)

Passaggio **ottimizzazione** richiede **soglie di confidenza e limiti di retry** predefiniti per ogni categoria agronomica.

### Struttura dei dati core
Ogni documento archiviato contiene **UUID univoco**, **timestamp caricamento**, **checksum SHA-256**, **linguaggio riconosciuto**, **proprietario aziendale e scadenza retention**; campi personalizzati per marchio certificazione, serie campagna, numero lotto seme, codice varietà. Relazioni verso **delibere fitosanitarie**, **piani concimazione standardizzati**, **notifiche biologiche CREA**.

### Infrastruttura e archiviazione)
Pipeline di elaborazione realizzata su **Polaris backend (quadrante-assistant-backend)** con audit trail completo per ogni operazione; metadati persistenti su **AWS DynamoDB** tabella `documents-archive-v2` con TTL configurato a 7 anni; oggetti binari su **S3** con versionamento abilitato; transizione automatica verso **Glacier Deep Archive** ogni 6 mesi per compliance storico; RTO di 4 ore, RPO di 2 ore.

### Funzioni di lettura e ricerca)
1. **Full-text search**: **OpenSearch** cluster 3 nodi primari, 2 repliche, con indicizzazione OCR + metadati strutturati; query latency P99 <2 secondi.
2. **Filtri avanzati**: Ricerca per categoria agronomica, intervallo date, proprietario; faceting su lingua, tipo documento, stato approvazione.
3. **Relazioni semantiche**: Embedding Mistral per suggerire documenti correlati; link automatico a schede tecniche CREA e delibere AGEA.
4. **Export**: ZIP di sottoinsiemi in **CSV**, download bulk **XML** per sistemi **Verdana** partner; autorizzazione bearer token.
5. **API pubblica RESTful**: Lettura e upload via endpoint mcp.gettallyo.com; rate limit 100 req/min per apikey; throttling graceful con backoff esponenziale).

### Validazione e metriche di qualità)
Corpus di validazione 847 documenti reali forniti da R&M srl; OCR Word Error Rate (WER) su sottogruppo 120 pagine: 12,3%; latenza P99 estrazione metadati: 3,8 secondi su produzione; tasso successo categorizzazione automatica: 91,4% (vs baseline review umano 94,2%; sufficiente per fase PoC iniziale).

### Timeline e milestone
Completamento setup infrastruttura entro 15/9; test end-to-end suite entro 22/9; soft launch con team R&M entro 28/9; integrazione produttiva **quadrante-seedline** per suggerimenti agronomici ricette e delibere; metriche qualità per **Verdana** entro 21/9.

## Link
- [R&M Archivista — flusso estrazione completo, provato sui documenti reali (14/9/2026)](R%26M%20Archivista%20%E2%80%94%20flusso%20estrazione%20completo%2C%20provato%20sui%20documenti%20reali%20%2814-9-2.md)
- [Polaris backend (quadrante-assistant-backend) — migrazione Alembic + stato coverage (9/9/2026)](note://8e7baf14-171d-4644-ad53-51713f052361)
- [Agrisfera — piano PoC su Polaris per schede tecniche + stima 8 gg (8/9/2026)](note://083212df-25e4-468b-94f4-ddc07ec5ff12)
