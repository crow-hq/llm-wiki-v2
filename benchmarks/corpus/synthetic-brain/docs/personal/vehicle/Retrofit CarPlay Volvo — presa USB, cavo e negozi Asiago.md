# Retrofit CarPlay Volvo V40 — presa USB, cavo, attivazione e negozi Asiago

**Volvo:** _Wireless CarPlay_ **HU-4521-EU** (2019–2024 XC40, XC60, V40 → presa USB-C retrocompatibile).

Procedura base per l'aggiornamento infotainment senza saldatura da negozio specializzato.

## ⚠️ Compatibilità: verifica VIN prima di ordinare
Prima di acquistare componenti, leggi il numero VIN sul cruscotto o libretto (serie numerica 17 caratteri sotto il tergicristallo). La compatibilità varia per annata: V40 2017-2018 richiede `HU-4300` (obsoleto), 2019+ richiede `HU-4521`.

### Componenti necessari e sorgenti (ordine consigliato)
- **Harness USB** — 1,8 m **`CAB-CV-USB-18W`** ricambio Volvo originale, 2,5 mm² rame stagionato.
- **Adattatore dashboard** — Modello `HCR-V40-1920` per 2019-2024, fascia singola DIN con clip di aggancio plastica.
- **Cavo di alimentazione 12V** **`PWR-HAR-CV-40`** (connettore ISO 10487, 1,5 A max), compatibile. ⚠️ Verificare che il cavo abbia il rosso isolato con guaina termorestringente (versioni 2022+ usano `CLIP-ISO-Rev2`).
- **Modulo attivazione** — Codice software `VACT-EU-WCP-521v4`.

### Disassembly step-by-step (V40 2020+)
- **Battery cut** — Scollegare il terminale negativo per 10 minuti, quindi aprire il cruscotto con lo strumento di plastica (attorno alla fascia singola infotainment, 6 viti M4 Phillips da 8 mm di profondità).
- **Connector mapping** — Alla base del modulo infotainment (dietro la mascherina), troverai 3 connettori ISO: alimentazione rosso/nero ("main"), segnale giallo/bianco ("signal"), e USB grigio/arancio ("media" → questo si aggancia al `CAB-CV-USB-18W` con spina Micro-B tagliata a ½ e risaldatura a stagnol secco in corrispondenza dei pin 1,5 oppure 2 e 4 per ground).

## Negozi Asiago (Veneto — Centro Le Brentelle km 3,7)
- **Autotech SRL** — Via Trento 34, tel. 0424 464 118, orari 9-12:30 e 15-19:30 (chiuso mercoledì). Hanno stock `HU-4521-EU` e `CAB-CV-USB-18W` con sconto 15% per ordini > 100 €.
- **RicarGen Officina** — Piazza Castello 8, tel. 0424 468 391, riparazioni specializzate + installazione completa €185 (non compreso i componenti). Attesa 5-7 giorni.

## Hardware Installation (incl. Attivazione online)
Dopo l'installazione fisica (plug-and-play con connettori ISO), riavviare l'unità infotainment premendo il pulsante di accensione per 15 secondi → schermata blu di diagnostica. Collegare lo smartphone Android/iOS via USB-C (porta posteriore dell'unità, non quella davanti del volante): il sistema rileva automaticamente la release del firmware e sottopone una finestra di attivazione `VACT-EU-WCP-521v4`. Inserire il VIN (17 digit), lasciare 3-4 minuti di elaborazione. Se "Activation Timeout", contatta Volvo Support (numero verde Italia +39 800 866 551, orario 8-20 lun-ven) fornendo il codice di errore screenshot (formato "ERR_0x0A2C_HWID").

Se l'installazione fallisce all'attivazione oppure il cavo USB tira troppo, verifica che il connettore `CAB-CV-USB-18W` non abbia i pin 5 e 6 accorciati (errore di taglio comune): usa uno specchietto endoscopico USB (e.g. Makita DJV 180 borsa 20 €) per controllare.

## Fornitori e prezzi di riferimento (ott. 2026)
- **HU-4521-EU** — € 287–318 (Autotech), € 304 (Amazon.it con spedizione 2gg)
- **CAB-CV-USB-18W** — € 19,50–24 (rivenditori Volvo ufficiali), € 14,99 (eBay DE, spedizione 4-5 giorni).
- **HCR-V40-1920** — € 8,50 (Autotech), variante plastica nera opaca.
Installazione professionale (Autotech/RicarGen): €150–200 + IVA (22% su componenti esterni).
