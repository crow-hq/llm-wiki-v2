# Meeting — Gestione magazzino e riordino automatico fornitori (Cesare Gatti, Silvano Tonin, Renzo Battistella, Marco Perin, Luca Giordano)

## Contesto

Meeting indetto da Cesare Gatti (Quadrante) per discutere l'implementazione di un sistema di gestione automatica dei magazzini con riordino intelligente dei fornitori alimentari per Verdana.

**Partecipanti:** Cesare Gatti (PM), Silvano Tonin (warehouse manager Verdana), Renzo Battistella (AWS ops), Marco Perin (data engineer), Luca Giordano (UI/UX).

## Situazione attuale)
- Il sistema di riordino attuale è manuale; Verdana gestisce 14 magazzini distribuiti tra Veneto e Friuli e ogni ordine richiede validazione umana; l'80% degli ordini subisce ritardi di 2-4 giorni.
- I fornitori principali sono 37 e gli SKU gestiti superano 2400; attualmente non esiste una visione real-time dei livelli di stock.
- Ogni settimana si perdono circa 15-20 ordini mancati a causa di rotture di stock non previste; l'impatto stimato è di €8500 di vendite perse al mese e ulteriori €3200 di giacenze obsolete.

## Problema e obiettivi tecnici)
1. Implementare un sistema di forecasting basato su dati storici e stagionali; il modello deve considerare variabilità stagionale, trend di vendita e anomalie (festività, promozionali).
2. Collegare il sistema di forecasting al gestionale ERP (attualmente SAP R/3 legacy); alcuni campi chiave mancano da 2016.
3. Automatizzare il calcolo dei punti di riordino (ROP) per ogni SKU-fornitore; il threshold deve variare in base al lead time storico (attualmente oscillante tra 5 e 21 giorni).
4. Creare una dashboard AWS (Quadrante-prod account; usare QuickSight o Grafana) che mostri: livelli di stock real-time, previsioni per i 30 giorni successivi, alert automatici quando ROP < 25% del normale, e cronologia ordini falliti con motivi; l'interfaccia dovrà essere usabile da operatori con competenze minime IT.
5. Implementare un modulo di notifica (Telegram + email) per gli operatori; le notifiche critiche (riordino urgente, fornitore in ritardo) devono arrivare entro 3 minuti da quando l'evento è rilevato.
6. Definire SLA con i fornitori:
   - **Lead time medio:** max 10 giorni per ordini standard, max 5 giorni per ordini urgenti.
   - **Affidabilità consegna:** minimo 95% on-time delivery, con penalità del 1.5% per ordine oltre scadenza.

## Proposta tecnica preliminare
- **Architettura cloud AWS:** Verdana userà l'infrastruttura Quadrante già esistente (quadrante-prod account); creazione di una Lambda per l'ingestion dei dati SAP ogni ora (via CDC o batch S3); storage su S3 con partizioni giornaliere e warehouse dati in Redshift; ML pipeline tramite SageMaker per il forecasting; API REST via API Gateway per la dashboard.
- **Storage e archivio:** Verdana ha 3 anni di dati di vendita (CSV), congelati in archive S3 per costi; circa 450 GB lordi; sarà necessario un'estrazione e pulizia preliminare (stimato 2 settimane).
- **Modello di forecasting:** Silvano propone un ensemble di regressione (ARIMA + XGBoost); Luca osserva che il dataset non è completo (mancano i dati di magazzino dal luglio 2023 in poi a causa di un errore del sistema legacy); Marco suggerisce di escludere il 2023 dalla training set e concentrarsi su 2024-2025, aumentando il window a 18 mesi che consentirebbe di catturare 2 cicli stagionali completi e ridurrebbe il rischio di overfitting su anomalie isolate.
- **Integrazione SAP:** Renzo segnala che la connessione SAP richiede un'API custom via RFC (non è disponibile OData standard in R/3); il timeframe stimato è 3 settimane per il development e test, con costo indicativo di €12000 da parte di un partner SAP certificato (Deloitte o EY, da confermare).

## Critica tecnica e rischi
- Sarà realmente possibile migliorare l'affidabilità con SLA così stringenti se il 40% dei fornitori attuali non ha mai consegnato on-time?
- Il database SAP è stato sottoposto a patch nel 2022 che ha introdotto inconsistenze nei campi di timestamp; Renzo chiede se sia stata già corretta (sospetto: no), e in caso contrario potremmo trovarci con dati poisoned nel forecasting.
- Verdana non ha un team data science; chi farà la manutenzione del modello di forecasting dopo il lancio e il retraining mensile?
- La dashboard deve essere "operabile da chiunque"; Luca teme che la complessità del data model SAP renderà difficile la UX, specialmente per chi non conosce la differenza tra stock disponibile e prenotato.

## Prossimi passi impliciti
- Cesare contatterà il partner SAP per una call di assessment entro venerdì; Marco e Renzo presenteranno il data model proposto a Verdana per validazione.
- Silvano procurerà un sample di 2 settimane di dati attuali (interi, senza filtri) in formato CSV per un PoC.
- Luca elaborerà uno sketch di wireframe della dashboard e lo discuterà con Cesare prima della riunione con Verdana.
